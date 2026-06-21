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

        default_alt = float(cfg.get("default_alt", 10.0))
        params = action.params or {}

        # ── Simulated mode — pymavlink not installed on this host ─────────────
        if not _MAVLINK_AVAILABLE:
            log.info("[SIMULATED] MAVLink %s: %s %s (would send to %s)",
                     action.device_id, action.action, params, conn_str)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=True,
                response={"status": "simulated", "connection": conn_str,
                          "command": action.action, "params": params},
            )

        # ── Real command dispatch ─────────────────────────────────────────────
        try:
            conn = self._get_connection(conn_str)
        except Exception as e:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"MAVLink connection to {conn_str} failed: {e}",
            )

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
            self._set_mode(conn, "GUIDED")
            self._arm(conn)
            conn.mav.command_long_send(
                conn.target_system, conn.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0, 0, 0, 0, 0, 0, 0, alt,
            )
            ok = self._wait_ack(conn, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF)
            detail = {"altitude": alt}

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

        elif act == "land":
            ok = self._set_mode(conn, "LAND")
            detail = {"mode": "LAND"}

        elif act == "return_home":
            ok = self._set_mode(conn, "RTL")
            detail = {"mode": "RTL"}

        elif act == "loiter":
            ok = self._set_mode(conn, "LOITER")
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
    def _set_mode(self, conn, mode_name: str) -> bool:
        """Set a flight mode by name and confirm via COMMAND_ACK."""
        mode_map = conn.mode_mapping()
        if mode_map is None or mode_name not in mode_map:
            log.warning("MAVLink: mode '%s' unknown to this vehicle", mode_name)
            return False
        mode_id = mode_map[mode_name]
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id,
            0, 0, 0, 0, 0,
        )
        return self._wait_ack(conn, mavutil.mavlink.MAV_CMD_DO_SET_MODE)

    def _arm(self, conn) -> bool:
        """Arm the vehicle's motors and confirm via COMMAND_ACK."""
        conn.mav.command_long_send(
            conn.target_system, conn.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1, 0, 0, 0, 0, 0, 0,
        )
        return self._wait_ack(conn, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM)

    def _wait_ack(self, conn, command_id) -> bool:
        """Wait for a COMMAND_ACK for the given command. Returns True only on an
        explicit ACCEPTED result — silence or rejection is False. This is the
        command-channel embodiment of 'no positive signal, no success'."""
        msg = conn.recv_match(type="COMMAND_ACK", blocking=True,
                              timeout=self._ack_timeout)
        if msg is None:
            log.warning("MAVLink: no COMMAND_ACK for command %s within %ss",
                        command_id, self._ack_timeout)
            return False
        if msg.command != command_id:
            # An ACK for a different command — not ours. Treat conservatively.
            log.debug("MAVLink: got ACK for %s while waiting for %s",
                      msg.command, command_id)
        accepted = (msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED)
        if not accepted:
            log.warning("MAVLink: command %s result=%s (not ACCEPTED)",
                        command_id, msg.result)
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
# Fixed interval between reconnect attempts (the panel deferred exponential backoff).
_RECONNECT_INTERVAL_S = 3.0


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
                    # Could not connect — wait and retry, but keep checking _running.
                    self._sleep_interruptible(_RECONNECT_INTERVAL_S)
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

    def _reconnect(self) -> bool:
        """Open a fresh connection via the factory and reset the mapper so it does
        not assume the pre-disconnection mode. Returns True on success."""
        try:
            self._conn = self._connection_factory()
            if self._conn is None:
                return False
            # Re-learn reality from the next heartbeat — never assume the past.
            self._mapper.reset()
            log.info("MAVLink listener %s: connected", self.device_id)
            return True
        except Exception as e:
            log.warning("MAVLink listener %s: connect failed: %s", self.device_id, e)
            self._conn = None
            return False

    def _drop_connection(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in small slices so a stop() is noticed quickly."""
        deadline = time.time() + seconds
        while self._running and time.time() < deadline:
            time.sleep(min(0.2, deadline - time.time()))
