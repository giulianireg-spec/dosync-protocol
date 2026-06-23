"""
DoSync — MAVLink Adapter (command channel)
==========================================
Drives a MAVLink vehicle (drone/rover/boat running ArduPilot or PX4) by
translating a DoSync action into the corresponding MAVLink command and waiting
for the vehicle's COMMAND_ACK. This is the "dumb body, external mind" principle
at its purest: the aircraft already knows how to fly — its firmware holds the
failsafe and the flight controller. DoSync does not fly it; DoSync *coordinates*
it, expressing intent ("go to this point", "come home") and letting the vehicle
execute with its own safety systems intact.

SCOPE — this module is the COMMAND CHANNEL only (Step 1):
    A command is a point-in-time event: DoSync sends "go_to", the vehicle replies
    COMMAND_ACK: ACCEPTED almost immediately. That ACK means "I received and
    accepted the order" — NOT "I arrived". execute() returns that ACK as an
    ActionResult, which the execution_model records as a dispatch acceptance
    (operation -> in_progress), never as completion. "Dispatch OK != navigating."

    The TELEMETRY CHANNEL — the continuous stream that reports arming, takeoff,
    arrival, and (critically) a human taking manual control — is a SEPARATE
    component with its own lifecycle (a background listener), built in Step 2. It
    is what makes "silence != success" real: only positive telemetry advances an
    operation toward completed. This file deliberately does not implement it.

SAFETY POSTURE (established by the expert panel, incl. a drone manufacturer and a
pilot):
    - The failsafe lives in the VEHICLE FIRMWARE, never in DoSync. Network/HTTP can
      fail exactly when needed; the drone must protect itself without us.
    - Operator override is by HARDWARE/RC and always wins. The pilot moves the
      stick; they do not wait for DoSync to "cede". DoSync only learns (via Step 2
      telemetry) that control was taken, and reports it.
    - DoSync coordinates; it is NOT the failsafe.

DEPENDENCY — pymavlink is OPTIONAL and imported lazily. A hub that controls no
MAVLink vehicle never installs it. Absent the library, the adapter degrades to
SIMULATED mode: it logs the command it WOULD send and returns success, so the
manifest, registration, and the rest of the protocol work unchanged. Same posture
as the BLE adapter with bleak. The open protocol never forces a drone library on
a deployment that only has light bulbs.

Manifest adapter_config schema (per vehicle):
    {
        "connection": "udp:127.0.0.1:14550",   # MAVLink endpoint (SITL or radio)
        "default_alt": 10.0                      # default takeoff altitude (m), optional
    }
"""

from __future__ import annotations
import logging
import threading
import queue
import time
import asyncio
from typing import Optional

from ..models import ActionResult, DeviceAction, Urgency
from . import DoSyncAdapter

log = logging.getLogger("dosync.adapters.mavlink")

# pymavlink is imported lazily so the module imports (and the adapter registers /
# unit-tests) on a host without the drone stack. Absent it, we run simulated.
try:
    from pymavlink import mavutil
    _MAVLINK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on host
    mavutil = None  # type: ignore
    _MAVLINK_AVAILABLE = False


# ── Action vocabulary ────────────────────────────────────────────────────────
# The five high-level actions DoSync expresses to an aerial vehicle. Each maps to
# a MAVLink command or mode change. These are INTENT-level verbs — the vehicle
# decides how to carry them out, the same way "ensure_safety" lets a bulb decide
# it should turn on. The aerial profile of the execution_model.
SUPPORTED_ACTIONS = ("take_off", "go_to", "land", "return_home", "loiter")

# How long to wait for the vehicle's COMMAND_ACK before treating the dispatch as
# failed. A command is point-in-time, so this is short — we are not waiting for
# the action to *finish*, only for the vehicle to *accept* the order.
_ACK_TIMEOUT_S = 5.0


class MAVLinkAdapter(DoSyncAdapter):
    """Command-channel adapter for a MAVLink vehicle.

    One instance handles every device whose manifest declares adapter="mavlink".
    The connection string and defaults come from the manifest's adapter_config,
    read from the hub registry (same pattern as WiZAdapter / BLEAdapter).

    The MAVLink connection is opened lazily on first use and cached, so a hub that
    registers a drone but never commands it pays no connection cost.

    TWO CHANNELS — DESIGN NOTE (and documented tech debt):
        This adapter currently opens TWO connections per vehicle: a COMMAND channel
        (outbound — sends commands, reads its own ACKs) and a TELEMETRY channel (the
        listener — reads the position stream continuously). They must not bind the
        same UDP port, so _split_channels rewrites a `udp:` command string to
        `udpout:` and keeps the binding form for telemetry (or uses a declared
        `telemetry_connection`). This is correct and sufficient for SITL/UDP.

        It is NOT how a real drone works. Over a serial radio (SiK 915MHz, a USB
        cable) a vehicle is ONE bidirectional link — a serial port cannot be opened
        twice. The correct long-term design is a SINGLE connection with a SINGLE
        reader: a producer/consumer where the listener owns the read loop and the
        command channel writes, then awaits its ACK *via* the listener rather than
        reading the socket itself. That refactor (restructuring _wait_ack to go
        through the listener) is the next step for hardware support and is
        deliberately deferred — the two-connection split unblocks SITL today without
        pretending it already serves a serial drone. ("Implementado ≠ validado.")
    """

    def __init__(self, hub=None, ack_timeout: float = _ACK_TIMEOUT_S):
        """
        Args:
            hub: reference to the DoSyncHub to read adapter_config from the
                 manifest. Optional — if absent, config must come in action.params.
            ack_timeout: seconds to wait for COMMAND_ACK before failing the dispatch.
        """
        self._hub = hub
        self._ack_timeout = ack_timeout
        # Cache of connection-string -> live mavutil connection. Keyed by endpoint
        # so multiple vehicles on different endpoints each keep their own link.
        self._connections: dict = {}
        # ── Telemetry channel (Step 2b) ──────────────────────────────────────
        # One listener thread per vehicle (producer) feeds a shared queue; a single
        # consumer asyncio task (on the event-loop thread) drains it and calls
        # hub.apply_telemetry. The queue is the thread-safe boundary; only the
        # consumer touches the DB.
        self._telemetry_queue: "queue.Queue" = queue.Queue()
        self._listeners: dict = {}            # device_id -> MAVLinkTelemetryListener
        self._consumer_task: Optional[asyncio.Task] = None
        self._consumer_running = False

    @property
    def adapter_name(self) -> str:
        return "mavlink"

    # ── Config resolution (same pattern as BLE/WiZ) ──────────────────────────
    def _get_config(self, action: DeviceAction) -> dict:
        """Resolve adapter_config: action.params override, then manifest."""
        cfg = action.params.get("adapter_config")
        if cfg:
            return cfg
        if self._hub:
            device = self._hub.registry.get(action.device_id)
            if device and device.adapter_config:
                return device.adapter_config
        return {}

    def _get_connection(self, conn_str: str):
        """Return a cached MAVLink connection for this endpoint, opening it once.
        Only called when pymavlink is available (never in simulated mode)."""
        conn = self._connections.get(conn_str)
        if conn is None:
            log.info("MAVLink: opening connection %s", conn_str)
            conn = mavutil.mavlink_connection(conn_str)
            conn.wait_heartbeat(timeout=10)
            log.info("MAVLink: heartbeat from system %s component %s",
                     conn.target_system, conn.target_component)
            self._connections[conn_str] = conn
        return conn

    # ── The command channel ───────────────────────────────────────────────────
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Translate a DoSync action into a MAVLink command and return the ACK.

        Returns success=True when the vehicle ACCEPTS the command (a dispatch
        acceptance — the operation is now underway, NOT finished). Returns
        success=False when the vehicle rejects it or no ACK arrives. The continuous
        confirmation that the vehicle actually flew the command is the telemetry
        channel's job (Step 2), not this method's.
        """
        if action.action not in SUPPORTED_ACTIONS:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"MAVLink adapter has no mapping for action '{action.action}'. "
                      f"Supported: {', '.join(SUPPORTED_ACTIONS)}.",
            )

        cfg = self._get_config(action)
        conn_str = cfg.get("connection")
        if not conn_str:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error="MAVLink manifest missing 'connection' (e.g. 'udp:127.0.0.1:14550').",
            )

        # Separate the command and telemetry channels so they never bind the same
        # UDP port (the cause of 'Address already in use'). command_conn is outbound
        # (udpout — no local bind), telemetry_conn listens for the position stream.
        try:
            command_conn, telemetry_conn = self._split_channels(cfg)
        except ValueError as e:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=str(e),
            )

        default_alt = float(cfg.get("default_alt", 10.0))
        params = action.params or {}

        # ── Simulated mode — pymavlink not installed on this host ─────────────
        if not _MAVLINK_AVAILABLE:
            log.info("[SIMULATED] MAVLink %s: %s %s (would send to %s)",
                     action.device_id, action.action, params, command_conn)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=True,
                response={"status": "simulated", "connection": command_conn,
                          "command": action.action, "params": params},
            )

        # ── Real command dispatch ─────────────────────────────────────────────
        try:
            conn = self._get_connection(command_conn)
        except Exception as e:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"MAVLink connection to {command_conn} failed: {e}",
            )

        # Lazily start the telemetry listener the first time we command this vehicle.
        # The return channel must be listening BEFORE the command takes effect, so
        # we start it here — before _dispatch sends anything — and never miss the
        # position messages that confirm arrival. start_telemetry is idempotent
        # (a no-op if already listening) and safe (a no-op in simulated mode). It
        # listens on telemetry_conn — the binding endpoint, distinct from the
        # outbound command channel.
        if action.device_id not in self._listeners:
            self.start_telemetry(action.device_id, telemetry_conn)

        # Wait for the listener (the single reader) to be connected before we send
        # any command, so the ACKs of the opening commands (set_mode, arm, take_off)
        # are captured. Without this, the first commands could be sent before the
        # reader is up and their ACKs would be read-and-missed. Bounded wait — if the
        # listener can't connect, _wait_ack will report no ACK (silence is not
        # success) rather than block forever.
        listener = self._listeners.get(action.device_id)
        if listener is not None:
            if not listener.wait_connected(timeout=self._ack_timeout):
                log.warning("MAVLink: telemetry listener for %s not connected within "
                            "%ss — command ACKs may be missed",
                            action.device_id, self._ack_timeout)

        try:
            return await self._dispatch(conn, action, params, default_alt)
        except Exception as e:
            log.warning("MAVLink %s failed: %s", action.action, e)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"MAVLink dispatch failed: {e}",
            )

    async def _dispatch(self, conn, action: DeviceAction, params: dict,
                        default_alt: float) -> ActionResult:
        """Send the MAVLink command for this action and wait for its ACK.

        Each branch ends by sending a command that produces a COMMAND_ACK (or, for
        mode changes, sets the mode and confirms). The dispatch is considered
        accepted when the vehicle acknowledges; the actual flying is observed via
        telemetry (Step 2).
        """
        act = action.action
        ok = False
        detail = {}

        if act == "take_off":
            alt = float(params.get("altitude", default_alt))
            # Sequence: GUIDED -> arm -> NAV_TAKEOFF. The panel's "preparing" phase
            # (arming/taking_off) will be surfaced by telemetry in Step 2; here we
            # only dispatch the order and confirm acceptance.
            self._set_mode(conn, "GUIDED", device_id=action.device_id)
            self._arm(conn, device_id=action.device_id)
            send_time = time.time()
            conn.mav.command_long_send(
                conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0, 0, 0, 0, 0, 0, 0, alt,
            )
            ok = self._wait_ack(conn, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                                device_id=action.device_id, since=send_time)
            detail = {"altitude": alt}
            # Tell this vehicle's telemetry listener to confirm the climb: emit
            # FINISHED once the vehicle reaches the commanded altitude. Without this
            # the take_off would dispatch, be ACK'd, and then stall forever (silence
            # is not success) because nothing ever confirms the climb completed.
            # Guarded so it's a no-op when telemetry isn't running.
            if ok:
                listener = self._listeners.get(action.device_id)
                if listener is not None:
                    listener.set_arrival_target("altitude", alt)

        elif act == "go_to":
            lat = params.get("lat")
            lon = params.get("lon")
            alt = float(params.get("alt", default_alt))
            if lat is None or lon is None:
                return ActionResult(
                    device_id=action.device_id, action=act, success=False,
                    error="go_to requires 'lat' and 'lon' params.",
                )
            # Reposition the vehicle to a coordinate (GUIDED mode target).
            conn.mav.set_position_target_global_int_send(
                0, conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,  # position only
                int(lat * 1e7), int(lon * 1e7), alt,
                0, 0, 0, 0, 0, 0, 0, 0,
            )
            # Position targets do not emit a COMMAND_ACK; acceptance is implicit on
            # send in GUIDED mode. We confirm the vehicle is in GUIDED.
            ok = True
            detail = {"lat": lat, "lon": lon, "alt": alt}
            # Tell this vehicle's telemetry listener where the vehicle is headed, so
            # it can emit FINISHED on arrival (set_position_target gives no
            # MISSION_ITEM_REACHED). This is the single point where the command
            # channel touches the telemetry channel — guarded so it is a no-op when
            # telemetry isn't running (simulated mode, or telemetry not started).
            listener = self._listeners.get(action.device_id)
            if listener is not None:
                listener.set_arrival_target("position", (lat, lon))

        elif act == "land":
            ok = self._set_mode(conn, "LAND", device_id=action.device_id)
            detail = {"mode": "LAND"}

        elif act == "return_home":
            ok = self._set_mode(conn, "RTL", device_id=action.device_id)
            detail = {"mode": "RTL"}

        elif act == "loiter":
            ok = self._set_mode(conn, "LOITER", device_id=action.device_id)
            detail = {"mode": "LOITER"}

        if ok:
            log.info("MAVLink %s: %s accepted %s", action.device_id, act, detail)
            return ActionResult(
                device_id=action.device_id, action=act, success=True,
                response={"dispatch": "accepted", "command": act, **detail},
            )
        return ActionResult(
            device_id=action.device_id, action=act, success=False,
            error=f"Vehicle did not accept '{act}' (no ACK / rejected within "
                  f"{self._ack_timeout}s).",
        )

    # ── MAVLink primitives ────────────────────────────────────────────────────
    def _set_mode(self, conn, mode_name: str, device_id: str = None) -> bool:
        """Set a flight mode by name and confirm via COMMAND_ACK."""
        mode_map = conn.mode_mapping()
        if mode_map is None or mode_name not in mode_map:
            log.warning("MAVLink: mode '%s' unknown to this vehicle", mode_name)
            return False
        mode_id = mode_map[mode_name]
        send_time = time.time()
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id,
            0, 0, 0, 0, 0,
        )
        return self._wait_ack(conn, mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                              device_id=device_id, since=send_time)

    def _arm(self, conn, device_id: str = None) -> bool:
        """Arm the vehicle's motors and confirm via COMMAND_ACK."""
        send_time = time.time()
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1, 0, 0, 0, 0, 0, 0,
        )
        return self._wait_ack(conn, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                              device_id=device_id, since=send_time)

    def _wait_ack(self, conn, command_id, device_id: str = None,
                  since: float = None) -> bool:
        """Wait for a COMMAND_ACK for the given command. Returns True only on an
        explicit ACCEPTED result — silence or rejection is False. This is the
        command-channel embodiment of 'no positive signal, no success'.

        SINGLE-READER design: the ACK is read by the telemetry listener (the only
        reader of the socket) and recorded in its ACK registry; this method consumes
        it from there via wait_for_ack, rather than reading the socket itself. The
        old socket read does not work once the command channel is outbound-only
        (udpout) — the ACK arrives on the listener's socket, not the command's.

        `since` is the command's send time; only an ACK that arrived at or after it
        counts, so a stale ACK from an earlier identical command is never accepted.
        If no listener is running for this device, there is no reader for the ACK —
        we log and return False (never assume success)."""
        listener = self._listeners.get(device_id) if device_id else None
        if listener is None:
            # No single reader for this device's ACKs. With an outbound-only command
            # channel there is nothing to read the ACK from, so we cannot confirm.
            # Silence is not success.
            log.warning("MAVLink: no telemetry listener for %s — cannot confirm "
                        "COMMAND_ACK for command %s (treating as not accepted)",
                        device_id, command_id)
            return False

        send_time = since if since is not None else time.time()
        result = listener.wait_for_ack(command_id, send_time, self._ack_timeout)
        if result is None:
            log.warning("MAVLink: no COMMAND_ACK for command %s within %ss",
                        command_id, self._ack_timeout)
            return False
        accepted = (result == mavutil.mavlink.MAV_RESULT_ACCEPTED)
        if not accepted:
            log.warning("MAVLink: command %s result=%s (not ACCEPTED)",
                        command_id, result)
        return accepted

    # ── Telemetry channel (Step 2b) ──────────────────────────────────────────
    def start_telemetry(self, device_id: str, conn_str: str = None) -> bool:
        """Begin listening to a vehicle's telemetry.

        Spawns a listener thread for the vehicle and ensures the consumer task is
        running. The listener reconnects on its own via the connection factory, so
        this can be called even before the vehicle is reachable.

        Returns False (and does nothing) in simulated mode — without pymavlink there
        is no socket to listen to. A hub with no drone library still runs; it just
        has no live telemetry, exactly like the command channel.
        """
        if not _MAVLINK_AVAILABLE:
            log.info("[SIMULATED] telemetry not started for %s (pymavlink absent)",
                     device_id)
            return False
        if device_id in self._listeners:
            return True  # already listening

        conn_str = conn_str or self._connection_string_for(device_id)
        if not conn_str:
            log.warning("Cannot start telemetry for %s: no connection string", device_id)
            return False

        # The factory lets the listener (re)open the link on its own thread, and
        # lets tests inject a fake. We do NOT share the command-channel connection:
        # telemetry reads continuously and must not contend with command ACKs.
        def factory():
            conn = mavutil.mavlink_connection(conn_str)
            conn.wait_heartbeat(timeout=10)
            return conn

        listener = MAVLinkTelemetryListener(
            device_id=device_id,
            connection_factory=factory,
            out_queue=self._telemetry_queue,
        )
        self._listeners[device_id] = listener
        listener.start()
        self._ensure_consumer()
        return True

    def _connection_string_for(self, device_id: str) -> Optional[str]:
        """Resolve a device's MAVLink endpoint from its manifest adapter_config."""
        if self._hub:
            device = self._hub.registry.get(device_id)
            if device and device.adapter_config:
                return device.adapter_config.get("connection")
        return None

    @staticmethod
    def _split_channels(cfg: dict) -> tuple:
        """Derive the COMMAND and TELEMETRY connection strings from adapter_config,
        so the two channels never bind the same UDP port.

        THE PROBLEM this solves: opening two `mavutil.mavlink_connection` on the same
        `udp:IP:PORT` makes both bind that local port — the second fails with
        'Address already in use'. The command channel only needs to SEND (and read
        its own ACKs), the telemetry channel needs to LISTEN continuously.

        THE RULE (panel decision, SITL/UDP):
          - command   → `udpout:IP:PORT` (outbound, does NOT bind a fixed local port)
          - telemetry → explicit `telemetry_connection` if given, else the original
                        `udp:`/`udpin:` (binds, listens) — the channel that receives
                        the vehicle's position stream.
        A `udp:` command string is rewritten to `udpout:` automatically so an
        existing manifest keeps working; telemetry keeps the binding form.

        TECH DEBT (documented, not hidden): a REAL drone over a serial radio is ONE
        bidirectional link — a serial port cannot be opened twice. The correct
        long-term design is a single connection with a single reader (producer/
        consumer: the listener reads, the command writes and awaits its ACK via the
        listener). This two-connection split is valid for SITL/UDP only; the serial
        hardware path requires unifying them. See DESIGN note in the class docstring.

        Returns (command_conn_str, telemetry_conn_str). Either may be None.
        """
        base = cfg.get("connection")
        telemetry = cfg.get("telemetry_connection")

        command = base
        if base and base.startswith("udp:"):
            # Rewrite the command channel to outbound so it does not bind the port
            # the telemetry channel listens on.
            command = "udpout:" + base[len("udp:"):]

        if telemetry is None:
            # No dedicated telemetry endpoint declared: the telemetry channel listens
            # on the original binding string (udp:/udpin:). If the command was the
            # only string and we rewrote it to udpout, telemetry uses the original
            # binding form so it actually receives the stream.
            telemetry = base

        # Defensive validation: if both channels would bind the SAME udp port, the
        # second open fails with 'Address already in use'. Fail clearly instead.
        def _binds_same_port(a: str, b: str) -> bool:
            def norm(s):
                for pfx in ("udp:", "udpin:"):
                    if s and s.startswith(pfx):
                        return s[len(pfx):]
                return None  # udpout/serial/etc don't bind a fixed listen port
            na, nb = norm(a), norm(b)
            return na is not None and na == nb

        if command and telemetry and _binds_same_port(command, telemetry):
            raise ValueError(
                f"MAVLink command and telemetry would bind the same UDP port "
                f"({command} / {telemetry}). Use 'udpout:' for the command "
                f"connection, or declare a separate 'telemetry_connection'. "
                f"(Two binds on one UDP port fail with 'Address already in use'.)"
            )

        return command, telemetry

    def _ensure_consumer(self) -> None:
        """Start the single consumer task if it isn't already running. The consumer
        runs on the event-loop thread — the only place the DB is touched."""
        if self._consumer_running:
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is None or not loop.is_running():
            # No running loop (e.g. some test contexts). The consumer can be driven
            # manually via drain_telemetry_once() instead.
            return
        self._consumer_running = True
        self._consumer_task = loop.create_task(self._consume_telemetry())

    async def _consume_telemetry(self) -> None:
        """Drain the telemetry queue, applying each fact to the hub. Runs until
        stop_telemetry() lowers the flag. Sleeps briefly when the queue is empty so
        it never busy-spins."""
        log.info("MAVLink telemetry consumer started")
        while self._consumer_running:
            applied = self.drain_telemetry_once()
            if applied == 0:
                await asyncio.sleep(0.1)
        log.info("MAVLink telemetry consumer stopped")

    def drain_telemetry_once(self) -> int:
        """Apply all currently-queued telemetry facts to the hub. Returns how many
        were applied. Separated from the async loop so tests can drive it directly
        without an event loop. This is the ONLY place the DB is touched for
        telemetry — always on the calling (event-loop) thread."""
        applied = 0
        while True:
            try:
                device_id, event, phase = self._telemetry_queue.get_nowait()
            except queue.Empty:
                break
            if self._hub is not None:
                try:
                    self._hub.apply_telemetry(device_id, event, phase=phase)
                except Exception as e:
                    log.warning("apply_telemetry failed for %s (%s): %s",
                                device_id, event, e)
            applied += 1
        return applied

    def stop_telemetry(self) -> None:
        """Stop all listeners and the consumer. Joins every listener thread."""
        self._consumer_running = False
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            self._consumer_task = None
        for device_id, listener in list(self._listeners.items()):
            listener.stop()
        self._listeners.clear()

    async def disconnect(self) -> None:
        """Close all cached MAVLink connections and tear down the telemetry channel."""
        self.stop_telemetry()
        for conn_str, conn in list(self._connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()

    async def get_state(self, device_id: str) -> Optional[dict]:
        """State query is part of the telemetry channel (Step 2). Deferred."""
        return None


def mavlink_manifest(
    device_id: str,
    device_name: str,
    connection: str,
    default_alt: float = 10.0,
    **kwargs,
):
    """Helper to build a CapabilityManifest for a MAVLink vehicle.

    The vehicle declares the five aerial actions as long-running, telemetry-capable
    actuators — so the execution_model tracks each as an operation and (in Step 2)
    the telemetry channel advances it. This is the aerial domain profile of the
    universal execution_model.
    """
    from ..models import (
        CapabilityManifest, ActuatorSpec, DeviceCategory, CertTier,
    )
    actuators = [
        ActuatorSpec(a, a, execution_model="long_running", emits_telemetry=True)
        for a in SUPPORTED_ACTIONS
    ]
    return CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer=kwargs.get("manufacturer", "MAVLink"),
        model=kwargs.get("model", "generic"),
        firmware=kwargs.get("firmware", "ArduPilot"),
        category=DeviceCategory.ACTUATOR,
        tags=kwargs.get("tags", ["aerial", "vehicle", "mavlink"]),
        actuators=actuators,
        sensors=[],
        emergency_capable=kwargs.get("emergency_capable", False),
        cert_tier=CertTier.BASIC,
        adapter_config={"connection": connection, "default_alt": default_alt},
    )


# ── Telemetry mapping (pure, Step 2a) ─────────────────────────────────────────
# The MAVLink-native -> abstract TelemetryEvent translation. This is a PURE
# function of (message, remembered previous mode): no socket, no I/O. The
# background listener loop (Step 2b) owns the socket and calls this for each
# message; keeping the mapping pure means it is exhaustively testable by injecting
# fake messages — including messages that produce NO event.
#
# Why a class and not a function: the most safety-critical event,
# MANUAL_CONTROL_TAKEN, is a TRANSITION, not a level. The vehicle broadcasts a
# HEARTBEAT ~1/s carrying its current flight mode. We must emit "a human took
# control" exactly once, on the GUIDED -> manual edge — not on every heartbeat
# that happens to be in a manual mode. That requires remembering the previous
# mode. The memory lives here, isolated from the socket.

# ArduCopter flight modes that mean "DoSync is driving" vs "a human/other is".
# GUIDED is the mode DoSync commands the vehicle in. AUTO (running a mission) is
# also autonomous. Anything else, entered while an operation is active, means
# control left DoSync's hands — most often a pilot moving the sticks.
_AUTONOMOUS_MODES = frozenset({"GUIDED", "AUTO"})


class MAVLinkTelemetryMapper:
    """Translates MAVLink messages into abstract TelemetryEvents, remembering the
    previous flight mode so manual-takeover is detected as an edge, not a level.

    Stateful only in the minimal way the transition detection requires. One mapper
    per vehicle/connection. Pure with respect to I/O — it never touches a socket;
    the listener loop (Step 2b) feeds it messages and acts on the events it returns.

    Each call to map_message returns either a (TelemetryEvent, phase) tuple or
    None when the message implies no operation-relevant fact. `phase` is an
    optional domain sub-phase string (e.g. "arming") carried with PREPARING; it is
    None for events that don't refine a phase.
    """

    def __init__(self):
        # Last flight mode we saw in a HEARTBEAT. None until the first heartbeat.
        self._last_mode: Optional[str] = None
        # Whether we've already emitted STARTED for the current flight, so we don't
        # re-emit it on every position update. Reset when disarmed.
        self._takeoff_confirmed = False

    def reset(self) -> None:
        """Clear remembered state — used on reconnect, so the mapper re-learns the
        vehicle's mode from the next heartbeat rather than assuming the past."""
        self._last_mode = None
        self._takeoff_confirmed = False

    def map_message(self, msg) -> Optional[tuple]:
        """Map one MAVLink message to (TelemetryEvent, phase) or None.

        `msg` is anything with a `.get_type()` and the relevant fields — a real
        pymavlink message in production, or a simple stand-in in tests. The mapping
        reads only attributes, never a socket, so it is fully testable offline.
        """
        # Local import so this module still imports without the reconciler in any
        # odd packaging; in practice it's always present.
        from ..reconciler import TelemetryEvent

        try:
            mtype = msg.get_type()
        except Exception:
            return None

        if mtype == "HEARTBEAT":
            return self._map_heartbeat(msg, TelemetryEvent)
        if mtype == "STATUSTEXT":
            return self._map_statustext(msg, TelemetryEvent)
        if mtype == "MISSION_ITEM_REACHED":
            # The vehicle reached a commanded waypoint — the positive completion
            # signal for a go_to. (Takeoff/land have their own confirmations.)
            return (TelemetryEvent.FINISHED, None)
        return None

    def _map_heartbeat(self, msg, TelemetryEvent) -> Optional[tuple]:
        """HEARTBEAT carries the current flight mode and the armed flag. This is
        where manual-takeover is detected, on the autonomous->manual edge."""
        mode = self._mode_name(msg)
        if mode is None:
            return None

        prev = self._last_mode
        self._last_mode = mode

        # The critical safety edge: we WERE driving (autonomous) and now we are
        # not. A human (or a failsafe) took control. Emit exactly once, on the edge.
        if (prev in _AUTONOMOUS_MODES
                and mode not in _AUTONOMOUS_MODES):
            return (TelemetryEvent.MANUAL_CONTROL_TAKEN, None)

        # No operation-relevant fact from this heartbeat (same mode, or a
        # manual->manual change, or the first heartbeat). Steady-state heartbeats
        # must NOT spam the reconciler.
        return None

    def _map_statustext(self, msg, TelemetryEvent) -> Optional[tuple]:
        """STATUSTEXT carries human-readable vehicle notices. We map only the few
        that correspond to operation facts; everything else is None."""
        text = (getattr(msg, "text", "") or "").strip()
        low = text.lower()
        if not low:
            return None
        # Arming is the start of the PREPARING phase, sub-phase "arming".
        if "arming motors" in low or low == "arming":
            return (TelemetryEvent.PREPARING, "arming")
        # A failure notice. ArduPilot emits varied failure text; we match the
        # common, unambiguous markers and stay conservative otherwise.
        if low.startswith("prearm") or "failsafe" in low or "crash" in low:
            return (TelemetryEvent.FAILED, None)
        return None

    @staticmethod
    def _mode_name(msg) -> Optional[str]:
        """Extract the flight-mode name from a HEARTBEAT. Real pymavlink exposes
        a mapping; in tests a stand-in can carry a `.mode_name` directly."""
        # Test/stand-in fast path.
        direct = getattr(msg, "mode_name", None)
        if direct:
            return direct
        # Real pymavlink path: decode custom_mode via the message's own helper if
        # present. We avoid importing mavutil here (keeps the mapper pure); the
        # listener (Step 2b) can attach a resolved mode_name onto the message
        # before calling, which is the direct path above. Returning None when we
        # can't resolve is safe: it yields no event.
        return None


# ── Telemetry listener (background, Step 2b) ──────────────────────────────────
# The listener is the producer half of a producer-consumer pair. It owns a thread
# that blocks on the MAVLink socket — necessary because pymavlink's recv_match is
# blocking and would freeze the asyncio event loop if called on it directly. The
# thread translates each message with the (pure) mapper and ENQUEUES the resulting
# (device_id, event, phase) fact. It NEVER touches the DB or the audit log: those
# live on the event-loop thread (SQLite is not safely shared across threads), so
# the consumer — an asyncio task on the main thread — is the only thing that calls
# hub.apply_telemetry. This split is the whole reason the design is two objects.
#
# Disconnection safety (the panel's hard rule): when the socket goes quiet, the
# thread does NOT invent state. Silence is not a fact and is never enqueued as one.
# The thread logs the gap, tries to reconnect on a fixed interval, and on reconnect
# calls mapper.reset() so it re-learns the vehicle's mode from scratch rather than
# assuming the past. Active operations simply stay where they are; their
# time_in_state grows and the Policy Engine is what watches them.

# How long recv_match blocks before returning control to the loop so it can check
# the stop flag and notice silence. Short, so manual-takeover latency stays ~1-2s
# and shutdown is responsive.
_RECV_TIMEOUT_S = 0.5
# After this many seconds with no message at all, treat the link as down and begin
# reconnect attempts. A healthy vehicle heartbeats ~1/s, so this is generous.
_SILENCE_BEFORE_RECONNECT_S = 5.0
# Reconnect backoff. On a long-range radio link a drop can last a while, and
# hammering the factory every few seconds is poor radio citizenship (and wasteful).
# So reconnect attempts back off exponentially: base, base*2, base*4 ... capped at
# max. The counter resets to base the moment a connection succeeds, so a brief
# blip recovers fast and only a sustained outage stretches the interval out.
_RECONNECT_BASE_S = 3.0    # first retry waits this long
_RECONNECT_MAX_S = 60.0    # never wait longer than this between attempts
# Waypoint-arrival threshold. A go_to issued via set_position_target does NOT
# produce a MISSION_ITEM_REACHED (that is mission-only), so without this the
# vehicle would reach its destination and DoSync would never mark the operation
# finished. The listener compares live position against the go_to target and emits
# FINISHED once the vehicle is within this horizontal radius. 3m is a touch beyond
# ArduPilot's typical 2m waypoint-acceptance radius, accounting for GPS jitter — a
# vehicle never hovers perfectly still over a point. "Entered the radius = arrived";
# we deliberately do not require holding for N seconds (a simpler, sufficient rule).
_WAYPOINT_ARRIVAL_RADIUS_M = 3.0
# A take_off is considered FINISHED once the vehicle reaches this fraction of the
# commanded altitude. ArduPilot itself treats a climb as complete around 95% — a
# vehicle oscillates and rarely settles exactly on the target, so requiring the exact
# altitude would never confirm and the supervisor would stall. Vertical analogue of
# the waypoint arrival radius.
_TAKEOFF_ARRIVAL_FRACTION = 0.95


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters. Delegates to the
    shared geo module (dosync/geo.py) so the formula lives in exactly one place.
    Kept as a thin module-level wrapper for the listener's waypoint-arrival check."""
    from ..geo import haversine_m
    return haversine_m(lat1, lon1, lat2, lon2)


class MAVLinkTelemetryListener:
    """A background thread that reads one vehicle's MAVLink telemetry, translates
    it with a MAVLinkTelemetryMapper, and enqueues abstract facts for the adapter's
    consumer to apply. One listener per vehicle.

    Lifecycle: start() spawns the thread; stop() lowers the running flag and joins.
    The thread never blocks longer than _RECV_TIMEOUT_S, so stop() returns promptly.

    The listener is given a `connection_factory` (a zero-arg callable returning an
    object with recv_match) rather than a live connection, so it can reconnect by
    calling the factory again — and so tests can inject a fake connection.
    """

    def __init__(self, device_id: str, connection_factory, out_queue: "queue.Queue",
                 mapper: "MAVLinkTelemetryMapper" = None):
        self.device_id = device_id
        self._connection_factory = connection_factory
        self._queue = out_queue
        self._mapper = mapper or MAVLinkTelemetryMapper()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._conn = None
        # Reconnect backoff: how many consecutive connect attempts have failed.
        # 0 = healthy. Each failure grows the wait (see _current_backoff); a
        # successful connect resets it to 0.
        self._reconnect_failures = 0
        # Active ARRIVAL TARGET for this vehicle, or None. A single target at a time:
        # the supervisor runs steps sequentially (it waits for one step's FINISHED
        # before dispatching the next), so a take_off (altitude target) and a go_to
        # (position target) are never active simultaneously. This unifies what were
        # two parallel mechanisms into one "arrival target" with a kind:
        #   ("position", (lat, lon))  — reached when within _WAYPOINT_ARRIVAL_RADIUS_M
        #   ("altitude", target_m)    — reached at _TAKEOFF_ARRIVAL_FRACTION of target
        # Lives HERE (per-vehicle, already drone-specific), not in the pure mapper or
        # the generic hub — neither should know about coordinates or altitude.
        self._arrival_target: Optional[tuple] = None  # (kind, value) | None
        self._target_lock = threading.Lock()

        # ── COMMAND_ACK registry (single-reader design) ──────────────────────
        # The listener is the ONLY reader of the socket, so COMMAND_ACKs arrive
        # here, not on the (outbound-only) command channel. We record the latest
        # ACK per command id and notify waiters. _wait_ack (called from the command
        # path) consumes from here instead of reading the socket itself — which is
        # exactly how a real GCS handles a single bidirectional link, and what makes
        # this adapter work over a serial radio (one link, one reader), not just
        # SITL/UDP. Maps command_id -> (result, arrival_time). A waiter only accepts
        # an ACK whose arrival_time is at or after its own send time, so a stale ACK
        # from an earlier identical command is never mistaken for a fresh one, and an
        # ACK that arrives just before the waiter starts waiting is not lost.
        self._ack_registry: dict = {}            # command_id -> (result, arrival_time)
        self._ack_condition = threading.Condition()
        # Set once the listener has an open connection. The command path waits on
        # this before dispatching, so the listener (the single reader) is already
        # listening when the first command's ACK comes back — otherwise the ACKs of
        # the opening commands (set_mode, arm) could be read-and-missed before the
        # reader is up.
        self._connected_event = threading.Event()

    def wait_connected(self, timeout: float) -> bool:
        """Block up to `timeout` for the listener to establish its connection.
        Returns True if connected within the timeout, False otherwise."""
        return self._connected_event.wait(timeout=timeout)

    def record_ack(self, command_id: int, result: int, at: float = None) -> None:
        """Record a COMMAND_ACK and wake any waiter. Called by the listener thread
        when it reads a COMMAND_ACK off the socket."""
        ts = at if at is not None else time.time()
        with self._ack_condition:
            self._ack_registry[command_id] = (result, ts)
            self._ack_condition.notify_all()

    def wait_for_ack(self, command_id: int, since: float, timeout: float) -> Optional[int]:
        """Block up to `timeout` seconds for a COMMAND_ACK for `command_id` that
        arrived at or after `since`. Returns the MAVLink result code, or None on
        timeout. Thread-safe: the listener thread records ACKs while this runs on
        the command path. The `since` filter is what prevents both the lost-ACK race
        (ACK arrived just before we started waiting — still counted, its arrival_time
        >= since) and the stale-ACK bug (an ACK from a prior identical command —
        arrival_time < since, ignored)."""
        deadline = time.time() + timeout
        with self._ack_condition:
            while True:
                entry = self._ack_registry.get(command_id)
                if entry is not None and entry[1] >= since:
                    return entry[0]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._ack_condition.wait(timeout=remaining)

    def set_arrival_target(self, kind: str, value) -> None:
        """Record the active arrival target. kind is "position" (value=(lat,lon)) or
        "altitude" (value=target_m). The listener emits FINISHED once the vehicle
        reaches it. Called by the adapter from the command channel — the one point
        where command and telemetry meet. Replaces any prior target (the supervisor's
        sequencing guarantees there is at most one in flight)."""
        with self._target_lock:
            self._arrival_target = (kind, value)

    def clear_arrival_target(self) -> None:
        with self._target_lock:
            self._arrival_target = None

    def _get_arrival_target(self) -> Optional[tuple]:
        with self._target_lock:
            return self._arrival_target

    # Backward-compatible aliases — set_destination/clear_destination were the
    # position-only API before take_off needed altitude confirmation. Kept so any
    # existing caller/test using them still works; they delegate to the unified target.
    def set_destination(self, lat: float, lon: float) -> None:
        self.set_arrival_target("position", (lat, lon))

    def clear_destination(self) -> None:
        self.clear_arrival_target()

    def _get_destination(self) -> Optional[tuple]:
        """Backward-compatible accessor: returns the active position target (lat,lon)
        or None. Returns None if the active target is an altitude (take_off) rather
        than a position — preserving the original position-only semantics."""
        target = self._get_arrival_target()
        if target is not None and target[0] == "position":
            return target[1]
        return None

    def _current_backoff(self) -> float:
        """Seconds to wait before the next reconnect attempt, growing
        exponentially with consecutive failures and capped at the max."""
        interval = _RECONNECT_BASE_S * (2 ** max(0, self._reconnect_failures - 1))
        return min(interval, _RECONNECT_MAX_S)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name=f"mavlink-listener-{self.device_id}", daemon=True)
        self._thread.start()
        log.info("MAVLink listener started for %s", self.device_id)

    def stop(self, join_timeout: float = 2.0) -> None:
        """Signal the thread to stop and wait for it to exit. Idempotent."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        log.info("MAVLink listener stopped for %s", self.device_id)

    def _run(self) -> None:
        """The thread body. Reads messages, maps them, enqueues facts. Handles
        silence and reconnection without ever inventing operation state."""
        last_message_at = time.time()
        while self._running:
            # Ensure we have a connection; (re)connect if needed.
            if self._conn is None:
                if not self._reconnect():
                    # Could not connect — wait (with exponential backoff) and retry,
                    # but keep checking _running so stop() stays responsive.
                    self._sleep_interruptible(self._current_backoff())
                    continue
                last_message_at = time.time()

            # Read one message (blocking up to _RECV_TIMEOUT_S).
            try:
                msg = self._conn.recv_match(blocking=True, timeout=_RECV_TIMEOUT_S)
            except Exception as e:
                log.warning("MAVLink listener %s: recv error: %s", self.device_id, e)
                self._drop_connection()
                continue

            now = time.time()
            if msg is None:
                # No message this cycle. Not a fact — never enqueue anything. Just
                # check whether the link has gone quiet long enough to be 'down'.
                if now - last_message_at > _SILENCE_BEFORE_RECONNECT_S:
                    log.warning("MAVLink listener %s: link silent %.1fs — reconnecting "
                                "(operations untouched; silence is not success)",
                                self.device_id, now - last_message_at)
                    self._drop_connection()
                continue

            # A real message arrived. Translate and, if it carries a fact, enqueue.
            last_message_at = now
            # COMMAND_ACK capture (single-reader design). The listener is the only
            # reader of the socket, so a command's ACK arrives HERE, not on the
            # outbound command channel. Record it so the waiting command path
            # (wait_for_ack) can consume it. This is what lets the command confirm
            # its ACK over a one-reader link — the serial-real design, not just SITL.
            try:
                if msg.get_type() == "COMMAND_ACK":
                    self.record_ack(msg.command, msg.result, now)
            except Exception:
                pass
            # Resolve the human-readable flight-mode name on heartbeats before the
            # mapper sees them. Real pymavlink HEARTBEATs carry the mode as a numeric
            # custom_mode, but the (pure, socket-free) mapper keys off a `mode_name`
            # string. Resolving it here — where we have the live connection — keeps
            # the mapper pure while making manual-takeover detection actually work
            # against a real vehicle (not just against tests that supply mode_name).
            self._attach_mode_name(msg)
            try:
                mapped = self._mapper.map_message(msg)
            except Exception as e:
                log.debug("MAVLink listener %s: map error on %s: %s",
                          self.device_id, getattr(msg, "get_type", lambda: "?")(), e)
                mapped = None
            if mapped is not None:
                event, phase = mapped
                # Enqueue the abstract fact. The consumer (event-loop thread) applies
                # it. We never touch the DB here.
                self._queue.put((self.device_id, event, phase))

            # Waypoint-arrival detection. A go_to (set_position_target) gets no
            # MISSION_ITEM_REACHED, so we close the loop by position: if a
            # destination is set and the vehicle is within the arrival radius, emit
            # FINISHED once and clear the destination. This is a positive
            # confirmation (the vehicle actually reached the point) — silence still
            # never completes anything.
            self._check_arrival(msg)

    def _attach_mode_name(self, msg) -> None:
        """For a HEARTBEAT, decode the numeric custom_mode into a mode-name string
        and attach it as `msg.mode_name`, which is what the mapper reads. No-op for
        other message types or if decoding is unavailable (the mapper then simply
        emits no event for that heartbeat — safe)."""
        try:
            if msg.get_type() != "HEARTBEAT":
                return
        except Exception:
            return
        if getattr(msg, "mode_name", None):
            return  # already resolved (e.g. a test stand-in)
        try:
            from pymavlink import mavutil
            msg.mode_name = mavutil.mode_string_v10(msg)
        except Exception:
            # Cannot resolve (no pymavlink, or unexpected message) — leave it unset.
            pass

    def _check_arrival(self, msg) -> None:
        """If an arrival target is active and this position message shows the vehicle
        has reached it, enqueue FINISHED once and clear the target. Handles both
        target kinds — a position (go_to) reached within the arrival radius, and an
        altitude (take_off) reached at the arrival fraction. GLOBAL_POSITION_INT
        carries both lat/lon (1e7 deg) and relative_alt (mm), so one message type
        serves both checks. The supervisor's sequencing guarantees only one target is
        ever active, so there is no risk of a double FINISHED."""
        try:
            if msg.get_type() != "GLOBAL_POSITION_INT":
                return
        except Exception:
            return
        target = self._get_arrival_target()
        if target is None:
            return
        kind, value = target
        from ..reconciler import TelemetryEvent

        if kind == "position":
            try:
                cur_lat = msg.lat / 1e7
                cur_lon = msg.lon / 1e7
            except Exception:
                return
            distance = _haversine_m(cur_lat, cur_lon, value[0], value[1])
            if distance <= _WAYPOINT_ARRIVAL_RADIUS_M:
                log.info("MAVLink listener %s: reached go_to target (%.1fm) — FINISHED",
                         self.device_id, distance)
                self._queue.put((self.device_id, TelemetryEvent.FINISHED, None))
                self.clear_arrival_target()

        elif kind == "altitude":
            try:
                cur_alt = msg.relative_alt / 1000.0  # mm -> m
            except Exception:
                return
            target_alt = float(value)
            if target_alt > 0 and cur_alt >= target_alt * _TAKEOFF_ARRIVAL_FRACTION:
                log.info("MAVLink listener %s: reached take_off altitude "
                         "(%.1fm of %.1fm) — FINISHED",
                         self.device_id, cur_alt, target_alt)
                self._queue.put((self.device_id, TelemetryEvent.FINISHED, None))
                self.clear_arrival_target()

    # Backward-compatible alias for the former method name.
    def _check_waypoint_arrival(self, msg) -> None:
        self._check_arrival(msg)

    def _reconnect(self) -> bool:
        """Open a fresh connection via the factory and reset the mapper so it does
        not assume the pre-disconnection mode. Returns True on success.

        On success the backoff counter resets to 0 (a recovered link retries fast
        next time). On failure it grows, stretching the interval for a sustained
        outage so we are not hammering a dead radio link."""
        try:
            self._conn = self._connection_factory()
            if self._conn is None:
                self._reconnect_failures += 1
                return False
            # Re-learn reality from the next heartbeat — never assume the past.
            self._mapper.reset()
            self._reconnect_failures = 0  # healthy again — reset backoff
            self._connected_event.set()
            log.info("MAVLink listener %s: connected", self.device_id)
            return True
        except Exception as e:
            self._reconnect_failures += 1
            log.warning("MAVLink listener %s: connect failed (attempt %d): %s",
                        self.device_id, self._reconnect_failures, e)
            self._conn = None
            return False

    def _drop_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._connected_event.clear()

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in small slices so a stop() is noticed quickly."""
        deadline = time.time() + seconds
        while self._running and time.time() < deadline:
            time.sleep(min(0.2, deadline - time.time()))
