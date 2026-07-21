"""DEVICE-HEALTH-ACTIVE (c) — powered-off vs network-unreachable (2026-07-21).

For a UDP device, a command timeout is identical whether the device lost power
or lost wifi — the transport cannot tell. So (c) does not guess from the timeout;
it cross-references the independent heartbeat signal and returns a verdict
CALIBRATED WITH ITS EVIDENCE, honest about indeterminacy. Panel #2 (Delgado):
the operator needs to know whether there's something to go fix.
"""
import time

from dosync.hub import DoSyncHub
from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                           SensorSpec)


def _hub_with_device(did="wiz-1"):
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id=did, device_name=did, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=["light"], sensors=[], events=[],
        actuators=[], emergency_capable=False, cert_tier=CertTier.BASIC))
    return hub


def test_reachable_device_needs_no_attribution():
    hub = _hub_with_device()
    hub.health.mark_reachable("wiz-1")
    a = hub.health.reachability_assessment("wiz-1")
    assert a["cause"] == "reachable"


def test_recent_heartbeat_then_unresponsive_is_network_not_power():
    """A device that heartbeat'd seconds ago but ignores a command was alive just
    now — high-confidence network/app fault, not a dead bulb."""
    hub = _hub_with_device()
    now = time.time()
    hub.health.record_heartbeat("wiz-1")            # alive just now
    hub.health.mark_unreachable("wiz-1", ttl_seconds=300)  # but command timed out
    a = hub.health.reachability_assessment("wiz-1", now=now + 10)
    assert a["cause"] == "network_or_app"
    assert a["confidence"] == "high"


def test_long_silence_and_unresponsive_leans_powered_off():
    hub = _hub_with_device()
    now = time.time()
    hub.health.record_heartbeat("wiz-1")
    hub.health.mark_unreachable("wiz-1", ttl_seconds=3000)
    # far beyond the freshness window
    a = hub.health.reachability_assessment("wiz-1", now=now + 500)
    assert a["cause"] == "likely_powered_off"
    assert a["confidence"] == "medium"


def test_never_heartbeated_is_honestly_indeterminate():
    """The honest core: without an independent signal, power and network cannot
    be separated. Say so — do not invent certainty."""
    hub = _hub_with_device()
    hub.health.mark_unreachable("wiz-1", ttl_seconds=300)
    a = hub.health.reachability_assessment("wiz-1")
    assert a["cause"] == "indeterminate"
    assert a["confidence"] == "low"
    assert "transport limitation" in a["evidence"]


def test_assessment_carries_its_evidence():
    """Every verdict must be explainable, not a bare label."""
    hub = _hub_with_device()
    hub.health.mark_unreachable("wiz-1", ttl_seconds=300)
    a = hub.health.reachability_assessment("wiz-1")
    assert a["evidence"] and len(a["evidence"]) > 20


def test_endpoint_exposes_assessments():
    from fastapi.testclient import TestClient
    import server as srv
    srv.hub.registry.register(CapabilityManifest(
        device_id="wiz-c-e2e", device_name="e2e", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"], sensors=[],
        events=[], actuators=[], emergency_capable=False, cert_tier=CertTier.BASIC))
    srv.hub.health.mark_unreachable("wiz-c-e2e", ttl_seconds=300)
    client = TestClient(srv.app)
    r = client.get("/v1/health/reachability")
    assert r.status_code == 200
    body = r.json()
    assert "assessments" in body
    if "wiz-c-e2e" in body["assessments"]:
        assert "cause" in body["assessments"]["wiz-c-e2e"]
