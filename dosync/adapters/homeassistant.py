"""
DoSync — Home Assistant Bridge
===============================
Connects DoSync to Home Assistant through its local REST API.
Exposes Home Assistant entities as certifiable DoSync devices.

Requires:
    pip install aiohttp

Configuration:
    HA_URL   = "http://homeassistant.local:8123"
    HA_TOKEN = "<long-lived access token>"

Getting a token from Home Assistant:
    1. Profile (bottom-left icon) → Long-Lived Access Tokens
    2. "Create Token" → copy the token

Usage:
    from dosync.adapters.homeassistant import HABridge

    bridge = HABridge(
        ha_url="http://homeassistant.local:8123",
        ha_token="<token>",
        hub=hub,
    )

    # Import every HA entity into the hub
    count = await bridge.import_devices()
    print(f"Imported {count} devices")

    # Registrar el adapter en el executor
    executor.register(bridge)
"""

from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..hub import DoSyncHub

from ..adapters import DoSyncAdapter
from . import failure_reason
from ..models import (
    ActionResult, ActuatorSpec, CapabilityManifest,
    CertTier, DeviceAction, DeviceCategory, EventSpec,
    SensorSpec, Urgency,
)

log = logging.getLogger("dosync.adapters.ha")

# ── Domain → DoSync mapping ───────────────────────────────────────────────────
# Each HA domain maps to DoSync tags, actuators and sensors

HA_DOMAIN_MAP = {
    "light": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["light"],
        "actuators": [
            ActuatorSpec("turn_on",        "turn_on",        "Turn on"),
            ActuatorSpec("turn_off",       "turn_off",       "Turn off"),
            ActuatorSpec("set_brightness", "set_brightness", "Brightness 0-100%",
                         {"type": "object",
                          "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}},
                          "required": ["brightness"]}),
            ActuatorSpec("set_color",      "set_color",      "Color RGB",
                         {"type": "object",
                          "properties": {"r": {"type": "integer", "minimum": 0, "maximum": 255},
                                         "g": {"type": "integer", "minimum": 0, "maximum": 255},
                                         "b": {"type": "integer", "minimum": 0, "maximum": 255}},
                          "required": ["r", "g", "b"]}),
            ActuatorSpec("set_effect",     "set_effect",     "Efecto Ambilight",
                         {"type": "object",
                          "properties": {"effect": {"type": "string"}},
                          "required": ["effect"]}),
            ActuatorSpec("set_color_temp", "set_color_temp", "Colour temperature",
                         {"type": "object",
                          "properties": {"kelvin": {"type": "integer", "minimum": 2200, "maximum": 6500}},
                          "required": ["kelvin"]}),
        ],
        "sensors": [
            SensorSpec("state",      "boolean",  "On or off", kind="device_state"),
            SensorSpec("brightness", "integer",  "Current brightness", unit="%", kind="device_state"),
        ],
        # Bridged devices reach DoSync through the HA hop: an emergency response
        # should rely on natively-integrated devices, not on a bridge dependency.
        # Deployments that accept the dependency can override per device after
        # registration. (2026-07-12 — operator benchmark finding: the Ambilight
        # was force-included in emergencies because of this default.)
        "emergency_capable": False,
    },
    "switch": {
        "category": DeviceCategory.ACTUATOR,
        # "smart-plug" is a deprecated vendor-ish tag (TAG-VOCABULARY); the
        # native adapters stopped emitting it — the HA map lagged behind.
        "tags":      ["appliance"],
        "actuators": [
            ActuatorSpec("turn_on",  "turn_on",  "Turn on"),
            ActuatorSpec("turn_off", "turn_off", "Turn off"),
        ],
        "sensors": [SensorSpec("state", "boolean", "State", kind="device_state")],
        "emergency_capable": False,
    },
    "climate": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["thermostat", "climate"],
        "actuators": [
            ActuatorSpec("set_temperature", "set_temperature", "Temperature",
                         {"type": "object",
                          "properties": {"celsius": {"type": "number"}},
                          "required": ["celsius"]}),
            ActuatorSpec("turn_off",        "turn_off",        "Turn the climate off"),
        ],
        "sensors": [
            # The per-sensor grain earning its keep: current_temp MEASURES THE
            # ROOM (environment — a thermostat is also a thermometer); target_temp
            # is the setpoint, the device's own configuration (device_state).
            SensorSpec("current_temp", "temperature", "Current temperature", unit="celsius"),
            SensorSpec("target_temp",  "temperature", "Target temperature", unit="celsius",
                       kind="device_state"),
        ],
        "emergency_capable": False,
    },
    "cover": {
        "category": DeviceCategory.ACTUATOR,
        "tags":      ["blinds", "climate"],
        "actuators": [
            ActuatorSpec("set_position", "set_position", "Position 0-100%",
                         {"type": "object",
                          "properties": {"position": {"type": "integer", "minimum": 0, "maximum": 100}},
                          "required": ["position"]}),
            ActuatorSpec("turn_on",  "turn_on",  "Abrir"),
            ActuatorSpec("turn_off", "turn_off", "Cerrar"),
        ],
        "sensors": [SensorSpec("position", "integer", "Current position", unit="%",
                                kind="device_state")],
        "emergency_capable": False,
    },
    "lock": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["door-lock", "access", "emergency"],
        "actuators": [
            ActuatorSpec("unlock", "unlock", "Desbloquear"),
            ActuatorSpec("lock",   "lock",   "Bloquear"),
        ],
        "sensors": [SensorSpec("state", "boolean", "Locked or unlocked",
                                kind="device_state")],
        "emergency_capable": True,
    },
    "binary_sensor": {
        "category": DeviceCategory.SENSOR,
        "tags":      ["sensor"],
        "actuators": [],
        "sensors":   [SensorSpec("state", "boolean", "State")],
        "emergency_capable": False,
    },
    "sensor": {
        "category": DeviceCategory.SENSOR,
        "tags":      ["sensor"],
        "actuators": [],
        "sensors":   [SensorSpec("value", "float", "Sensor reading")],
        "emergency_capable": False,
    },
    "camera": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["camera", "emergency"],
        "actuators": [
            ActuatorSpec("record", "record", "Start recording"),
            ActuatorSpec("stream", "stream", "Start streaming"),
        ],
        "sensors":   [SensorSpec("state", "boolean", "Active", kind="device_state")],
        "emergency_capable": True,
    },
    "alarm_control_panel": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["alarm", "emergency", "security"],
        "actuators": [
            ActuatorSpec("arm",   "arm",   "Armar"),
            ActuatorSpec("alarm", "alarm", "Trigger the alarm"),
        ],
        "sensors":   [SensorSpec("state", "string", "Alarm state",
                                  kind="device_state")],
        "emergency_capable": True,
    },
    "media_player": {
        "category": DeviceCategory.HYBRID,
        "tags":      ["display", "communication"],
        "actuators": [
            ActuatorSpec("turn_on",  "turn_on",  "Turn on"),
            ActuatorSpec("turn_off", "turn_off", "Turn off"),
            ActuatorSpec("display",  "display",  "Mostrar mensaje"),
        ],
        "sensors":   [SensorSpec("state", "string", "State", kind="device_state")],
        "emergency_capable": False,
    },
}

# Domains we skip — they do not map to DoSync devices
# ── HA housekeeping (HA-BRIDGE-HYGIENE, 2026-07-19) ──────────────────────────
# Home Assistant's own internals — sunrise/sunset times from the `sun`
# integration, backup status from `backup` — surface as sensor.* entities and
# used to be imported as DoSync "devices". They are not devices in any
# meaningful sense: nothing about a building is being sensed, and every HA
# deployment has them (this is bridge-standard behavior, not one deployment's
# quirk — benchmark cause #3, the largest remaining precision gap). Skipped by
# DEFAULT; a deployment that genuinely wants them opts in with
# DOSYNC_HA_IMPORT_HOUSEKEEPING=true. The trailing underscore matters:
# "sun_" must not match a real sensor named "sunroom_temperature".
HA_HOUSEKEEPING_PREFIXES = ("sun_", "backup_")

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
    Bridge between DoSync and Home Assistant.

    Acts as:
    1. Scanner — importa dispositivos de HA como manifests DoSync
    2. Adapter — ejecuta acciones DoSync traducidas a servicios HA
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        hub: "DoSyncHub",
        simulated: bool = False,
        import_housekeeping: bool | None = None,
        exclude_prefixes: list[str] | None = None,
    ):
        """
        Args:
            ha_url:    URL de HA (ej: http://homeassistant.local:8123)
            ha_token:  Long-lived access token de HA
            hub:       instancia del DoSyncHub
            simulated: when True, use sample data instead of connecting to HA
        """
        self._url       = ha_url.rstrip("/")
        self._token     = ha_token
        self._hub       = hub
        self._simulated = simulated
        self._session   = None
        # HA-BRIDGE-HYGIENE: both are deployment configuration (env), exposed as
        # constructor params so tests need no environment.
        import os as _os
        self._import_housekeeping = (
            import_housekeeping if import_housekeeping is not None
            else _os.environ.get("DOSYNC_HA_IMPORT_HOUSEKEEPING", "").lower()
                 in ("1", "true", "yes"))
        raw_excludes = (exclude_prefixes if exclude_prefixes is not None
                        else [p.strip() for p in
                              _os.environ.get("DOSYNC_HA_EXCLUDE_ENTITIES", "").split(",")
                              if p.strip()])
        self._exclude_prefixes = tuple(raw_excludes)

        # Outcome of the most recent import cycle, readable through the hub's
        # status. The log alone was not enough: an expired token produced 401s
        # for days and nobody read the journal.
        self.last_import: dict | None = None

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

    async def start_import_loop(self, interval: float = None) -> None:
        """Re-import from Home Assistant on a schedule.

        `import_devices` existed, worked, and nothing called it. Its only
        appearance outside its own definition was an example in this module's
        docstring, so the bridge registered itself at startup — for EXECUTING
        actions — and the registry froze at whatever a manual invocation had
        put there. Add a sensor to Home Assistant and DoSync never sees it.
        Nothing fails and nothing says so.

        Found on 5 September while trying to validate a change to how sensor
        types are read: three service restarts produced no import lines at all.
        The same shape as `start_state_refresh`, whose own docstring records it
        never running for months behind a `log.debug`.

        Periodic rather than at startup, deliberately: a hub that runs for weeks
        is exactly the case that matters, and importing only at boot leaves it
        blind until the next restart. The interval defaults to 15 minutes —
        entities do not appear often, and each cycle is a full state fetch.

        Failure is loud and non-fatal. Home Assistant being unreachable, or a
        token having expired — both of which happened this week — must not stop
        a hub that also governs devices HA knows nothing about. But a silent
        failure is how the expired token went unnoticed for days, so every
        failed cycle is a warning and the outcome is readable from the hub's
        status, not only from the log.
        """
        if interval is None:
            interval = float(os.environ.get("DOSYNC_HA_IMPORT_INTERVAL", "900"))

        log.info("HA bridge: periodic import started (interval=%.0fs)", interval)

        while True:
            try:
                await asyncio.sleep(interval)
                result = await self.import_devices()
                self.last_import = {
                    "at": time.time(),
                    "ok": True,
                    **{k: result.get(k) for k in ("new", "updated", "skipped", "total")},
                }
                if result.get("new") or result.get("updated"):
                    log.info("HA bridge: import cycle — %d new, %d updated",
                             result.get("new", 0), result.get("updated", 0))
            except asyncio.CancelledError:
                log.info("HA bridge: periodic import stopped")
                break
            except Exception as e:
                # Warning and not debug: this is the level at which an expired
                # token becomes visible to whoever reads the journal.
                self.last_import = {"at": time.time(), "ok": False, "error": str(e)}
                log.warning("HA bridge: import cycle failed: %s", e)

    def _state_to_manifest(self, state: dict) -> Optional[CapabilityManifest]:
        """Convert an HA state object into a DoSync CapabilityManifest."""
        entity_id  = state.get("entity_id", "")
        domain     = entity_id.split(".")[0] if "." in entity_id else ""
        attributes = state.get("attributes", {})
        friendly   = attributes.get("friendly_name", entity_id)

        if domain in HA_IGNORED_DOMAINS:
            return None
        if domain not in HA_DOMAIN_MAP:
            return None

        # Skip ALL WiZ entities — DoSync registers WiZ bulbs directly via
        # WiZAdapter (device_id "wiz-*"), so importing their HA mirror creates
        # logical duplicates. This covers BOTH the primary light.wiz_* entity
        # (which duplicated all 10 bulbs on 2026-07-12) AND the read-only power
        # sub-sensors (sensor.wiz_*_power/energy/...) HA auto-creates. Devices
        # with a native DoSync adapter must not be re-imported through the bridge.
        if "wiz" in entity_id.lower():
            log.debug("Skipping WiZ entity (already registered via WiZAdapter): %s", entity_id)
            return None

        # HA housekeeping (sun times, backup status) is not a device. Skipped by
        # default; DOSYNC_HA_IMPORT_HOUSEKEEPING=true opts back in. See the
        # module-level note for the reasoning.
        name_part = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
        if (not self._import_housekeeping
                and name_part.startswith(HA_HOUSEKEEPING_PREFIXES)):
            log.debug("Skipping HA housekeeping entity: %s", entity_id)
            return None

        # Deployment-declared exclusions (DOSYNC_HA_EXCLUDE_ENTITIES): matched as
        # prefixes of the entity name, same rule as housekeeping.
        if self._exclude_prefixes and name_part.startswith(self._exclude_prefixes):
            log.debug("Skipping HA entity excluded by deployment config: %s", entity_id)
            return None

        mapping = HA_DOMAIN_MAP[domain]

        # The domain map alone types every binary_sensor as `boolean` and every
        # sensor as `float`, which is the SHAPE of the reading and not what it
        # measures. A motion detector, a door contact and a smoke alarm all
        # arrive as `boolean`, and a resolver deciding participation by declared
        # capability cannot tell them apart — measured in production, the
        # deployment's two HA binary sensors dropped out of every intent.
        #
        # Home Assistant already knows which is which: `device_class` is a
        # published, bounded enum (`motion`, `smoke`, `door`, `occupancy`, …).
        # The bridge was reading `attributes` for the friendly name and throwing
        # the rest away.
        sensors = self._typed_sensors(mapping, attributes)

        extra_tags = self._infer_tags(friendly, entity_id)

        manifest = CapabilityManifest(
            device_id=f"ha-{entity_id.replace('.', '-')}",
            device_name=f"{friendly}",
            manufacturer="Home Assistant",
            model=f"HA {domain}",
            firmware="auto",
            category=mapping["category"],
            tags=list(set(mapping["tags"] + extra_tags)),
            sensors=sensors,
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

    def _typed_sensors(self, mapping: dict, attributes: dict) -> list:
        """Replace a shape with a meaning when Home Assistant supplies one.

        `device_class` is HA's own vocabulary and it is bounded: this reads it
        verbatim rather than translating it, so an unfamiliar class arrives as
        itself instead of being flattened to `boolean`. A class DoSync does not
        recognise still says more than a shape does — and nothing here needs
        updating when HA adds one.

        When the attribute is absent, the domain default stands.
        """
        sensors = list(mapping.get("sensors", []))
        device_class = (attributes.get("device_class") or "").strip()
        if not device_class or not sensors:
            return sensors

        # Only the reading itself is retyped. Sensors marked `device_state` are
        # the hub's own bookkeeping — whether a light is on — and a device class
        # says nothing about those.
        retyped = []
        for spec in sensors:
            if getattr(spec, "kind", None) == "device_state":
                retyped.append(spec)
                continue
            retyped.append(SensorSpec(
                spec.id, device_class, spec.description,
                unit=getattr(spec, "unit", None),
                kind=getattr(spec, "kind", None)))
        return retyped

    def _infer_tags(self, friendly_name: str, entity_id: str) -> list[str]:
        """Infer location and type tags from the device name."""
        tags  = []
        name  = (friendly_name + " " + entity_id).lower()
        # Location tags from spec/TAG-VOCABULARY.md, plus the operator's own
        # vocabulary via DOSYNC_HA_LOCATION_TAGS (comma-separated). The list used
        # to be a bilingual hard-coded set of house rooms — one deployment's
        # language baked into a product surface, and useless to a deployment
        # whose locations are cells, wards or flight zones.
        rooms = ["entrance", "bedroom", "living-room", "kitchen", "bathroom",
                 "hallway", "office", "garage", "outdoor", "dining-room",
                 "basement"]
        extra = os.environ.get("DOSYNC_HA_LOCATION_TAGS", "")
        rooms += [t.strip().lower() for t in extra.split(",") if t.strip()]
        for room in rooms:
            if room in name:
                tags.append(room)
        return tags

    # ── Simulated mode ────────────────────────────────────────────────────────

    def _import_simulated(self) -> int:
        """Import sample devices for testing without a live HA instance."""
        simulated_states = [
            {"entity_id": "light.living_room_main",
             "state": "on",
             "attributes": {"friendly_name": "Sala — Luces principales",
                            "brightness": 200}},
            {"entity_id": "light.bedroom_lamp",
             "state": "off",
             "attributes": {"friendly_name": "Zone 5 — Lamp"}},
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
             "attributes": {"friendly_name": "Front door camera"}},
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
        """Translate a DoSync action into a Home Assistant service call and execute it."""
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
                error=failure_reason(e),
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
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
