"""DEVICE-HEALTH-ACTIVE (b) — device-initiated heartbeat (2026-07-21).

The hub tracks device health from execution (passive) and an optional periodic
probe (active pull). A heartbeat is the active PUSH signal, for devices the hub
cannot poll — behind NAT, sleeping, inbound-blocked. Positive signal only: it
marks reachable and stamps last_heartbeat; it never marks a device unreachable.
"""
import time

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                           SensorSpec)


def _hub_with_device():
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id="sensor-remote-04", device_name="Remote", manufacturer="t",
        model="t", firmware="1", category=DeviceCategory.SENSOR, tags=["sensor"],
        sensors=[SensorSpec("temp", "temperature", "t")], events=[], actuators=[],
        emergency_capable=False, cert_tier=CertTier.BASIC))
    return hub


def test_heartbeat_marks_reachable_and_stamps_time():
    hub = _hub_with_device()
    hub.health.record_heartbeat("sensor-remote-04")
    snap = hub.health.snapshot("sensor-remote-04")
    assert snap["reachable"] is True
    assert snap["last_heartbeat"] is not None
    assert snap["last_seen"] == snap["last_heartbeat"]
    assert "device-initiated heartbeat" in snap["note"]


def test_heartbeat_stores_optional_report_verbatim():
    hub = _hub_with_device()
    report = {"battery_pct": 82, "rssi": -67, "firmware": "2.1.4"}
    hub.health.record_heartbeat("sensor-remote-04", report)
    snap = hub.health.snapshot("sensor-remote-04")
    assert snap["heartbeat_report"] == report


def test_heartbeat_clears_a_stale_unreachable_mark():
    """A device that was marked unreachable by a failed action, then phones home,
    is reachable again — recovery without the hub having to act."""
    hub = _hub_with_device()
    hub.health.mark_unreachable("sensor-remote-04", ttl_seconds=300)
    assert hub.health.snapshot("sensor-remote-04")["reachable"] is False
    hub.health.record_heartbeat("sensor-remote-04")
    assert hub.health.snapshot("sensor-remote-04")["reachable"] is True


def test_heartbeat_never_marks_unreachable():
    """The asymmetry: a heartbeat is positive-only. Absence of heartbeats is not
    evidence of death — only a real action timeout marks unreachable. There is
    no code path from record_heartbeat to an unreachable verdict."""
    hub = _hub_with_device()
    hub.health.record_heartbeat("sensor-remote-04")
    snap = hub.health.snapshot("sensor-remote-04")
    assert snap["reachable"] is True
    assert snap["unreachable_since"] is None


def test_passive_and_active_signals_coexist():
    """A device confirmed by a real action reads as action-confirmed; the same
    device after a heartbeat reads as heartbeat-confirmed. Both are 'reachable',
    the note distinguishes the source."""
    hub = _hub_with_device()
    hub.health.mark_reachable("sensor-remote-04")            # passive (action)
    assert "last action" in hub.health.snapshot("sensor-remote-04")["note"]
    hub.health.record_heartbeat("sensor-remote-04")          # active (push)
    assert "heartbeat" in hub.health.snapshot("sensor-remote-04")["note"]


def test_heartbeat_endpoint_end_to_end():
    """Over the wire: known device acknowledged, unknown device 404."""
    from fastapi.testclient import TestClient
    import server as srv

    srv.hub.registry.register(CapabilityManifest(
        device_id="hb-e2e-01", device_name="E2E", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.SENSOR, tags=["sensor"],
        sensors=[], events=[], actuators=[], emergency_capable=False,
        cert_tier=CertTier.BASIC))
    client = TestClient(srv.app)

    r = client.post("/v1/heartbeat",
                    json={"device_id": "hb-e2e-01", "report": {"battery_pct": 90}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged"
    assert body["reachable"] is True
    assert body["last_heartbeat"] is not None

    r404 = client.post("/v1/heartbeat", json={"device_id": "ghost-does-not-exist"})
    assert r404.status_code == 404
