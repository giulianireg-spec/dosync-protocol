"""
DoSync — Shelly Adapter
=======================
Adapter para dispositivos Shelly via HTTP local (Gen1 y Gen2).

Características:
- Comunicación 100% local — sin nube, sin internet requerido
- Compatible con Shelly 1, 1PM, 2.5, Plug S, Dimmer, RGBW2, Pro series
- Gen1: API /relay/0, /light/0 via GET requests
- Gen2: API RPC via POST /rpc/Switch.Set, /rpc/Light.Set
- No requiere dependencias externas — usa requests (ya en requirements.txt)

Instalación:
    No requiere instalación adicional.

Registro de un dispositivo Shelly en DoSync:
    from dosync.adapters.shelly import ShellyAdapter, shelly_manifest

    executor.register(ShellyAdapter())

    hub.register_device(shelly_manifest(
        device_id="shelly-living-01",
        device_name="Shelly Living Room",
        ip="192.168.1.50",
        device_type="relay",   # relay | dimmer | plug | rgbw
        gen=1,                 # 1 o 2
        tags=["light", "living-room"],
    ))
"""
from __future__ import annotations

import logging
from typing import Optional

from ..adapters import DoSyncAdapter
from ..models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters.shelly")

try:
    import requests as _requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    log.warning("requests not installed — ShellyAdapter running in simulated mode.")


# ── Manifest helper ───────────────────────────────────────────────────────────

def shelly_manifest(
    device_id: str,
    device_name: str,
    ip: str,
    device_type: str = "relay",
    gen: int = 1,
    tags: Optional[list[str]] = None,
    room: str = "",
    emergency_capable: bool = False,
):
    """
    Genera un CapabilityManifest listo para registrar un dispositivo Shelly.

    Args:
        device_id:        identificador único (ej: "shelly-living-01")
        device_name:      nombre visible (ej: "Shelly Living Room")
        ip:               IP del dispositivo en la red local
        device_type:      tipo: "relay" | "dimmer" | "plug" | "rgbw"
        gen:              generación de la API: 1 o 2
        tags:             tags adicionales
        room:             habitación (se agrega como tag)
        emergency_capable: si puede actuar en emergencias
    """
    from ..models import (
        ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
        EventSpec, SensorSpec, Urgency,
    )

    base_tags = ["shelly", "smart-plug"]

    # Tags según tipo de dispositivo
    type_tags = {
        "relay":  ["light", "appliance"],
        "dimmer": ["light", "climate"],
        "plug":   ["smart-plug", "appliance"],
        "rgbw":   ["light", "climate"],
    }
    base_tags.extend(type_tags.get(device_type, []))
    if tags:
        base_tags.extend(tags)
    if room:
        base_tags.append(room)

    # Actuadores según tipo
    actuators_map = {
        "relay":  [("turn_on", "Encender relay"), ("turn_off", "Apagar relay")],
        "dimmer": [("turn_on", "Encender dimmer"), ("turn_off", "Apagar dimmer"),
                   ("set_brightness", "Ajustar brillo 0-100%")],
        "plug":   [("turn_on", "Encender enchufe"), ("turn_off", "Apagar enchufe")],
        "rgbw":   [("turn_on", "Encender"), ("turn_off", "Apagar"),
                   ("set_brightness", "Brillo"), ("set_color", "Color RGB")],
    }
    actuators = [
        ActuatorSpec(id=f"{device_id}-{a[0]}", type=a[0], description=a[1])
        for a in actuators_map.get(device_type, actuators_map["relay"])
    ]

    return CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer="Allterco Robotics",
        model=f"Shelly {device_type.capitalize()} Gen{gen}",
        firmware="unknown",
        category=DeviceCategory.ACTUATOR,
        tags=list(set(base_tags)),
        sensors=[],
        actuators=actuators,
        events=[],
        emergency_capable=emergency_capable,
        cert_tier=CertTier.BASIC,
        adapter="shelly",
        adapter_config={"ip": ip, "device_type": device_type, "gen": gen},
    )


# ── Shelly HTTP client ────────────────────────────────────────────────────────

class _ShellyGen1:
    """Cliente HTTP para Shelly Gen1 (API REST simple)."""

    def __init__(self, ip: str):
        self.base = f"http://{ip}"
        self.timeout = 5

    def relay_set(self, channel: int, on: bool) -> dict:
        url = f"{self.base}/relay/{channel}"
        r = _requests.get(url, params={"turn": "on" if on else "off"},
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def light_set(self, channel: int, on: bool,
                  brightness: Optional[int] = None,
                  red: int = 255, green: int = 255, blue: int = 255) -> dict:
        url = f"{self.base}/light/{channel}"
        params: dict = {"turn": "on" if on else "off"}
        if brightness is not None:
            params["brightness"] = max(0, min(100, brightness))
        if red != 255 or green != 255 or blue != 255:
            params["red"] = red
            params["green"] = green
            params["blue"] = blue
        r = _requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def get_status(self) -> dict:
        r = _requests.get(f"{self.base}/status", timeout=self.timeout)
        r.raise_for_status()
        return r.json()


class _ShellyGen2:
    """Cliente HTTP para Shelly Gen2 (API RPC JSON)."""

    def __init__(self, ip: str):
        self.base = f"http://{ip}/rpc"
        self.timeout = 5

    def _rpc(self, method: str, params: dict) -> dict:
        r = _requests.post(self.base, json={"id": 1, "method": method,
                                             "params": params},
                           timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def switch_set(self, channel: int, on: bool) -> dict:
        return self._rpc("Switch.Set", {"id": channel, "on": on})

    def light_set(self, channel: int, on: bool,
                  brightness: Optional[int] = None) -> dict:
        params: dict = {"id": channel, "on": on}
        if brightness is not None:
            params["brightness"] = max(0, min(100, brightness))
        return self._rpc("Light.Set", params)

    def get_status(self) -> dict:
        return self._rpc("Shelly.GetStatus", {})


# ── Adapter ───────────────────────────────────────────────────────────────────

class ShellyAdapter(DoSyncAdapter):
    """
    Adapter DoSync para dispositivos Shelly.

    Soporta Gen1 (API REST) y Gen2 (API RPC).
    Sin dependencias externas — usa requests.
    """

    @property
    def adapter_name(self) -> str:
        return "shelly"

    async def get_state(self, device_id: str) -> dict | None:
        """
        Query current Shelly device state via HTTP.
        Supports Gen1 (/status) and Gen2 (Shelly.GetStatus).
        Timeout: 3 seconds.
        """
        if not self._hub:
            return None
        device = self._hub.registry.get(device_id)
        if not device or not device.adapter_config:
            return None
        config = device.adapter_config
        ip  = config.get("ip")
        gen = int(config.get("gen", 1))
        if not ip:
            return None
        try:
            import asyncio as _asyncio
            if gen == 1:
                client = _ShellyGen1(ip)
                raw = await _asyncio.wait_for(
                    _asyncio.get_event_loop().run_in_executor(None, client.get_status),
                    timeout=3.0,
                )
                relays = raw.get("relays", [])
                lights = raw.get("lights", [])
                if relays:
                    return {"on": relays[0].get("ison", False)}
                if lights:
                    return {"on": lights[0].get("ison", False),
                            "brightness": lights[0].get("brightness", 0)}
                return None
            else:
                client = _ShellyGen2(ip)
                raw = await _asyncio.wait_for(
                    _asyncio.get_event_loop().run_in_executor(None, client.get_status),
                    timeout=3.0,
                )
                switches = raw.get("result", {}).get("switch:0", {})
                if switches:
                    return {"on": switches.get("output", False)}
                lights = raw.get("result", {}).get("light:0", {})
                if lights:
                    return {"on": lights.get("output", False),
                            "brightness": lights.get("brightness", 0)}
                return None
        except Exception as e:
            import logging as _log
            _log.getLogger("dosync.adapters.shelly").debug(
                "get_state %s @ %s: %s", device_id, ip, e)
            return None

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        config = action.params.get("_adapter_config", {})
        ip = config.get("ip")
        device_type = config.get("device_type", "relay")
        gen = int(config.get("gen", 1))

        if not ip:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                response=None,
                error="Missing IP in adapter_config",
            )

        if not REQUESTS_AVAILABLE:
            log.warning("requests not available — simulating Shelly action %s on %s",
                        action.action, action.device_id)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response={"simulated": True, "ip": ip},
                error=None,
            )

        try:
            response = await self._execute_http(
                ip, gen, device_type, action
            )
            log.info("Shelly %s @ %s — %s OK", action.device_id, ip, action.action)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response=response,
                error=None,
            )
        except Exception as e:
            log.error("Shelly error %s @ %s: %s", action.device_id, ip, e)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                response=None,
                error=str(e),
            )

    async def _execute_http(self, ip: str, gen: int,
                            device_type: str, action: DeviceAction) -> dict:
        """Ejecuta la acción via HTTP según la generación del dispositivo."""
        import asyncio

        def _sync_call():
            channel = int(action.params.get("channel", 0))
            brightness = action.params.get("brightness")

            if gen == 1:
                client = _ShellyGen1(ip)
                if action.action == "turn_on":
                    if device_type in ("dimmer", "rgbw"):
                        return client.light_set(channel, True, brightness)
                    return client.relay_set(channel, True)
                elif action.action == "turn_off":
                    if device_type in ("dimmer", "rgbw"):
                        return client.light_set(channel, False)
                    return client.relay_set(channel, False)
                elif action.action == "set_brightness" and brightness is not None:
                    return client.light_set(channel, True, int(brightness))
                elif action.action == "set_color":
                    r = action.params.get("red", 255)
                    g = action.params.get("green", 255)
                    b = action.params.get("blue", 255)
                    return client.light_set(channel, True, brightness, r, g, b)
            else:
                client = _ShellyGen2(ip)
                if action.action == "turn_on":
                    if device_type == "dimmer":
                        return client.light_set(channel, True, brightness)
                    return client.switch_set(channel, True)
                elif action.action == "turn_off":
                    if device_type == "dimmer":
                        return client.light_set(channel, False)
                    return client.switch_set(channel, False)
                elif action.action == "set_brightness" and brightness is not None:
                    return client.light_set(channel, True, int(brightness))

            return {"status": "unknown_action", "action": action.action}

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_call)
