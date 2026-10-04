"""operate_device: direct control for agents, governed by default.

Closing the direct path (rule 8) left an agent unable to "turn off the hall
light" unless the operator had registered a class granting turn_off. So a sixth
universal class grants any action a device declares, and acts only on the
actions the agent proposes: every guarantee but class authority still holds --
declared actions, place, no opposite actions, declared emergency actions first,
operator policies, audit -- and an operator who wants that bound narrows or
blocks the class by policy. The MCP per-device tool fires it.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)


def _dev(device_id, acts, location="", emergency_actions=None):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts], location=location,
        emergency_capable=bool(emergency_actions), emergency_actions=emergency_actions or [])


@pytest.fixture
def hub():
    h = DoSyncHub(db_path=":memory:")
    h.register_device(_dev("hall-light", ["turn_on", "turn_off"], location="home/hall"))
    h.register_device(_dev("garage-light", ["turn_on", "turn_off"], location="home/garage"))
    h.register_device(_dev("drone", ["goto", "land"]))
    h.register_device(_dev("conveyor", ["turn_on", "stop"], emergency_actions=[{"action": "stop"}]))
    return h


def _run(hub, proposals, urgency=Urgency.INFO, **ctx):
    plan = hub.resolver.validate_proposals(Intent(intent=IntentClass("operate_device"), urgency=urgency,
                                                  context={**ctx, "proposed_actions": proposals}))
    return ({(a.device_id, a.action) for a in plan.actions},
            {(r["device_id"], r["action"]): r["reason"] for r in plan.refused_proposals})


def test_it_is_a_universal_class(hub):
    row = hub.db.get_intent_class("operate_device")
    assert row and row["resolution_actuators"] == ["*"] and row.get("location_role") == "restricts"


def test_any_declared_action_runs_and_nothing_else(hub):
    ok, refused = _run(hub, [{"device_id": "hall-light", "action": "turn_off"},
                             {"device_id": "drone", "action": "goto", "params": {"lat": 1, "lon": 2}},
                             {"device_id": "drone", "action": "fly_away"}])
    assert ok == {("hall-light", "turn_off"), ("drone", "goto")}
    assert refused == {("drone", "fly_away"): "not_declared"}


def test_the_other_guarantees_hold(hub):
    ok, refused = _run(hub, [{"device_id": "garage-light", "action": "turn_off"}], location="home/hall")
    assert not ok and refused == {("garage-light", "turn_off"): "outside_place"}
    ok, refused = _run(hub, [{"device_id": "hall-light", "action": "turn_on"},
                             {"device_id": "hall-light", "action": "turn_off"}])
    assert not ok and set(refused.values()) == {"opposite_actions"}
    ok, refused = _run(hub, [{"device_id": "conveyor", "action": "turn_on"}], urgency=Urgency.EMERGENCY)
    assert not ok and refused == {("conveyor", "turn_on"): "not_its_emergency_action"}


def test_action_types_narrow_it(hub):
    ok, refused = _run(hub, [{"device_id": "hall-light", "action": "turn_on"}], action_types=["turn_off"])
    assert not ok and refused == {("hall-light", "turn_on"): "outside_class"}


def test_without_proposals_it_is_refused_with_its_reason():
    import dosync.server as srv
    c = TestClient(srv.app)
    r = c.post("/v1/intent/async", json={"intent": "operate_device", "urgency": "info", "context": {}})
    assert r.status_code == 422 and "proposed_actions" in r.json()["detail"]
    assert c.get("/v1/status").json()["intents_rejected"].get("proposals_required", 0) >= 1


def test_an_operator_policy_blocks_it(hub):
    from dosync.executor import SimulatedExecutor
    from dosync.policies import BlockIntentPolicy, PolicyEngine
    hub.policy_engine = PolicyEngine()
    hub.policy_engine.add(BlockIntentPolicy(intent_classes=["operate_device"], reason="no direct control here"))
    result = asyncio.run(hub.execute_intent(
        Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
               context={"proposed_actions": [{"device_id": "hall-light", "action": "turn_off"}]}),
        SimulatedExecutor()))
    assert not result.results, "a blocked operate_device ran an action"


def test_the_mcp_tool_fires_operate_device_and_never_the_direct_path(monkeypatch):
    pytest.importorskip("mcp")
    import dosync.mcp_server as m
    calls = []

    async def fake(method, path, body=None):
        calls.append((method, path, body))
        if path == "/v1/intent/async":
            return {"intent_id": "i1"}
        if path == "/v1/intent/i1":
            return {"status": "success", "results": [{"device_id": "hall-light", "action": "turn_off",
                                                      "success": True}],
                    "refused_proposals": [{"device_id": "hall-light", "action": "x", "reason": "not_declared"}]}
        return {}
    monkeypatch.setattr(m, "hub_request", fake)
    monkeypatch.setattr(m, "POLL_INTERVAL", 0)
    out = asyncio.run(m.call_tool("dosync_control_device", {"device_id": "hall-light", "action": "turn_off"}))
    text = out[0].text
    fired = [c for c in calls if c[1] == "/v1/intent/async"][0][2]
    assert fired["intent"] == "operate_device"
    assert fired["context"]["proposed_actions"] == [{"device_id": "hall-light", "action": "turn_off", "params": {}}]
    assert not any(c[1] == "/v1/device/action" for c in calls), "the tool used the ungoverned path"
    assert "✅ hall-light" in text and "refused by the hub (not_declared)" in text
