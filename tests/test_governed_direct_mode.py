"""Governed direct mode: the agent proposes the actions, the hub guarantees them.

The agent comparison of 2026-10-01 found an AI agent choosing devices itself
selected as well as the resolver and made fewer unsafe choices -- but nothing
guaranteed it would. In this mode (spec §6 rule 7) the agent sends
`context.proposed_actions`; the hub checks each against the protocol's
guarantees and refuses, with a reason, any that fails. The rest goes through
parameter validation, the operator's policies, execution and the audit log.
"""
import pytest
from fastapi.testclient import TestClient

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, SensorSpec, Urgency)


def _dev(device_id, acts=(), sensors=(), tags=(), emergency=False, emergency_actions=None, location=""):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=list(tags), events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts],
        sensors=[SensorSpec(id=s, type=s, description=s) for s in sensors],
        emergency_capable=emergency, emergency_actions=emergency_actions or [], location=location)


@pytest.fixture
def hub():
    h = DoSyncHub(db_path=":memory:")
    for d in (_dev("door", ["lock", "unlock"], location="plant/cell-2"),
              _dev("press", ["stop", "unlock"], emergency=True, location="plant/cell-2"),
              _dev("conveyor", ["turn_on", "stop"], emergency=True,
                   emergency_actions=[{"action": "stop"}], location="plant/cell-2"),
              _dev("compressor", ["turn_off"], location="plant/yard"),
              _dev("pager", ["notify"]),
              _dev("dust", sensors=["particulate"], location="plant/cell-2")):
        h.register_device(d)
    h.db.save_intent_class(name="equipment_off", urgency="warning", resolution_tags=["machinery"],
                           resolution_actuators=["turn_off", "stop"], location_role="restricts",
                           description="Stop equipment", domain="test")
    return h


def _validate(hub, cls, urgency, proposals, **ctx):
    plan = hub.resolver.validate_proposals(Intent(intent=IntentClass(cls), urgency=urgency,
                                                  context={**ctx, "proposed_actions": proposals}))
    return ({(a.device_id, a.action) for a in plan.actions},
            {(r["device_id"], r["action"]): r["reason"] for r in plan.refused_proposals})


@pytest.mark.parametrize("cls,urgency,proposal,ctx,reason", [
    ("control_access", Urgency.ALERT, ("ghost", "lock"), {}, "unknown_device"),
    ("control_access", Urgency.ALERT, ("compressor", "lock"), {}, "not_declared"),
    ("notify", Urgency.INFO, ("door", "unlock"), {}, "outside_class"),
    ("equipment_off", Urgency.WARNING, ("compressor", "turn_off"), {"location": "plant/cell-2"}, "outside_place"),
    ("ensure_safety", Urgency.EMERGENCY, ("conveyor", "turn_on"), {}, "not_its_emergency_action"),
    ("notify", Urgency.INFO, ("pager", "read_sensors"), {}, "not_declared"),
])
def test_each_guarantee_refuses_with_its_reason(hub, cls, urgency, proposal, ctx, reason):
    ok, refused = _validate(hub, cls, urgency, [{"device_id": proposal[0], "action": proposal[1]}], **ctx)
    assert not ok and refused == {proposal: reason}


def test_opposite_proposals_on_one_device_are_both_refused(hub):
    ok, refused = _validate(hub, "control_access", Urgency.ALERT,
                            [{"device_id": "door", "action": "lock"}, {"device_id": "door", "action": "unlock"}])
    assert not ok and set(refused.values()) == {"opposite_actions"}


def test_what_passes_is_exactly_what_was_proposed(hub):
    ok, refused = _validate(hub, "equipment_off", Urgency.WARNING,
                            [{"device_id": "conveyor", "action": "stop"},
                             {"device_id": "compressor", "action": "turn_off"}])
    assert ok == {("conveyor", "stop"), ("compressor", "turn_off")} and not refused
    ok, _ = _validate(hub, "report_status", Urgency.INFO, [{"device_id": "dust", "action": "read_sensors"}])
    assert ok == {("dust", "read_sensors")}, "a status class allows reading sensors"


def test_through_the_api_proposals_are_governed_recorded_and_reported():
    import time
    import dosync.server as srv
    with TestClient(srv.app) as c:
        srv.hub.register_device(_dev("gd-door", ["lock", "unlock"]))
        srv.hub.register_device(_dev("gd-pager", ["notify"]))
        r = c.post("/v1/intent/async", json={"intent": "control_access", "urgency": "alert", "context": {
            "proposed_actions": [{"device_id": "gd-door", "action": "unlock"},
                                 {"device_id": "gd-pager", "action": "notify"}]}})
        assert r.status_code == 200, r.text   # proposals name their direction: no ambiguity refusal
        for _ in range(100):
            result = c.get(f"/v1/intent/{r.json()['intent_id']}").json()
            if result.get("status") != "pending":
                break
            time.sleep(0.05)
    body = result.get("result", result)
    assert [x["device_id"] for x in body["results"]] == ["gd-door"]
    assert body["refused_proposals"] == [{"device_id": "gd-pager", "action": "notify",
                                          "reason": "outside_class"}]
    entry = [e for e in srv.hub.audit_log.entries() if e.get("type") == "intent_executed"][-1]
    assert entry.get("refused_proposals") == body["refused_proposals"]


def test_a_malformed_proposal_list_is_refused():
    import dosync.server as srv
    c = TestClient(srv.app)
    for bad in ([], "door:lock", [{"device_id": "x"}], [{"device_id": "x", "action": "y", "params": 3}]):
        r = c.post("/v1/intent/async", json={"intent": "notify", "urgency": "info",
                                             "context": {"proposed_actions": bad}})
        assert r.status_code == 422, bad
    assert c.get("/v1/status").json()["intents_rejected"].get("invalid_proposals", 0) >= 4


def test_what_a_plan_reports_survives_a_policy_that_rebuilds_it():
    """Parameter validation and the policy engine rebuild the plan from its
    actions; included_without_action was lost from the audit whenever a policy
    modified the plan. The hub now captures it before any step can drop it."""
    import asyncio
    from dosync.executor import SimulatedExecutor
    from dosync.policies import DeviceExclusionPolicy, PolicyEngine
    h = DoSyncHub(db_path=":memory:")
    h.register_device(_dev("lamp", ["set_color"], emergency=True))
    h.register_device(_dev("siren", ["alarm"], emergency=True))
    h.policy_engine = PolicyEngine()
    h.policy_engine.add(DeviceExclusionPolicy(intent_classes=["ensure_safety"],
                                              excluded_device_ids=["siren"],
                                              bypass_on_emergency=False))
    asyncio.run(h.execute_intent(Intent(intent=IntentClass("ensure_safety"),
                                        urgency=Urgency.EMERGENCY, context={}), SimulatedExecutor()))
    entry = [e for e in h.audit_log.entries() if e.get("type") == "intent_executed"][-1]
    assert entry.get("included_without_action") == ["lamp"]
