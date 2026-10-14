"""Nothing that did not run goes unexplained to the caller (G6, 2026-10-14).

Found on the reference deployment: an agent asked to switch a TV's Ambilight off
was answered "Nothing ran." twice, with no reason -- the MCP tool read only the
actions that ran or were refused, and an operator policy's decision (a denied
plan, a removed device, an action held for a person) reached the audit log but
not the result. The result now carries `blocked_by` and `removed_by_policy`, and
the MCP tools say in words what held, denied or removed the action.
"""
import asyncio

import pytest

from dosync.executor import SimulatedExecutor
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)
from dosync.policies import (BlockIntentPolicy, DeviceExclusionPolicy, PolicyEngine,
                             RequireConfirmationPolicy)

TV = "ha-light-tv_ambilight"


def _hub(*policies):
    hub = DoSyncHub(db_path=":memory:")
    hub.register_device(CapabilityManifest(
        device_id=TV, device_name="TV Ambilight", manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=["light"], sensors=[], events=[],
        actuators=[ActuatorSpec("turn_on", "turn_on", ""), ActuatorSpec("turn_off", "turn_off", "")]))
    engine = PolicyEngine()
    for p in policies:
        engine.add(p)
    hub.policy_engine = engine
    return hub


def _off(hub):
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": TV, "action": "turn_off"}]})
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def test_a_denied_plan_says_which_policy_and_why():
    r = _off(_hub(BlockIntentPolicy(["operate_device"], reason="no remote control after 22:00")))
    assert not r.results
    assert r.blocked_by == {"policy": "block_intent", "reason": "no remote control after 22:00"}


def test_a_removed_device_says_which_policy_and_why():
    r = _off(_hub(DeviceExclusionPolicy(intent_classes=["operate_device"], excluded_device_ids=[TV],
                                        reason="the TV is not for agents")))
    assert not r.results
    [removed] = r.removed_by_policy
    assert (removed["device_id"], removed["action"], removed["policy"]) == (TV, "turn_off", "device_exclusion")
    assert "the TV is not for agents" in removed["reason"]


def test_the_mcp_tools_put_it_in_words():
    # Not importorskip: without the [mcp] extra, dosync.mcp_server exits at import
    # (SystemExit, which importorskip does not catch) -- the floor CI job has no mcp.
    import importlib.util
    if importlib.util.find_spec("mcp") is None:
        pytest.skip("the [mcp] extra is not installed")
    from dosync import mcp_server
    lines = mcp_server._governance_lines({
        "held_for_confirmation": [{"device_id": TV, "action": "turn_off", "reason": "a person decides"}],
        "blocked_by": {"policy": "block_intent", "reason": "not at night"},
        "removed_by_policy": [{"device_id": "x", "action": "turn_on", "policy": "device_exclusion",
                               "reason": "excluded"}]})
    text = "\n".join(lines)
    assert "held for a person" in text and "a person decides" in text
    assert "You cannot release them" in text
    assert "block_intent" in text and "not at night" in text
    assert "device_exclusion" in text and "excluded" in text
    assert mcp_server._governance_lines({}) == []


def test_the_rest_result_carries_the_reason(monkeypatch):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    hub = _hub(DeviceExclusionPolicy(intent_classes=["operate_device"], excluded_device_ids=[TV],
                                     reason="the TV is not for agents"))
    monkeypatch.setattr(srv, "hub", hub)
    monkeypatch.setattr(srv, "executor", SimulatedExecutor(failure_rate=0.0))
    with TestClient(srv.app) as c:
        srv.hub = hub
        started = c.post("/v1/intent/async", json={"intent": "operate_device", "context": {
            "proposed_actions": [{"device_id": TV, "action": "turn_off"}]}}).json()
        for _ in range(100):
            res = c.get(f"/v1/intent/{started['intent_id']}").json()
            if res.get("status") != "pending":
                break
        assert "the TV is not for agents" in res["removed_by_policy"][0]["reason"]
