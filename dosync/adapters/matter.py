"""
DoSync — Matter Adapter
=======================
Adapter for Matter devices, via python-matter-server or Home Assistant bridge.

Matter is the Connectivity Standards Alliance IoT interoperability standard.
This adapter supports two modes:

Mode 1 — via Home Assistant (recommended):
    Reuses the existing HABridge: when HA has the Matter integration,
    Matter devices appear as HA entities and are controlled
    through the HABridge. No additional setup required.

Mode 2 — via python-matter-server (standalone, experimental):
    Connects directly to a python-matter-server running locally.
    Requires: pip install matter-server-client

Installation:
    HA mode:     no additional installation required
    Standalone mode: pip install matter-server-client (experimental)

Registering a Matter device with DoSync:
    from dosync.adapters.matter import MatterAdapter, matter_manifest

    executor.register(MatterAdapter(mode="ha", ha_url="http://localhost:8123",
                                    ha_token="..."))

    hub.register_device(matter_manifest(
        device_id="matter-light-01",
        device_name="Matter bulb, zone 1",
        entity_id="light.matter_bulb_zone1",   # the entity_id in HA
        location="building-b/floor-3/zone-1",
    ))
"""
from __future__ import annotations

import logging
from typing import Optional

from ..adapters import DoSyncAdapter
from ..models import ActionResult, DeviceAction, Urgency
from . import failure_reason

log = logging.getLogger("dosync.adapters.matter")


# ── Manifest helper ───────────────────────────────────────────────────────────

def matter_manifest(
    device_id: str,
    device_name: str,
    entity_id: str,
    device_type: str = "light",
    tags: Optional[list[str]] = None,
    room: str = "",
    emergency_capable: bool = False,
    location: str = "",
):
    """
    Build a CapabilityManifest for a Matter device.

    Args:
        device_id:        unique identifier (e.g. "matter-light-01")
        device_name:      display name
        entity_id:        the entity_id in Home Assistant (e.g. "light.matter_bulb")
        device_type:      "light" | "switch" | "cover" | "lock" | "climate" (Matter/HA device types)
        tags:             additional tags
        location:         location of the device, a path such as "building-b/floor-3" (protocol 0.5)
        room:             deprecated alias of location
        emergency_capable: whether the device may act in an emergency
    """
    from ..models import (
        ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
        SensorSpec, Urgency, normalize_location,
    )

    # Tag vocabulary: no vendor name ("matter"), canonical "plug" not
    # "smart-plug", no imprecise "climate"/"door". Per TAG-VOCABULARY.md.
    base_tags = []
    type_tags = {
        "light":   ["light"],
        "switch":  ["appliance", "plug"],
        "cover":   ["blinds"],
        "lock":    ["lock", "security", "emergency"],
        "climate": ["thermostat"],
    }
    base_tags.extend(type_tags.get(device_type, ["appliance"]))
    if tags:
        base_tags.extend(tags)
    # `room` is the deprecated alias of `location` (protocol 0.5, spec §10.5): it
    # used to be appended to the tags, where a place cannot be told apart from
    # a category. Both now set the manifest's location field.
    if room and not location:
        import logging as _logging
        _logging.getLogger("dosync.adapters").warning(
            "`room=` is deprecated since protocol 0.5; pass `location=` instead")
    location = normalize_location(location or room)

    actuators_map = {
        "light":   [("turn_on", "Turn on"), ("turn_off", "Turn off"),
                    ("set_brightness", "Brightness"), ("set_color", "Colour")],
        "switch":  [("turn_on", "Turn on"), ("turn_off", "Turn off")],
        "cover":   [("open", "Open"), ("close", "Close"),
                    ("set_position", "Position 0-100%")],
        "lock":    [("lock", "Lock"), ("unlock", "Unlock")],
        "climate": [("set_temperature", "Target temperature"), ("turn_on", "Turn on"),
                    ("turn_off", "Turn off")],
    }
    actuators = [
        ActuatorSpec(id=f"{device_id}-{a[0]}", type=a[0], description=a[1])
        for a in actuators_map.get(device_type, actuators_map["switch"])
    ]

    return CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer="Matter",
        model=f"Matter {device_type.capitalize()}",
        firmware="unknown",
        category=DeviceCategory.ACTUATOR,
        tags=list(set(base_tags)),
        sensors=[],
        actuators=actuators,
        events=[],
        emergency_capable=emergency_capable,
        cert_tier=CertTier.BASIC,
        location=location,
        adapter="matter",
        adapter_config={
            "entity_id": entity_id,
            "device_type": device_type,
            "mode": "ha",
        },
    )


# ── Matter via HA ─────────────────────────────────────────────────────────────

class _MatterViaHA:
    """Controls Matter devices through the Home Assistant REST API."""

    def __init__(self, ha_url: str, ha_token: str):
        self.ha_url = ha_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {ha_token}",
            "Content-Type": "application/json",
        }
        self.timeout = 10

    def call_service(self, domain: str, service: str,
                     entity_id: str, data: Optional[dict] = None) -> dict:
        import requests
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        url = f"{self.ha_url}/api/services/{domain}/{service}"
        r = requests.post(url, json=payload, headers=self.headers,
                          timeout=self.timeout, verify=False)
        r.raise_for_status()
        return {"status": "ok", "service": f"{domain}.{service}",
                "entity_id": entity_id}

    def get_state(self, entity_id: str) -> dict:
        import requests
        url = f"{self.ha_url}/api/states/{entity_id}"
        r = requests.get(url, headers=self.headers,
                         timeout=self.timeout, verify=False)
        r.raise_for_status()
        return r.json()


# ── Adapter ───────────────────────────────────────────────────────────────────

class MatterAdapter(DoSyncAdapter):
    """
    DoSync adapter for Matter devices.

    Modo actual: via Home Assistant bridge (v0.2).
    Modo futuro: via python-matter-server standalone (v0.3).
    """

    def __init__(self, ha_url: Optional[str] = None,
                 ha_token: Optional[str] = None, hub=None):
        import os
        self._ha_url   = ha_url or os.environ.get("HA_URL", "http://localhost:8123")
        self._ha_token = ha_token or os.environ.get("HA_TOKEN", "")
        self._hub      = hub
        self._client: Optional[_MatterViaHA] = None
        if self._ha_token:
            self._client = _MatterViaHA(self._ha_url, self._ha_token)

    @property
    def adapter_name(self) -> str:
        return "matter"

    async def get_state(self, device_id: str) -> dict | None:
        """Query current Matter device state via HA REST API."""
        if not self._client or not self._hub:
            return None
        device = self._hub.registry.get(device_id)
        if not device or not device.adapter_config:
            return None
        entity_id = device.adapter_config.get("entity_id")
        if not entity_id:
            return None
        try:
            import asyncio as _asyncio
            raw = await _asyncio.get_running_loop().run_in_executor(
                None, lambda: self._client.get_state(entity_id)
            )
            state_str = raw.get("state", "")
            attrs     = raw.get("attributes", {})
            on = state_str not in ("off", "unavailable", "unknown", "0")
            result = {"on": on, "state": state_str}
            if "brightness" in attrs:
                result["brightness"] = round(attrs["brightness"] / 255 * 100)
            if "current_temperature" in attrs:
                result["current_temperature"] = attrs["current_temperature"]
            return result
        except Exception as e:
            log.debug("MatterAdapter get_state %s: %s", device_id, e)
            return None

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        config = action.params.get("_adapter_config", {})
        entity_id = config.get("entity_id")
        device_type = config.get("device_type", "light")

        if not entity_id:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                response=None,
                error="Missing entity_id in adapter_config",
            )

        if not self._client:
            log.warning("Matter adapter: no HA client — simulating %s on %s",
                        action.action, action.device_id)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response={"simulated": True, "entity_id": entity_id},
                error=None,
            )

        try:
            import asyncio
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self._call_ha(device_type, action, entity_id)
            )
            log.info("Matter %s — %s OK", action.device_id, action.action)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response=response,
                error=None,
            )
        except Exception as e:
            log.error("Matter error %s: %s", action.device_id, e)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                response=None,
                error=failure_reason(e),
            )

    def _call_ha(self, device_type: str,
                 action: DeviceAction, entity_id: str) -> dict:
        """Translate a DoSync action into an HA service call."""
        a = action.action
        p = action.params

        if device_type == "light":
            if a == "turn_on":
                data = {}
                if "brightness" in p:
                    data["brightness_pct"] = int(p["brightness"])
                if "red" in p:
                    data["rgb_color"] = [p["red"], p["green"], p["blue"]]
                return self._client.call_service("light", "turn_on",
                                                  entity_id, data)
            elif a == "turn_off":
                return self._client.call_service("light", "turn_off", entity_id)
            elif a == "set_brightness":
                return self._client.call_service(
                    "light", "turn_on", entity_id,
                    {"brightness_pct": int(p.get("brightness", 100))}
                )

        elif device_type == "switch":
            if a == "turn_on":
                return self._client.call_service("switch", "turn_on", entity_id)
            elif a == "turn_off":
                return self._client.call_service("switch", "turn_off", entity_id)

        elif device_type == "lock":
            if a == "unlock":
                return self._client.call_service("lock", "unlock", entity_id)
            elif a == "lock":
                return self._client.call_service("lock", "lock", entity_id)

        elif device_type == "cover":
            if a == "open":
                return self._client.call_service("cover", "open_cover", entity_id)
            elif a == "close":
                return self._client.call_service("cover", "close_cover", entity_id)
            elif a == "set_position":
                return self._client.call_service(
                    "cover", "set_cover_position", entity_id,
                    {"position": int(p.get("position", 50))}
                )

        elif device_type == "climate":
            if a == "set_temperature":
                return self._client.call_service(
                    "climate", "set_temperature", entity_id,
                    {"temperature": float(p.get("temperature", 21))}
                )
            elif a == "turn_on":
                return self._client.call_service("climate", "turn_on", entity_id)
            elif a == "turn_off":
                return self._client.call_service("climate", "turn_off", entity_id)

        return {"status": "unknown_action", "action": a}
