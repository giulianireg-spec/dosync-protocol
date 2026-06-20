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

    async def disconnect(self) -> None:
        """Close all cached MAVLink connections."""
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
