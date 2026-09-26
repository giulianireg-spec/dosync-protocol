"""Where a device is: an operator-owned, hierarchical location that survives.

The protocol could restrict an intent to a place, but an operator had no safe,
audited way to say where a device IS. Re-registering wiped the address the API
hides; PATCH's `room` was stored nowhere and read by nothing; a restart dropped
most of what a device declared. And a single exact-match string fit a house but
not a plant (site/area/line/cell), a store, a building or a spacecraft.

This covers: the manifest's `location` path and its containment rule, one
serializer inverse so nothing is lost on restart, PATCH as the operator's tool
(persisted, audited), registration that never erases what it did not send,
adoption, and the emergency rule -- a restricting emergency keeps its zone, and
an emergency is never refused for an unknown location.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from dosync.hub import CapabilityMatchingResolver, CapabilityRegistry, DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
                           ContextSignalType, DeviceCategory, EventSpec, Intent,
                           IntentClass, SensorSpec, Severity, Urgency, VerifyBinding,
                           location_contains, normalize_location)


# ── the manifest survives: one inverse of to_dict ──────────────────────────

def _everything():
    """A manifest with every field away from its default."""
    return CapabilityManifest(
        device_id="rover-7", device_name="Rover 7", manufacturer="Acme", model="R7",
        firmware="3.1", category=DeviceCategory.ACTUATOR, tags=["rover", "science"],
        sensors=[SensorSpec(id="battery", type="percent", description="b", unit="%",
                            range=[0, 100], poll_interval_ms=5000, kind="device_state")],
        actuators=[ActuatorSpec(
            id="drive", type="drive", description="d",
            params_schema={"type": "object", "properties": {"m": {"type": "number"}}},
            execution_model="long_running", supports_progress=True, supports_cancel=True,
            emits_telemetry=True,
            verify_with=VerifyBinding(sensor_id="odometer", expected_reading=10, deadline_s=30.0))],
        events=[EventSpec(id="stuck", severity=Severity.WARNING, description="s")],
        context_signals=[ContextSignal(type=list(ContextSignalType)[0], description="c",
                                       confidence_weight=0.5)],
        emergency_capable=True, cert_tier=CertTier.STANDARD, dosync_version="0.4",
        adapter="mavlink", adapter_config={"url": "udp://x"},
        discovery_evidence={"nt": "urn:x"}, provenance={"drafted_by": "operator"},
        location="mars-base/garage/bay-2")


def test_from_dict_is_the_exact_inverse_of_to_dict():
    m = _everything()
    assert CapabilityManifest.from_dict(m.to_dict()) == m, \
        "a field written by to_dict() is not read back by from_dict()"


def test_a_restart_keeps_everything_a_device_declared():
    path = os.path.join(tempfile.mkdtemp(), "restart.db")
    DoSyncHub(db_path=path).register_device(_everything())
    assert DoSyncHub(db_path=path).registry.get("rover-7") == _everything()


# ── the location path ──────────────────────────────────────────────────────

def test_containment_is_by_segment_not_by_string_prefix():
    assert location_contains("plant-1/line-3", "plant-1/line-3/cell-2")
    assert location_contains("plant-1/line-3", "plant-1/line-3")
    assert not location_contains("plant-1/line-3", "plant-1/line-30"), \
        "line-3 took in line-30: a string prefix, not a segment"
    assert not location_contains("plant-1/line-3", "plant-1"), \
        "a place above the area was treated as inside it"


@pytest.mark.parametrize("path", ["kitchen", "plant-1/line-3/cell-2",
                                  "edificio-b/piso-4/sala-412", "iss/us-lab/rack-4",
                                  "倉庫/棚-3"])
def test_any_language_and_any_scheme_is_a_valid_location(path):
    assert normalize_location(f"  {path}  ") == path


@pytest.mark.parametrize("bad", ["/plant-1", "plant-1/", "plant-1//cell", "a/ b",
                                 "a\x07b", "x" * 257])
def test_malformed_locations_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_location(bad)


# ── the resolver ───────────────────────────────────────────────────────────

def _dev(did, location="", tags=("stop",), emergency=False):
    m = CapabilityManifest(device_id=did, device_name=did, manufacturer="t", model="t",
                           firmware="1", category=DeviceCategory.ACTUATOR, tags=list(tags),
                           emergency_capable=emergency,
                           actuators=[ActuatorSpec(id="stop", type="stop", description="")])
    m.location = location
    return m


PLANT = [_dev("cell-2", "plant-1/line-3/cell-2", emergency=True),
         _dev("cell-9", "plant-1/line-3/cell-9", emergency=True),
         _dev("line-30", "plant-1/line-30/cell-1", emergency=True),
         _dev("plant-breaker", "plant-1", emergency=True)]
STOP = {"tags": ["stop"], "actuators": ["stop"], "location_role": "restricts"}


def _acting(devices, resolution, context, urgency=Urgency.INFO):
    reg = CapabilityRegistry()
    for d in devices:
        reg.register(d)
    r = CapabilityMatchingResolver(reg)
    r._get_resolution = lambda intent: dict(resolution)
    plan = r.resolve(Intent(intent=IntentClass("stop_line"), urgency=urgency, context=context))
    return {a.device_id for a in plan.actions}


def test_a_location_takes_in_everything_below_it_and_nothing_above():
    assert _acting(PLANT, STOP, {"location": "plant-1/line-3"}) == {"cell-2", "cell-9"}


def test_an_emergency_stop_stays_in_its_zone():
    # Every device here is emergency_capable: the force-inclusion must respect
    # the zone too, or a line-3 stop would trip line 30 and the plant breaker.
    assert _acting(PLANT, STOP, {"location": "plant-1/line-3"},
                   urgency=Urgency.EMERGENCY) == {"cell-2", "cell-9"}


def test_an_emergency_at_an_unknown_location_acts_everywhere_rather_than_nowhere():
    assert _acting(PLANT, STOP, {"location": "plant-1/line-99"},
                   urgency=Urgency.EMERGENCY) == {"cell-2", "cell-9", "line-30", "plant-breaker"}


def test_a_location_written_as_a_tag_still_counts_exactly():
    legacy = [_dev("old-lamp", tags=("stop", "kitchen")), _dev("other", tags=("stop", "hall"))]
    assert _acting(legacy, STOP, {"location": "kitchen"}) == {"old-lamp"}


# ── the operator's tool, registration, adoption, the hub's checks ──────────

def _client():
    import dosync.server as srv
    return TestClient(srv.app), srv


def _register(client, did, **extra):
    body = {"device_id": did, "device_name": did, "manufacturer": "t", "model": "t",
            "firmware": "1", "category": "actuator", "tags": ["light"],
            "actuators": [{"id": "turn_on", "type": "turn_on", "description": ""}]}
    body.update(extra)
    r = client.post("/v1/devices/register", json=body)
    assert r.status_code == 200, r.text


def test_patch_sets_persists_and_audits_a_location():
    client, srv = _client()
    _register(client, "loc-lamp-1")
    r = client.patch("/v1/devices/loc-lamp-1", json={"location": "building-b/floor-4"})
    assert r.status_code == 200 and r.json()["location"] == "building-b/floor-4"
    assert srv.hub.registry.get("loc-lamp-1").location == "building-b/floor-4"
    saved = [d for d in srv.hub.db.load_devices() if d["device_id"] == "loc-lamp-1"][0]
    assert saved["location"] == "building-b/floor-4", "the location was not persisted"
    moves = [e for e in srv.hub.audit_log.entries()
             if e.get("type") == "device_relocated" and e.get("device_id") == "loc-lamp-1"]
    assert moves and moves[-1]["location"] == "building-b/floor-4"


def test_patch_accepts_room_as_an_alias_and_validates():
    client, srv = _client()
    _register(client, "loc-lamp-2")
    assert client.patch("/v1/devices/loc-lamp-2", json={"room": "kitchen"}).json()["location"] == "kitchen"
    assert client.patch("/v1/devices/loc-lamp-2", json={"location": "/bad"}).status_code == 422
    assert client.patch("/v1/devices/loc-lamp-2", json={}).status_code == 422
    assert client.patch("/v1/devices/loc-lamp-2", json={"device_name": "Lamp"}).status_code == 200


def test_reregistering_never_erases_what_it_did_not_send():
    client, srv = _client()
    _register(client, "loc-lamp-3", adapter="wiz", adapter_config={"ip": "10.0.0.9"})
    client.patch("/v1/devices/loc-lamp-3", json={"location": "main-bedroom"})
    _register(client, "loc-lamp-3", adapter="wiz", tags=["light", "energy"])
    d = srv.hub.registry.get("loc-lamp-3")
    assert d.adapter_config.get("ip") == "10.0.0.9", "re-registering wiped the address"
    assert d.location == "main-bedroom", "re-registering erased the operator's location"
    assert d.tags == ["light", "energy"], "what the device did send was not applied"


def test_a_changed_adapter_does_not_inherit_the_old_address():
    client, srv = _client()
    _register(client, "loc-lamp-4", adapter="wiz", adapter_config={"ip": "10.0.0.9"})
    _register(client, "loc-lamp-4", adapter="shelly")
    assert "ip" not in srv.hub.registry.get("loc-lamp-4").adapter_config


def test_registration_accepts_what_a_device_declares_about_its_actions():
    client, srv = _client()
    _register(client, "loc-drone", actuators=[{
        "id": "take_off", "type": "take_off", "description": "",
        "execution_model": "long_running", "emits_telemetry": True, "supports_cancel": True,
        "verify_with": {"sensor_id": "altitude", "expected_reading": 10}}])
    a = srv.hub.registry.get("loc-drone").actuators[0]
    assert (a.execution_model, a.emits_telemetry, a.supports_cancel) == ("long_running", True, True)
    assert isinstance(a.verify_with, VerifyBinding) and a.verify_with.sensor_id == "altitude"


def test_adoption_stores_the_location_in_the_field_not_as_a_tag():
    client, srv = _client()
    r = client.post("/v1/discovery/adopt", json={"device_id": "loc-printer", "device_name": "P",
                                                "location": "store-12/back-office"})
    assert r.status_code == 200, r.text
    d = srv.hub.registry.get("loc-printer")
    assert d.location == "store-12/back-office" and "store-12/back-office" not in d.tags


def test_the_hub_knows_a_place_by_what_is_below_it():
    client, srv = _client()
    _register(client, "loc-lamp-5")
    client.patch("/v1/devices/loc-lamp-5", json={"location": "hq/floor-9/room-901"})
    client.post("/v1/intent-classes", json={"name": "light_zone_loc", "urgency": "info",
                                            "resolution_tags": ["light"],
                                            "resolution_actuators": ["turn_on"]})
    ok = client.post("/v1/intent/async", json={"intent": "light_zone_loc", "urgency": "info",
                                               "context": {"location": "hq/floor-9"}})
    assert ok.status_code == 200, "a floor that holds a room with a device was refused as unknown"


def test_an_emergency_at_an_unknown_location_is_accepted_and_recorded():
    client, srv = _client()
    client.post("/v1/intent-classes", json={"name": "stop_zone_loc", "urgency": "emergency",
                                            "resolution_tags": ["light"],
                                            "resolution_actuators": ["turn_on"]})
    before = client.get("/v1/status").json()["emergency_location_fallbacks"]
    r = client.post("/v1/intent/async", json={"intent": "stop_zone_loc", "urgency": "emergency",
                                              "context": {"location": "nowhere/at/all"}})
    assert r.status_code == 200, "an emergency was refused"
    assert r.json()["location_not_found"] == "nowhere/at/all"
    assert client.get("/v1/status").json()["emergency_location_fallbacks"] == before + 1
    assert any(e.get("type") == "emergency_location_not_found" and e.get("location") == "nowhere/at/all"
               for e in srv.hub.audit_log.entries())


def test_a_leading_slash_is_named_in_the_error():
    # "/plant-1" would also fail the empty-segment check, with a message about a
    # segment the operator cannot see; the error says what is actually wrong.
    with pytest.raises(ValueError, match="leading or trailing '/'"):
        normalize_location("/plant-1")
