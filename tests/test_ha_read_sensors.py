"""Reading a Home Assistant sensor is a state query, not a service call.

Every action was translated into POST /api/services/{domain}/{service}, and
read_sensors had no translation, so it fell through to the default ("turn_on").
Home Assistant answered 400 for an entity with no such service, and the hub read
that failure as the device not responding and excluded it for ~30 minutes. On
the reference hub two binary sensors failed on every report_status for months.
"""
import asyncio

import pytest

from dosync.adapters.homeassistant import HABridge
from dosync.hub import DoSyncHub
from dosync.models import (CapabilityManifest, CertTier, DeviceAction, DeviceCategory,
                           SensorSpec, Urgency)


def _hub_with(device_id, sensors):
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="ha", model="x",
        firmware="1", category=DeviceCategory.SENSOR, tags=["sensor"],
        sensors=sensors, events=[], actuators=[], emergency_capable=False,
        cert_tier=CertTier.BASIC, adapter="homeassistant",
        adapter_config={"entity_id": f"binary_sensor.{device_id}", "domain": "binary_sensor"}))
    return hub


def _bridge(hub, state):
    bridge = HABridge(ha_url="http://ha.local", ha_token="t", hub=hub, simulated=False)

    async def _fake_get_state(device_id):
        return state

    async def _no_session():
        raise AssertionError("read_sensors called a Home Assistant service")

    bridge.get_state = _fake_get_state
    bridge._get_session = _no_session
    return bridge


def _read(bridge, device_id, params=None):
    return asyncio.run(bridge.execute(
        DeviceAction(device_id=device_id, action="read_sensors", params=params or {}),
        Urgency.INFO))


def test_a_binary_sensor_reports_its_state_without_calling_a_service():
    hub = _hub_with("door", [SensorSpec("state", "boolean", "State")])
    result = _read(_bridge(hub, {"on": True, "state": "on"}), "door")
    assert result.success is True
    assert result.response["readings"] == {"state": True}


def test_a_numeric_sensor_reports_a_number():
    hub = _hub_with("temp", [SensorSpec("value", "float", "Sensor reading")])
    result = _read(_bridge(hub, {"on": True, "state": "23.5"}), "temp")
    assert result.response["readings"] == {"value": 23.5}


def test_an_unreadable_entity_fails_with_a_reason():
    hub = _hub_with("gone", [SensorSpec("state", "boolean", "State")])
    result = _read(_bridge(hub, None), "gone")
    assert result.success is False
    assert "Home Assistant" in result.error


def test_only_the_requested_sensors_are_reported():
    hub = _hub_with("multi", [SensorSpec("state", "boolean", "State"),
                              SensorSpec("brightness", "integer", "Brightness", unit="%")])
    result = _read(_bridge(hub, {"on": True, "state": "on", "brightness": 40}),
                   "multi", params={"sensor_ids": ["brightness"]})
    assert result.response["readings"] == {"brightness": 40}
