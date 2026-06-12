"""
DoSync — WiZ Adapter
====================
Adapter para lamparitas Philips WiZ via protocolo UDP local.

Características:
- Comunicación 100% local — sin nube, sin internet requerido
- Compatible con todas las lamparitas WiZ con WiFi
- Soporta: encender, apagar, brillo, color RGB, temperatura de color
- Discovery automático de IPs via broadcast (opcional)

Instalación:
    pip install pywizlight

Registro de un dispositivo WiZ en DoSync:

    from dosync.adapters.wiz import WiZAdapter, wiz_manifest

    # Registrar el adapter en el executor
    executor = AdapterExecutor(hub)
    executor.register(WiZAdapter())

    # Registrar la lamparita en el hub
    hub.register_device(wiz_manifest(
        device_id="wiz-living-01",
        device_name="Lámpara sala",
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
    log.warning(
        "pywizlight not installed — WiZAdapter running in simulated mode. "
        "Install with: pip install pywizlight"
    )


# ── Manifest helper ───────────────────────────────────────────────────────────

def wiz_manifest(
    device_id: str,
    device_name: str,
    ip: str,
    tags: Optional[list[str]] = None,
    room: str = "",
):
    """
    Genera un CapabilityManifest listo para registrar una lamparita WiZ.

    Args:
        device_id:   identificador único (ej: "wiz-living-01")
        device_name: nombre visible (ej: "Lámpara sala")
        ip:          IP de la lamparita en la red local (ej: "192.168.1.45")
        tags:        tags adicionales (se agregan a ["light", "wiz"])
        room:        habitación (se agrega como tag si se provee)
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

    # Guardamos la IP en adapter_config para que el AdapterExecutor la encuentre
    manifest = CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer="Philips WiZ",
        model="WiZ WiFi Bulb",
        firmware="auto",
        category=DeviceCategory.ACTUATOR,
        tags=list(set(base_tags)),
        sensors=[
            SensorSpec("brightness", "integer", "Brillo actual", unit="%"),
            SensorSpec("state",      "boolean", "Encendida/apagada"),
        ],
        actuators=[
            ActuatorSpec("turn_on",         "turn_on",         "Encender"),
            ActuatorSpec("turn_off",        "turn_off",        "Apagar"),
            ActuatorSpec("set_brightness",  "set_brightness",  "Brillo 0-100%",
                         {"brightness": "int (0-100)"}),
            ActuatorSpec("set_color",       "set_color",       "Color RGB",
                         {"r": "int 0-255", "g": "int 0-255", "b": "int 0-255"}),
            ActuatorSpec("set_color_temp",  "set_color_temp",  "Temperatura de color",
                         {"kelvin": "int 2200-6500"}),
            ActuatorSpec("set_scene",       "set_scene",       "Escena WiZ predefinida",
                         {"scene_id": "int 1-32"}),
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
    """
    DoSync adapter for Philips WiZ smart bulbs.

    All communication is direct UDP on the local network.
    No WiZ account or internet connection required.
    """

    @property
    def adapter_name(self) -> str:
        return "wiz"

    def _get_ip(self, action: DeviceAction) -> Optional[str]:
        """Obtiene la IP del dispositivo desde el registry del hub."""
        # The device IP is stored in adapter_config of the manifest
        from .. import models   # avoid circular import
        return None  # se resuelve en execute via action.params o manifest

    def __init__(self, hub=None):
        """
        Args:
            hub: referencia al DoSyncHub para leer adapter_config del manifest.
                 Opcional — si no se pasa, la IP debe venir en action.params.
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
            log.error("WiZ error %s @ %s: %s", action.device_id, ip, e)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error=str(e),
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
    29: "Rhythm",     # sincroniza con música
}