"""A policy that fails is not a policy that passed (H7, SAMUEL R11, 2026-10-12).

Until this date the engine skipped a policy that raised, so an operator's
restriction with a bug let the plan through without a word. Outside an
emergency the plan is now blocked with reason ``policy_error`` and the failure
is recorded; in an emergency it is recorded and does not block, because the hub
never stops an evacuation over a policy's bug.
"""
import asyncio

from dosync.executor import SimulatedExecutor
from dosync.hub import DoSyncHub
from dosync.models import (ActionPlan, ActuatorSpec, CapabilityManifest, DeviceAction,
                           DeviceCategory, Intent, IntentClass, Urgency)
from dosync.policies import PolicyEngine
from dosync.samuel import BasePolicy, PolicyDecision, PolicyResult, evaluate_policies


class Broken(BasePolicy):
    def __init__(self, bypass=True):
        self._bypass = bypass

    @property
    def name(self):
        return "broken_curfew"

    @property
    def priority(self):
        return 10

    @property
    def bypass_on_emergency(self):
        return self._bypass

    def evaluate(self, intent, plan):
        raise KeyError("blocked_hours_start")


class DropPump(BasePolicy):
    name = "drop_pump"
    priority = 30
    bypass_on_emergency = False

    def evaluate(self, intent, plan):
        kept = [a for a in plan.actions if a.device_id != "pump-2"]
        if len(kept) == len(plan.actions):
            return None
        return PolicyResult(PolicyDecision.MODIFY, self.name, "pump-2 is under maintenance", kept)


def _intent(urgency):
    return Intent(intent=IntentClass("ensure_safety"), urgency=urgency, context={})


def _plan():
    return ActionPlan(intent_id="i", urgency=Urgency.INFO, actions=[
        DeviceAction(device_id="pump-2", action="stop"),
        DeviceAction(device_id="valve-7", action="close")])


def test_outside_an_emergency_a_failing_policy_blocks():
    r = evaluate_policies([Broken()], _intent(Urgency.ALERT), _plan())
    assert r.decision == PolicyDecision.BLOCK
    assert r.reason.startswith("policy_error") and r.policy_name == "broken_curfew"
    assert r.errors and "KeyError" in r.errors[0]


def test_in_an_emergency_a_failing_policy_is_recorded_and_does_not_block():
    r = evaluate_policies([Broken(bypass=False), DropPump()], _intent(Urgency.EMERGENCY), _plan())
    assert r.decision == PolicyDecision.MODIFY, "the working absolute policy still applies"
    assert [a.device_id for a in r.modified_actions] == ["valve-7"]
    assert r.errors and "broken_curfew" in r.errors[0]


def test_unchanged_combination_rules():
    engine = PolicyEngine()
    engine.add(DropPump())
    r = engine.evaluate(_intent(Urgency.ALERT), _plan())
    assert r.decision == PolicyDecision.MODIFY and r.errors == []
    assert evaluate_policies([], _intent(Urgency.INFO), _plan()).decision == PolicyDecision.ALLOW


def test_the_hub_blocks_and_records_it():
    hub = DoSyncHub(db_path=":memory:")
    for did, acts in (("pump-2", ["start", "stop"]), ("valve-7", ["open", "close"])):
        hub.register_device(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t", firmware="1",
            category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
            actuators=[ActuatorSpec(a, a, a) for a in acts]))
    engine = PolicyEngine()
    engine.add(Broken())
    hub.policy_engine = engine
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": "valve-7", "action": "close"}]})
    result = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert not result.success and not result.results, "nothing ran"
    types = [e.get("type") for e in hub.audit_log.entries()]
    assert "policy_error" in types and "intent_blocked" in types
    err = next(e for e in hub.audit_log.entries() if e.get("type") == "policy_error")
    assert err["blocked"] is True
