"""A plan never undoes itself, and in an emergency what was declared comes first.

Found by the agent comparison of 2026-10-01: asked to "secure access to the
plant", the hub's control_access plan sent the cell door lock AND unlock, and
unlocked a press; asked to keep people safe in a plant fire, its ensure_safety
plan turned a conveyor ON (the class asks for turn_on, meant for lights). The
agent choosing devices itself did neither. Device-level metrics could not see
either defect: the right devices acted, with the wrong actions.
"""
import pytest
from fastapi.testclient import TestClient

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency, opposite_pairs)


def _dev(device_id, acts, tags=(), emergency=False, emergency_actions=None):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=list(tags), sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts], emergency_capable=emergency,
        emergency_actions=emergency_actions or [])


def _hub(*devices):
    hub = DoSyncHub(db_path=":memory:")
    for d in devices:
        hub.register_device(d)
    return hub


def _plan(hub, cls, urgency, **ctx):
    return hub.resolver.resolve(Intent(intent=IntentClass(cls), urgency=urgency, context=ctx))


def test_opposite_pairs():
    assert opposite_pairs(["lock", "unlock", "notify"]) == [("lock", "unlock")]
    assert opposite_pairs(["turn_on", "set_brightness"]) == []


def test_a_plan_never_sends_a_device_two_opposite_actions():
    hub = _hub(_dev("door", ["lock", "unlock"], tags=["lock"]))
    for urgency in (Urgency.ALERT, Urgency.EMERGENCY):
        plan = _plan(hub, "control_access", urgency)
        acts = [a.action for a in plan.actions if a.device_id == "door"]
        assert not opposite_pairs(acts), f"{urgency}: the door received {acts}"
        assert "door" in plan.included_without_action


def test_an_intent_narrows_its_class_to_the_actions_it_means():
    hub = _hub(_dev("door", ["lock", "unlock"], tags=["lock"]), _dev("press", ["unlock", "stop"]))
    plan = _plan(hub, "control_access", Urgency.ALERT, action_types=["lock"])
    assert sorted((a.device_id, a.action) for a in plan.actions) == [("door", "lock")], \
        "securing access unlocked something"


def test_the_hub_refuses_an_ambiguous_intent_and_says_how_to_fix_it():
    import dosync.server as srv
    c = TestClient(srv.app)
    r = c.post("/v1/intent/async", json={"intent": "control_access", "urgency": "alert", "context": {}})
    assert r.status_code == 422 and "action_types" in r.json()["detail"] and "lock/unlock" in r.json()["detail"]
    ok = c.post("/v1/intent/async", json={"intent": "control_access", "urgency": "alert",
                                          "context": {"action_types": ["lock"]}})
    assert ok.status_code == 200
    bad = c.post("/v1/intent/async", json={"intent": "control_access", "urgency": "alert",
                                           "context": {"action_types": ["fly"]}})
    assert bad.status_code == 422
    assert srv.app is not None
    status = c.get("/v1/status").json()
    assert status["intents_rejected"].get("ambiguous_actions", 0) >= 1


def test_an_emergency_is_never_refused_for_ambiguity():
    import dosync.server as srv
    r = TestClient(srv.app).post("/v1/intent/async", json={"intent": "control_access",
                                                            "urgency": "emergency", "context": {}})
    assert r.status_code == 200


def test_in_an_emergency_declared_actions_take_precedence_over_the_class():
    conveyor = _dev("conveyor", ["turn_on", "stop"], emergency=True,
                    emergency_actions=[{"action": "stop", "params": {}}])
    plan = _plan(_hub(conveyor), "ensure_safety", Urgency.EMERGENCY)
    assert [a.action for a in plan.actions if a.device_id == "conveyor"] == ["stop"], \
        "a conveyor declared to stop in an emergency was turned on"


def test_explain_warns_when_an_emergency_device_declares_nothing():
    conveyor = _dev("conveyor", ["turn_on", "stop"], emergency=True)
    hub = _hub(conveyor)
    report = hub.resolver.explain(Intent(intent=IntentClass("ensure_safety"),
                                         urgency=Urgency.EMERGENCY, context={}))
    entry = next(d for d in report["included"] if d["device_id"] == "conveyor")
    assert "declare what it must do" in entry.get("warning", "")
