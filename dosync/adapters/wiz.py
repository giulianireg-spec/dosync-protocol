"""
DoSync — WiZ Adapter
====================
Adapter para lamparitas Philips WiZ via protocolo UDP local.

Features:
- Fully local communication — no cloud, no internet required
- Works with any WiFi WiZ bulb
- Soporta: encender, apagar, brillo, color RGB, temperatura de color
- Optional address discovery via broadcast

Installation:
    pip install pywizlight

Registro de un dispositivo WiZ en DoSync:

    from dosync.adapters.wiz import WiZAdapter, wiz_manifest

    # Registrar el adapter en el executor
    executor = AdapterExecutor(hub)
    executor.register(WiZAdapter())

    # Registrar la lamparita en el hub
    hub.register_device(wiz_manifest(
        device_id="wiz-living-01",
        device_name="Zone 1 lamp",
        ip="192.168.1.45",
        tags=["light", "living-room", "climate"],
    ))
"""

from __future__ import annotations
import logging
from typing import Optional

from ..adapters import DoSyncAdapter
from ..models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters.wiz")

# Optional import — if pywizlight is not installed, the adapter
# operates in simulated mode with a warning
try:
    from pywizlight import wizlight, PilotBuilder
    WIZ_AVAILABLE = True
except ImportError:
    WIZ_AVAILABLE = False
    # Deliberately silent. This is a REFERENCE adapter: a worked example of how
    # an adapter is written, for one vendor's product. A hub whose operator owns
    # no WiZ device must never be told to install a vendor library — that
    # presumes a configuration the protocol has no business presuming, and it
    # was the only adapter doing it. An operator who HAS registered a WiZ device
    # hears about it where it matters instead: the startup sweep names their
    # device and says its actions will be simulated.
    log.debug("pywizlight not installed — the WiZ reference adapter is inactive")


# ── Manifest helper ───────────────────────────────────────────────────────────

def wiz_manifest(
    device_id: str,
    device_name: str,
    ip: str,
    tags: Optional[list[str]] = None,
    room: str = "",
):
    """
    Build a CapabilityManifest ready to register a WiZ bulb.

    Args:
        device_id:   unique identifier (e.g. "wiz-zone1-01")
        device_name: display name (e.g. "Zone 1 lamp")
        ip:          bulb address on the local network (e.g. "192.168.1.45")
        tags:        tags adicionales (se agregan a ["light", "wiz"])
        room:        location (added as a tag when provided)
    """
    from ..models import (
        ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
        EventSpec, SensorSpec, Urgency,
    )

    # Tag vocabulary: only canonical role tags here. Vendor names ("wiz") and
    # imprecise tags ("climate") are non-portable and were removed per
    # TAG-VOCABULARY.md. The caller adds emergency/energy/location via `tags`.
    base_tags = ["light"]
    if tags:
        base_tags.extend(tags)
    if room:
        base_tags.append(room)

    # Store the address in adapter_config so the AdapterExecutor can find it
    manifest = CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer="Philips WiZ",
        model="WiZ WiFi Bulb",
        firmware="auto",
        category=DeviceCategory.ACTUATOR,
        tags=list(set(base_tags)),
        sensors=[
            # kind="device_state": these describe the LAMP, not the room. Real
            # telemetry, truthfully declared — but "read the environment" should
            # not sweep them (SENSOR-KIND, 2026-07-17).
            SensorSpec("brightness", "integer", "Brillo actual", unit="%",
                       kind="device_state"),
            SensorSpec("state",      "boolean", "Encendida/apagada",
                       kind="device_state"),
        ],
        actuators=[
            ActuatorSpec("turn_on",         "turn_on",         "Encender"),
            ActuatorSpec("turn_off",        "turn_off",        "Apagar"),
            ActuatorSpec("set_brightness",  "set_brightness",  "Brillo 0-100%",
                         {"type": "object",
                          "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}},
                          "required": ["brightness"]}),
            ActuatorSpec("set_color",       "set_color",       "Color RGB",
                         {"type": "object",
                          "properties": {"r": {"type": "integer", "minimum": 0, "maximum": 255},
                                         "g": {"type": "integer", "minimum": 0, "maximum": 255},
                                         "b": {"type": "integer", "minimum": 0, "maximum": 255}},
                          "required": ["r", "g", "b"]}),
            ActuatorSpec("set_color_temp",  "set_color_temp",  "Temperatura de color",
                         {"type": "object",
                          "properties": {"kelvin": {"type": "integer", "minimum": 2200, "maximum": 6500}},
                          "required": ["kelvin"]}),
            ActuatorSpec("set_scene",       "set_scene",       "Escena WiZ predefinida",
                         {"type": "object",
                          "properties": {"scene_id": {"type": "integer", "minimum": 1, "maximum": 32}},
                          "required": ["scene_id"]}),
        ],
        events=[],
        emergency_capable=True,      # puede usarse en emergencias (luces al max)
        cert_tier=CertTier.STANDARD,
    )

    # Adjuntar config del adapter directamente al manifest
    manifest.adapter        = "wiz"
    manifest.adapter_config = {"ip": ip, "port": 38899}

    return manifest


# ── WiZ Adapter ───────────────────────────────────────────────────────────────

class WiZAdapter(DoSyncAdapter):
    #: Reference implementation. See DoSyncAdapter.adapter_kind — shipped as a
    #: worked example of how an adapter is written, not as vendor-endorsed
    #: support for this product.
    adapter_kind = "reference"

    """
    DoSync adapter for Philips WiZ smart bulbs.

    All communication is direct UDP on the local network.
    No WiZ account or internet connection required.
    """

    @property
    def adapter_name(self) -> str:
        return "wiz"

    def _get_ip(self, action: DeviceAction) -> Optional[str]:
        """Get the device address from the hub registry."""
        # The device IP is stored in adapter_config of the manifest
        from .. import models   # avoid circular import
        return None  # se resuelve en execute via action.params o manifest

    def __init__(self, hub=None):
        """
        Args:
            hub: a DoSyncHub reference, to read adapter_config from the manifest.
                 Optional — without it, the address must come in action.params.
        """
        self._hub = hub

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Translate a DoSync action into a WiZ UDP command."""

        # Priority: action params override adapter_config from the manifest
        ip = action.params.get("ip")

        if not ip and self._hub:
            device = self._hub.registry.get(action.device_id)
            if device and device.adapter_config:
                ip = device.adapter_config.get("ip")

        if not ip:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error="WiZ IP not found. Use wiz_manifest(ip=...) or pass ip in params.",
            )

        if not WIZ_AVAILABLE:
            # Modo simulado — pywizlight no instalado
            log.info(
                "[SIMULATED] WiZ %s @ %s: %s %s",
                action.device_id, ip, action.action, action.params,
            )
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response={"status": "simulated", "ip": ip, "action": action.action},
            )

        try:
            bulb = wizlight(ip)
            pilot = await self._build_pilot(action, urgency)

            if action.action == "turn_off" or pilot is None:
                await bulb.turn_off()
                response = {"status": "off", "ip": ip}
            else:
                await bulb.turn_on(pilot)
                response = {
                    "status": "on",
                    "ip":     ip,
                    "action": action.action,
                    "params": action.params,
                }

            await bulb.async_close()

            log.info(
                "WiZ %s @ %s: %s → OK",
                action.device_id, ip, action.action,
            )
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response=response,
            )

        except Exception as e:
            import asyncio as _asyncio
            if isinstance(e, _asyncio.TimeoutError):
                log.warning("WiZ timeout %s @ %s — marking unreachable",
                            action.device_id, ip)
                if self._hub and hasattr(self._hub, 'resolver'):
                    if hasattr(self._hub.resolver, 'mark_unreachable'):
                        self._hub.resolver.mark_unreachable(action.device_id)
                return ActionResult(
                    device_id=action.device_id,
                    action=action.action,
                    success=False,
                    error="WiZ timeout — device unreachable",
                )
            # `str(e)` alone is not a reason. pywizlight raises exceptions whose
            # text is empty — a bulb that was powered off at the wall returned
            # `success: false, error: ""`, and the log line ended at the colon.
            # The operator was told the action failed and nothing else, which is
            # the same failure this project fixed in the verification panel: an
            # exception carrying no message must not become a blank field.
            reason = str(e).strip() or (
                f"{type(e).__name__} with no message — the bulb did not answer. "
                "It is usually powered off at the wall, on another network, or "
                "busy with another controller.")
            log.error("WiZ error %s @ %s: %s", action.device_id, ip, reason)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error=reason,
            )

    async def get_state(self, device_id: str) -> dict | None:
        """
        Query current WiZ bulb state via UDP updateState.
        Returns {"on": bool, "brightness": int} or None on failure.
        Timeout: 3 seconds.
        """
        ip = None
        if self._hub:
            device = self._hub.registry.get(device_id)
            if device and device.adapter_config:
                ip = device.adapter_config.get("ip")
        if not ip:
            return None
        if not WIZ_AVAILABLE:
            return None
        try:
            import asyncio as _asyncio
            bulb = wizlight(ip)
            pilot = await _asyncio.wait_for(bulb.updateState(), timeout=3.0)
            await bulb.async_close()
            if pilot:
                pr = pilot.pilotResult
                return {
                    "on":         pr.get("state", False),
                    "brightness": pr.get("dimming", 0),
                    "r":          pr.get("r"),
                    "g":          pr.get("g"),
                    "b":          pr.get("b"),
                    "temp":       pr.get("temp"),
                }
            return None
        except Exception as e:
            log.debug("WiZ get_state %s @ %s: %s", device_id, ip, e)
            return None

    async def _build_pilot(self, action: DeviceAction, urgency: Urgency) -> "PilotBuilder":
        """Build a pywizlight PilotBuilder for the given DoSync action."""
        params = action.params

        # Emergency: always maximum brightness, cool white
        if urgency == Urgency.EMERGENCY:
            return PilotBuilder(brightness=255, colortemp=6500)

        if action.action == "turn_on":
            brightness = params.get("brightness", 100)
            return PilotBuilder(brightness=self._pct_to_wiz(brightness))

        if action.action == "set_brightness":
            pct = params.get("brightness", 100)
            # Si viene 0 es apagar
            if pct == 0:
                return None  # se maneja como turn_off en execute()
            return PilotBuilder(brightness=self._pct_to_wiz(pct))

        if action.action == "set_color":
            r = params.get("r", 255)
            g = params.get("g", 255)
            b = params.get("b", 255)
            return PilotBuilder(rgb=(r, g, b))

        if action.action == "set_color_temp":
            kelvin = params.get("kelvin", 4000)
            return PilotBuilder(colortemp=kelvin)

        if action.action == "set_scene":
            scene_id = params.get("scene_id", 1)
            return PilotBuilder(scene=scene_id)

        # Default: encender con brillo al 80%
        return PilotBuilder(brightness=204)

    @staticmethod
    def _pct_to_wiz(pct: int) -> int:
        """Convierte porcentaje DoSync (0-100) a valor WiZ (0-255)."""
        return max(0, min(255, round(pct * 255 / 100)))


# ── Escenas WiZ predefinidas (referencia) ────────────────────────────────────

WIZ_SCENES = {
    1:  "Ocean",
    2:  "Romance",
    3:  "Sunset",
    4:  "Party",
    5:  "Fireplace",
    6:  "Cozy",
    9:  "Cool white",
    10: "Night light",
    11: "Focus",
    12: "Relax",
    13: "True colors",
    14: "TV time",
    15: "Plantgrowth",
    16: "Spring",
    17: "Summer",
    18: "Fall",
    19: "Deepdive",
    20: "Jungle",
    21: "Mojito",
    22: "Club",
    23: "Christmas",
    24: "Halloween",
    25: "Candlelight",
    26: "Golden white",
    27: "Pulse",
    28: "Steampunk",
    29: "Rhythm",     # syncs to music
}