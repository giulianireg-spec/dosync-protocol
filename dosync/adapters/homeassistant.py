"""
DoSync — Home Assistant Bridge
===============================
Conecta DoSync con Home Assistant via su API REST local.
Expone todos los dispositivos de HA como gadgets DoSync certificables.

Requiere:
    pip install aiohttp

Configuración:
    HA_URL   = "http://homeassistant.local:8123"
    HA_TOKEN = "<long-lived access token>"

Cómo obtener el token en HA:
    1. Perfil (ícono abajo izquierda) → Long-Lived Access Tokens
    2. "Create Token" → copiar el token

Uso:
    from dosync.adapters.homeassistant import HABridge

    bridge = HABridge(
        ha_url="http://homeassistant.local:8123",
        ha_token="<token>",
        hub=hub,
    )

    # Importar todos los dispositivos de HA al hub
    count = await bridge.import_devices()
    print(f"Imported {count} devices")

    # Registrar el adapter en el executor
    executor.register(bridge)
"""

from __future__ import annotations
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..hub import DoSyncHub

from ..adapters import DoSyncAdapter
from ..models import (
    ActionResult, ActuatorSpec, CapabilityManifest,
    CertTier, DeviceAction, DeviceCategory, EventSpec,
    SensorSpec, Urgency,
)

log = logging.getLogger("dosync.adapters.ha")

# ── Domain → DoSync mapping ───────────────────────────────────────────────────
# Cada dominio de HA se mapea a tags, actuadores y sensores DoSync

HA_DOMAIN_MAP = {
    "light": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["light", "climate"],
        "actuators": [
            ActuatorSpec("turn_on",        "turn_on",        "Encender"),
            ActuatorSpec("turn_off",       "turn_off",       "Apagar"),
            ActuatorSpec("set_brightness", "set_brightness", "Brillo 0-100%",
                         {"brightness": "int 0-100"}),
            ActuatorSpec("set_color",      "set_color",      "Color RGB",
                         {"r": "int", "g": "int", "b": "int"}),
            ActuatorSpec("set_effect",     "set_effect",     "Efecto Ambilight",
                         {"effect": "str"}),
            ActuatorSpec("set_color_temp", "set_color_temp", "Temperatura de color",
                         {"kelvin": "int 2200-6500"}),
        ],
        "sensors": [
            SensorSpec("state",      "boolean",  "Encendida/apagada"),
            SensorSpec("brightness", "integer",  "Brillo actual", unit="%"),
        ],
        "emergency_capable": True,
    },
    "switch": {
        "category": DeviceCategory.ACTUATOR,
        "tags":      ["smart-plug", "appliance"],
        "actuators": [
            ActuatorSpec("turn_on",  "turn_on",  "Encender"),
            ActuatorSpec("turn_off", "turn_off", "Apagar"),
        ],
        "sensors": [SensorSpec("state", "boolean", "Estado")],
        "emergency_capable": False,
    },
    "climate": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["thermostat", "climate"],
        "actuators": [
            ActuatorSpec("set_temperature", "set_temperature", "Temperatura",
                         {"celsius": "float"}),
            ActuatorSpec("turn_off",        "turn_off",        "Apagar clima"),
        ],
        "sensors": [
            SensorSpec("current_temp", "temperature", "Temperatura actual", unit="celsius"),
            SensorSpec("target_temp",  "temperature", "Temperatura objetivo", unit="celsius"),
        ],
        "emergency_capable": False,
    },
    "cover": {
        "category": DeviceCategory.ACTUATOR,
        "tags":      ["blinds", "climate"],
        "actuators": [
            ActuatorSpec("set_position", "set_position", "Posición 0-100%",
                         {"position": "int 0-100"}),
            ActuatorSpec("turn_on",  "turn_on",  "Abrir"),
            ActuatorSpec("turn_off", "turn_off", "Cerrar"),
        ],
        "sensors": [SensorSpec("position", "integer", "Posición actual", unit="%")],
        "emergency_capable": False,
    },
    "lock": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["door-lock", "access", "emergency"],
        "actuators": [
            ActuatorSpec("unlock", "unlock", "Desbloquear"),
            ActuatorSpec("lock",   "lock",   "Bloquear"),
        ],
        "sensors": [SensorSpec("state", "boolean", "Bloqueado/desbloqueado")],
        "emergency_capable": True,
    },
    "binary_sensor": {
        "category": DeviceCategory.SENSOR,
        "tags":      ["sensor"],
        "actuators": [],
        "sensors":   [SensorSpec("state", "boolean", "Estado")],
        "emergency_capable": False,
    },
    "sensor": {
        "category": DeviceCategory.SENSOR,
        "tags":      ["sensor"],
        "actuators": [],
        "sensors":   [SensorSpec("value", "float", "Valor del sensor")],
        "emergency_capable": False,
    },
    "camera": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["camera", "emergency"],
        "actuators": [
            ActuatorSpec("record", "record", "Iniciar grabación"),
            ActuatorSpec("stream", "stream", "Activar streaming"),
        ],
        "sensors":   [SensorSpec("state", "boolean", "Activa")],
        "emergency_capable": True,
    },
    "alarm_control_panel": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["alarm", "emergency", "security"],
        "actuators": [
            ActuatorSpec("arm",   "arm",   "Armar"),
            ActuatorSpec("alarm", "alarm", "Activar alarma"),
        ],
        "sensors":   [SensorSpec("state", "string", "Estado de alarma")],
        "emergency_capable": True,
    },
    "media_player": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["display", "communication"],
        "actuators": [
            ActuatorSpec("turn_on",  "turn_on",  "Encender"),
            ActuatorSpec("turn_off", "turn_off", "Apagar"),
            ActuatorSpec("display",  "display",  "Mostrar mensaje"),
        ],
        "sensors":   [SensorSpec("state", "string", "Estado")],
        "emergency_capable": False,
    },
}

# Dominios que ignoramos (no tienen sentido como gadgets DoSync)
HA_IGNORED_DOMAINS = {
    "automation", "script", "scene", "group",
    "input_boolean", "input_number", "input_select",
    "persistent_notification", "person", "zone",
    "sun", "weather", "update", "device_tracker",
}


# ── HA Service mapping ────────────────────────────────────────────────────────
# Traduce acciones DoSync a llamadas de servicio HA

def dosync_to_ha_service(domain: str, action: str, params: dict) -> tuple[str, str, dict]:
    """
    Retorna (domain, service, service_data) para llamar a HA.
    """
    if action == "turn_on":
        svc_data = {}
        if "brightness" in params:
            svc_data["brightness_pct"] = params["brightness"]
        if "r" in params:
            svc_data["rgb_color"] = [params["r"], params["g"], params["b"]]
        if "kelvin" in params:
            svc_data["color_temp_kelvin"] = params["kelvin"]
        return domain, "turn_on", svc_data

    if action == "turn_off":
        return domain, "turn_off", {}

    if action == "set_brightness":
        return domain, "turn_on", {"brightness_pct": params.get("brightness", 100)}

    if action == "set_color":
        return domain, "turn_on", {
            "rgb_color": [params.get("r",255), params.get("g",255), params.get("b",255)]
        }

    if action == "set_effect":
        return domain, "turn_on", {"effect": params.get("effect", "FOLLOW_VIDEO: STANDARD")}
    if action == "set_color_temp":
        return domain, "turn_on", {"color_temp_kelvin": params.get("kelvin", 4000)}

    if action == "set_temperature":
        return "climate", "set_temperature", {"temperature": params.get("celsius", 21)}

    if action == "set_position":
        return "cover", "set_cover_position", {"position": params.get("position", 50)}

    if action == "unlock":
        return "lock", "unlock", {}

    if action == "lock":
        return "lock", "lock", {}

    if action == "arm":
        return "alarm_control_panel", "alarm_arm_away", {}

    if action == "alarm":
        return "alarm_control_panel", "alarm_trigger", {}

    # Default: intentar turn_on
    return domain, "turn_on", {}


# ── HA Bridge ─────────────────────────────────────────────────────────────────

class HABridge(DoSyncAdapter):
    """
    Bridge entre DoSync y Home Assistant.

    Actúa como:
    1. Scanner — importa dispositivos de HA como manifests DoSync
    2. Adapter — ejecuta acciones DoSync traducidas a servicios HA
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        hub: "DoSyncHub",
        simulated: bool = False,
    ):
        """
        Args:
            ha_url:    URL de HA (ej: http://homeassistant.local:8123)
            ha_token:  Long-lived access token de HA
            hub:       instancia del DoSyncHub
            simulated: si True, usa datos de ejemplo sin conectar a HA real
        """
        self._url       = ha_url.rstrip("/")
        self._token     = ha_token
        self._hub       = hub
        self._simulated = simulated
        self._session   = None

    @property
    def adapter_name(self) -> str:
        return "homeassistant"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json",
        }

    async def _get_session(self):
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession(headers=self._headers())
            except ImportError:
                raise ImportError(
                    "aiohttp not installed. Run: pip install aiohttp"
                )
        return self._session

    # ── Import devices from HA ────────────────────────────────────────────────

    async def import_devices(self) -> dict:
        """
        Reads all HA states and registers devices in the hub.

        Returns {"new": N, "updated": M, "skipped": K, "total": N+M+K}
        - new:     device_id not previously in registry
        - updated: device_id existed but manifest changed
        - skipped: device_id existed with identical manifest (no-op)

        Idempotent: safe to run multiple times. Re-runs update changed
        devices and skip unchanged ones. Never creates duplicates.
        """
        if self._simulated:
            count = self._import_simulated()
            return {"new": count, "updated": 0, "skipped": 0, "total": count}

        try:
            session = await self._get_session()
            async with session.get(f"{self._url}/api/states") as resp:
                if resp.status == 401:
                    raise ValueError("Invalid HA token — check your Long-Lived Access Token")
                if resp.status != 200:
                    raise ConnectionError(f"HA returned status {resp.status}")
                states = await resp.json()
        except Exception as e:
            log.error("Failed to connect to HA at %s: %s", self._url, e)
            raise

        new_count = 0
        updated_count = 0
        skipped_count = 0

        for state in states:
            manifest = self._state_to_manifest(state)
            if not manifest:
                continue

            existing = self._hub.registry.get(manifest.device_id)
            if existing is None:
                self._hub.register_device(manifest)
                new_count += 1
                log.debug("HA bridge: new device %s", manifest.device_id)
            else:
                # Compare relevant fields — skip if nothing changed
                e, n = existing.to_dict(), manifest.to_dict()
                changed = (
                    e.get("device_name") != n.get("device_name")
                    or e.get("tags") != n.get("tags")
                    or e.get("actuators") != n.get("actuators")
                    or e.get("sensors") != n.get("sensors")
                    or e.get("emergency_capable") != n.get("emergency_capable")
                )
                if changed:
                    self._hub.register_device(manifest)
                    updated_count += 1
                    log.debug("HA bridge: updated device %s", manifest.device_id)
                else:
                    skipped_count += 1
                    log.debug("HA bridge: unchanged %s (skipped)", manifest.device_id)

        total = new_count + updated_count + skipped_count
        log.info(
            "HA bridge: %d new, %d updated, %d unchanged — %d device(s) from %s",
            new_count, updated_count, skipped_count, total, self._url,
        )
        return {"new": new_count, "updated": updated_count, "skipped": skipped_count, "total": total}

    def _state_to_manifest(self, state: dict) -> Optional[CapabilityManifest]:
        """Convierte un estado de HA en un CapabilityManifest DoSync."""
        entity_id  = state.get("entity_id", "")
        domain     = entity_id.split(".")[0] if "." in entity_id else ""
        attributes = state.get("attributes", {})
        friendly   = attributes.get("friendly_name", entity_id)

        if domain in HA_IGNORED_DOMAINS:
            return None
        if domain not in HA_DOMAIN_MAP:
            return None

        # Skip WiZ power monitoring sensors — these are read-only sub-entities
        # that HA auto-creates for WiZ bulbs. DoSync registers WiZ bulbs directly
        # via WiZAdapter, so importing these creates logical duplicates.
        if (domain == "sensor"
                and "wiz" in entity_id.lower()
                and entity_id.rsplit("_", 1)[-1] in {"power", "energy", "voltage", "current"}):
            log.debug("Skipping WiZ sub-sensor (already registered via WiZAdapter): %s", entity_id)
            return None

        mapping = HA_DOMAIN_MAP[domain]

        # Inferir tags adicionales del nombre
        extra_tags = self._infer_tags(friendly, entity_id)

        manifest = CapabilityManifest(
            device_id=f"ha-{entity_id.replace('.', '-')}",
            device_name=f"{friendly}",
            manufacturer="Home Assistant",
            model=f"HA {domain}",
            firmware="auto",
            category=mapping["category"],
            tags=list(set(mapping["tags"] + extra_tags)),
            sensors=mapping.get("sensors", []),
            actuators=mapping.get("actuators", []),
            events=[],
            emergency_capable=mapping.get("emergency_capable", False),
            cert_tier=CertTier.STANDARD,
        )
        manifest.adapter        = "homeassistant"
        manifest.adapter_config = {
            "entity_id": entity_id,
            "domain":    domain,
            "ha_url":    self._url,
        }
        return manifest

    def _infer_tags(self, friendly_name: str, entity_id: str) -> list[str]:
        """Infiere tags de ubicación y tipo desde el nombre del dispositivo."""
        tags  = []
        name  = (friendly_name + " " + entity_id).lower()
        rooms = [
            "living", "sala", "bedroom", "dormitorio", "kitchen", "cocina",
            "bathroom", "baño", "garage", "garden", "outdoor", "exterior",
            "office", "oficina", "hallway", "entrance", "entrada",
        ]
        for room in rooms:
            if room in name:
                tags.append(room)
        return tags

    # ── Simulated mode ────────────────────────────────────────────────────────

    def _import_simulated(self) -> int:
        """Importa dispositivos de ejemplo para testing sin HA real."""
        simulated_states = [
            {"entity_id": "light.living_room_main",
             "state": "on",
             "attributes": {"friendly_name": "Sala — Luces principales",
                            "brightness": 200}},
            {"entity_id": "light.bedroom_lamp",
             "state": "off",
             "attributes": {"friendly_name": "Dormitorio — Lámpara"}},
            {"entity_id": "climate.main_thermostat",
             "state": "heat",
             "attributes": {"friendly_name": "Termostato principal",
                            "current_temperature": 20.5,
                            "temperature": 22}},
            {"entity_id": "lock.front_door",
             "state": "locked",
             "attributes": {"friendly_name": "Cerradura puerta principal"}},
            {"entity_id": "cover.living_room_blinds",
             "state": "closed",
             "attributes": {"friendly_name": "Persiana sala", "position": 0}},
            {"entity_id": "alarm_control_panel.main",
             "state": "disarmed",
             "attributes": {"friendly_name": "Alarma principal"}},
            {"entity_id": "binary_sensor.motion_living",
             "state": "off",
             "attributes": {"friendly_name": "Sensor movimiento sala"}},
            {"entity_id": "switch.coffee_maker",
             "state": "off",
             "attributes": {"friendly_name": "Cafetera"}},
            {"entity_id": "media_player.living_tv",
             "state": "standby",
             "attributes": {"friendly_name": "TV sala"}},
            {"entity_id": "camera.front_door",
             "state": "idle",
             "attributes": {"friendly_name": "Cámara puerta principal"}},
        ]
        count = 0
        for state in simulated_states:
            manifest = self._state_to_manifest(state)
            if manifest:
                self._hub.register_device(manifest)
                count += 1
        log.info("HA bridge (simulated): imported %d device(s)", count)
        return count

    # ── Execute action ────────────────────────────────────────────────────────

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Traduce una acción DoSync a un servicio de HA y lo ejecuta."""
        device = self._hub.registry.get(action.device_id)
        if not device or not device.adapter_config:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error="Device not found or missing adapter_config",
            )

        entity_id = device.adapter_config.get("entity_id")
        domain    = device.adapter_config.get("domain", entity_id.split(".")[0])

        svc_domain, service, svc_data = dosync_to_ha_service(
            domain, action.action, action.params
        )
        svc_data["entity_id"] = entity_id

        # Modo simulado
        if self._simulated:
            log.info(
                "[HA SIMULATED] %s.%s(%s) → %s/%s %s",
                action.device_id, action.action, action.params,
                svc_domain, service, svc_data,
            )
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response={
                    "status":     "simulated",
                    "ha_service": f"{svc_domain}.{service}",
                    "entity_id":  entity_id,
                    "data":       svc_data,
                },
            )

        # Llamada real a HA
        try:
            session = await self._get_session()
            url = f"{self._url}/api/services/{svc_domain}/{service}"
            async with session.post(url, json=svc_data) as resp:
                if resp.status in (200, 201):
                    log.info(
                        "HA %s.%s → %s.%s OK",
                        action.device_id, action.action, svc_domain, service,
                    )
                    return ActionResult(
                        device_id=action.device_id,
                        action=action.action,
                        success=True,
                        response={
                            "status":     "ok",
                            "ha_service": f"{svc_domain}.{service}",
                            "entity_id":  entity_id,
                        },
                    )
                else:
                    body = await resp.text()
                    return ActionResult(
                        device_id=action.device_id,
                        action=action.action,
                        success=False,
                        error=f"HA returned {resp.status}: {body[:200]}",
                    )
        except Exception as e:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error=str(e),
            )

    async def get_state(self, device_id: str) -> dict | None:
        """
        Query current HA entity state via REST API.
        Returns normalized state dict or None on failure.
        Timeout: 3 seconds.
        """
        if self._simulated:
            return None
        if not self._hub:
            return None
        device = self._hub.registry.get(device_id)
        if not device or not device.adapter_config:
            return None
        entity_id = device.adapter_config.get("entity_id")
        if not entity_id:
            return None
        try:
            session = await self._get_session()
            async with session.get(
                f"{self._url}/api/states/{entity_id}",
                timeout=3.0,
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                state_str = data.get("state", "")
                attrs     = data.get("attributes", {})
                # Normalize to a common state format
                on = state_str not in ("off", "unavailable", "unknown", "0")
                result = {"on": on, "state": state_str}
                if "brightness" in attrs:
                    result["brightness"] = round(attrs["brightness"] / 255 * 100)
                if "temperature" in attrs:
                    result["temperature"] = attrs["temperature"]
                if "current_temperature" in attrs:
                    result["current_temperature"] = attrs["current_temperature"]
                return result
        except Exception as e:
            log.debug("HABridge get_state %s (%s): %s", device_id, entity_id, e)
            return None

    async def close(self) -> None:
        """Cierra la sesión HTTP."""
        if self._session:
            await self._session.close()
            self._session = None
