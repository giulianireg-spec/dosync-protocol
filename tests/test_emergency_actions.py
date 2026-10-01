"""An emergency-capable device does, in an emergency, what was declared -- never everything.

Until protocol 0.5 rev. 2026-09-30, an emergency_capable device whose actions
matched none the intent's class needs fell back to its FULL capability set: an
emergency-capable lock received `lock` and `unlock` at once, a line shutdown
turned an extractor on. Found reviewing the paper's description of the rule.
Now the device does its declared `emergency_actions`, and a value the operator
set takes precedence over the device's own; with none declared it takes part
without acting, and the plan, explain() and the audit log say so.
"""
import pytest
from fastapi.testclient import TestClient

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency, normalize_emergency_actions)


def _lock(emergency_actions=None, device_id="em-lock-1"):
    return CapabilityManifest(
        device_id=device_id, device_name="Door", manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=["lock"], sensors=[], events=[],
        actuators=[ActuatorSpec("lock", "lock", "Lock"), ActuatorSpec("unlock", "unlock", "Unlock")],
        emergency_capable=True, emergency_actions=emergency_actions or [])


def _plan(device):
    hub = DoSyncHub(db_path=":memory:")
    hub.register_device(device)
    intent = Intent(intent=IntentClass("ensure_safety"), urgency=Urgency.EMERGENCY, context={})
    return hub.resolver.resolve(intent), hub.resolver.explain(intent)


def test_validation_refuses_what_the_device_cannot_do_and_what_undoes_itself():
    kinds = {"lock", "unlock", "stop"}
    assert normalize_emergency_actions(["unlock", {"action": "stop", "params": {"x": 1}}], kinds) == \
        [{"action": "unlock", "params": {}}, {"action": "stop", "params": {"x": 1}}]
    for bad, why in ((["fly"], "not an action"), (["lock", "lock"], "twice"),
                     (["lock", "unlock"], "undo each other")):
        with pytest.raises(ValueError, match=why):
            normalize_emergency_actions(bad, kinds)


def test_with_nothing_declared_the_device_takes_part_without_acting():
    plan, report = _plan(_lock())
    assert not [a for a in plan.actions if a.device_id == "em-lock-1"], \
        "an emergency-capable lock was sent actions nobody declared"
    assert plan.included_without_action == ["em-lock-1"]
    entry = next(d for d in report["included"] if d["device_id"] == "em-lock-1")
    assert entry.get("included_without_action") is True


def test_with_unlock_declared_the_device_unlocks_and_nothing_else():
    plan, report = _plan(_lock([{"action": "unlock", "params": {}}]))
    assert [a.action for a in plan.actions if a.device_id == "em-lock-1"] == ["unlock"]
    assert plan.included_without_action == []
    entry = next(d for d in report["included"] if d["device_id"] == "em-lock-1")
    assert entry.get("emergency_actions") == ["unlock"]


def _register(client, device_id, emergency_actions=None):
    body = {"device_id": device_id, "device_name": "Door", "manufacturer": "t", "model": "t",
            "firmware": "1", "category": "actuator", "tags": ["lock"], "emergency_capable": True,
            "actuators": [{"id": "lock", "type": "lock", "description": ""},
                          {"id": "unlock", "type": "unlock", "description": ""}]}
    if emergency_actions is not None:
        body["emergency_actions"] = emergency_actions
    return client.post("/v1/devices/register", json=body)


def test_registration_accepts_valid_emergency_actions_and_refuses_contradictory_ones():
    import dosync.server as srv
    client = TestClient(srv.app)
    assert _register(client, "em-reg-1", ["unlock"]).status_code == 200
    assert srv.hub.registry.get("em-reg-1").emergency_actions == [{"action": "unlock", "params": {}}]
    assert _register(client, "em-reg-2", ["lock", "unlock"]).status_code == 422


def test_the_operator_decides_and_a_re_registration_keeps_it():
    import dosync.server as srv
    client = TestClient(srv.app)
    assert _register(client, "em-op-1", ["lock"]).status_code == 200
    r = client.patch("/v1/devices/em-op-1", json={"emergency_actions": ["unlock"]})
    assert r.status_code == 200 and r.json()["emergency_actions"] == ["unlock"]
    assert _register(client, "em-op-1", ["lock"]).status_code == 200
    assert [a["action"] for a in srv.hub.registry.get("em-op-1").emergency_actions] == ["unlock"], \
        "the device overrode what the operator decided it does in an emergency"
    entries = [e for e in srv.hub.audit_log.entries()
               if e.get("type") == "emergency_actions_changed" and e.get("device_id") == "em-op-1"]
    assert entries and entries[-1]["emergency_actions"] == ["unlock"]
    assert client.patch("/v1/devices/em-op-1", json={"emergency_actions": ["lock", "unlock"]}).status_code == 422


def test_a_declarative_file_declares_them_and_is_validated():
    from dosync.declarative import DeclarativeError, build_manifest
    data = {"device": {"id": "em-file-1", "name": "Gate", "tags": ["lock"],
                       "emergency_capable": True, "emergency_actions": ["unlock"]},
            "transport": {"kind": "http", "base_url": "http://x"},
            "actions": {"lock": {"type": "lock", "request": {"method": "POST", "path": "/l"}},
                        "unlock": {"type": "unlock", "request": {"method": "POST", "path": "/u"}}}}
    m = build_manifest(data, source="gate.yaml")
    assert m.emergency_actions == [{"action": "unlock", "params": {}}]
    assert m.adapter_config["emergency_actions_from_file"] == "gate.yaml"
    data["device"]["emergency_actions"] = ["lock", "unlock"]
    with pytest.raises(DeclarativeError, match="undo each other"):
        build_manifest(data)


def test_the_manifest_round_trip_keeps_them():
    m = _lock([{"action": "unlock", "params": {"duration": 60}}])
    assert CapabilityManifest.from_dict(m.to_dict()).emergency_actions == m.emergency_actions
