"""Human confirmation is a per-action hold, not a whole-plan block (P3, G9, spec §6.8 rule 9).

The operator marks actuator types (optionally on named devices) that need a
person. The hub withholds ONLY those actions and runs the rest of the plan; a
held action waits until a human confirms or denies it with the operator
credential, or the hold expires. An emergency no longer bypasses confirmation
wholesale: a device's DECLARED emergency action still flows, unless the policy
set even_in_emergency. This replaces the pre-2026-10-09 RequireConfirmationPolicy,
which stopped the whole intent and offered no way to confirm.
"""
import asyncio

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.policies import PolicyEngine, RequireConfirmationPolicy
from dosync.models import (ActionPlan, ActuatorSpec, CapabilityManifest, DeviceAction,
                           DeviceCategory, Intent, IntentClass, Urgency)


def _dev(device_id, acts, emergency=False, emergency_actions=None):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts], emergency_capable=emergency,
        emergency_actions=emergency_actions or [])


def _hub(policies, *devices):
    hub = DoSyncHub(db_path=":memory:")
    engine = PolicyEngine()
    for p in policies:
        engine.add(p)
    hub.policy_engine = engine
    hub.default_executor = SimulatedExecutor()
    for d in devices:
        hub.register_device(d)
    return hub


def _fire(hub, actions, urgency=Urgency.INFO, iid="x", cls="operate_device"):
    plan = ActionPlan(intent_id=iid, urgency=urgency,
                      actions=[DeviceAction(device_id=d, action=a) for d, a in actions])
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass(cls), urgency=urgency, context={})
    intent.intent_id = iid
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def _ran(result):
    return {(r.device_id, r.action) for r in result.results}


def _held(result):
    return {(h["device_id"], h["action"]) for h in result.held_for_confirmation}


def test_only_the_marked_action_is_held_the_rest_runs():
    hub = _hub([RequireConfirmationPolicy(["turn_off"])],
               _dev("lamp", ["turn_on", "turn_off"]), _dev("plug", ["turn_off"]))
    r = _fire(hub, [("lamp", "turn_on"), ("plug", "turn_off")], iid="i1")
    assert ("lamp", "turn_on") in _ran(r), "an unmarked action runs"
    assert ("plug", "turn_off") in _held(r), "a marked action is held"
    assert ("plug", "turn_off") not in _ran(r), "a held action does not run"
    assert r.status == "partial" and not r.success


def test_everything_held_yields_status_held():
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    r = _fire(hub, [("cell", "unlock")], iid="i2")
    assert not r.results and r.status == "held"


def test_confirm_runs_the_held_action():
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    _fire(hub, [("cell", "unlock")], iid="i3")
    res = asyncio.run(hub.confirm_intent("i3", SimulatedExecutor()))
    assert res["status"] == "confirmed"
    assert any(x["device_id"] == "cell" and x["action"] == "unlock" for x in res["results"])
    # consumed: a second confirm finds nothing
    assert asyncio.run(hub.confirm_intent("i3", SimulatedExecutor()))["status"] == "not_found"


def test_deny_drops_the_held_action():
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    _fire(hub, [("cell", "unlock")], iid="i4")
    assert hub.deny_intent("i4")["status"] == "denied"
    assert hub.deny_intent("i4")["status"] == "not_found"
    assert not any(e.get("type") == "confirmation_confirmed" for e in hub.audit_log.entries())
    assert any(e.get("type") == "confirmation_denied" for e in hub.audit_log.entries())


def test_hold_expires(monkeypatch):
    monkeypatch.setenv("DOSYNC_CONFIRMATION_TIMEOUT", "0")
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    _fire(hub, [("cell", "unlock")], iid="i5")
    import time
    time.sleep(0.01)
    res = asyncio.run(hub.confirm_intent("i5", SimulatedExecutor()))
    assert res["status"] == "expired"
    assert any(e.get("type") == "confirmation_expired" for e in hub.audit_log.entries())


def test_emergency_flows_a_declared_emergency_action():
    hub = _hub([RequireConfirmationPolicy(["unlock"])],
               _dev("cell", ["lock", "unlock"], emergency=True,
                    emergency_actions=[{"action": "unlock", "params": {}}]))
    r = _fire(hub, [("cell", "unlock")], urgency=Urgency.EMERGENCY, iid="i6", cls="ensure_safety")
    assert ("cell", "unlock") in _ran(r) and not r.held_for_confirmation, \
        "a declared emergency action flows without confirmation in an emergency"


def test_even_in_emergency_holds_a_declared_emergency_action():
    hub = _hub([RequireConfirmationPolicy(["unlock"], even_in_emergency=True)],
               _dev("cell", ["lock", "unlock"], emergency=True,
                    emergency_actions=[{"action": "unlock", "params": {}}]))
    r = _fire(hub, [("cell", "unlock")], urgency=Urgency.EMERGENCY, iid="i7", cls="ensure_safety")
    assert ("cell", "unlock") in _held(r), "even_in_emergency holds even a declared emergency action"


def test_emergency_holds_a_non_declared_marked_action():
    # The injected false-emergency defence (M5): an emergency does NOT bypass
    # confirmation for an action that is not the device's declared emergency action.
    hub = _hub([RequireConfirmationPolicy(["turn_off"])],
               _dev("compressor", ["turn_on", "turn_off"], emergency=True,
                    emergency_actions=[{"action": "turn_on", "params": {}}]))
    r = _fire(hub, [("compressor", "turn_off")], urgency=Urgency.EMERGENCY, iid="i8", cls="ensure_safety")
    assert ("compressor", "turn_off") in _held(r)


def test_device_ids_scope_the_hold():
    hub = _hub([RequireConfirmationPolicy(["turn_off"], device_ids=["plantroom"])],
               _dev("plantroom", ["turn_off"]), _dev("desk", ["turn_off"]))
    r = _fire(hub, [("plantroom", "turn_off"), ("desk", "turn_off")], iid="i9")
    assert ("desk", "turn_off") in _ran(r), "a device not in device_ids runs"
    assert ("plantroom", "turn_off") in _held(r), "a device in device_ids is held"


def test_held_action_leaves_an_audit_entry():
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    _fire(hub, [("cell", "unlock")], iid="i10")
    assert any(e.get("type") == "actions_held_for_confirmation" for e in hub.audit_log.entries())


def test_no_confirmation_policy_holds_nothing():
    hub = _hub([], _dev("cell", ["lock", "unlock"]))
    r = _fire(hub, [("cell", "unlock")], iid="i11")
    assert not r.held_for_confirmation and r.status == "success"


def test_confirmation_holds_on_the_governed_direct_path():
    # The hold must apply to proposed_actions too, not only resolution.
    hub = _hub([RequireConfirmationPolicy(["unlock"])], _dev("cell", ["lock", "unlock"]))
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": "cell", "action": "unlock"}]})
    intent.intent_id = "gd1"
    r = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert ("cell", "unlock") in _held(r), "a held action on the governed-direct path"
