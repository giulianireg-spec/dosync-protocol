"""No device oscillates across an opposite pair (P1, G4 extended, spec §6.8 rule 10).

The third agent comparison (2026-10-07) found that the per-plan opposite-action
guard (rule 6) could not see an attack that spread one reversal per intent across
many intents: flashing a light, toggling a lock. This guard keeps per (device,
opposite-pair) history across intents and drops an action that would reverse a
device more than DOSYNC_OSCILLATION_MAX_REVERSALS times within a rolling window.
A single correction is legitimate and allowed; a declared emergency action is
never dropped; window 0 disables the guard.
"""
import asyncio

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.models import (ActionPlan, ActuatorSpec, CapabilityManifest, DeviceAction,
                           DeviceCategory, Intent, IntentClass, Urgency)


def _dev(device_id, acts, emergency=False, emergency_actions=None):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts], emergency_capable=emergency,
        emergency_actions=emergency_actions or [])


def _hub(*devices):
    hub = DoSyncHub(db_path=":memory:")
    for d in devices:
        hub.register_device(d)
    return hub


def _fire(hub, device_id, action, urgency=Urgency.INFO, iid="x"):
    plan = ActionPlan(intent_id=iid, actions=[DeviceAction(device_id=device_id, action=action)],
                      urgency=urgency)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("operate_device"), urgency=urgency, context={})
    intent.intent_id = iid
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def _osc(result):
    return [r for r in (result.refused_proposals or []) if r.get("reason") == "oscillation"]


def test_a_single_correction_is_allowed():
    hub = _hub(_dev("plug", ["turn_on", "turn_off"]))
    assert not _osc(_fire(hub, "plug", "turn_on", iid="a"))
    r = _fire(hub, "plug", "turn_off", iid="b")
    assert not _osc(r) and r.results, "one reversal is a legitimate correction"


def test_the_third_reversal_is_dropped():
    hub = _hub(_dev("plug", ["turn_on", "turn_off"]))
    _fire(hub, "plug", "turn_on", iid="a")
    _fire(hub, "plug", "turn_off", iid="b")
    r = _fire(hub, "plug", "turn_on", iid="c")
    assert _osc(r) == [{"device_id": "plug", "action": "turn_on", "reason": "oscillation"}]
    assert not r.results, "a dropped action must not execute"


def test_a_declared_emergency_action_is_exempt():
    hub = _hub(_dev("door", ["lock", "unlock"], emergency=True,
                    emergency_actions=[{"action": "unlock", "params": {}}]))
    _fire(hub, "door", "lock", iid="a")
    _fire(hub, "door", "unlock", iid="b")
    _fire(hub, "door", "lock", iid="c")
    r = _fire(hub, "door", "unlock", iid="d")   # declared emergency action
    assert not _osc(r), "a declared emergency action is never dropped for oscillation"


def test_window_zero_disables_the_guard(monkeypatch):
    monkeypatch.setenv("DOSYNC_OSCILLATION_WINDOW", "0")
    hub = _hub(_dev("plug", ["turn_on", "turn_off"]))
    _fire(hub, "plug", "turn_on", iid="a")
    _fire(hub, "plug", "turn_off", iid="b")
    r = _fire(hub, "plug", "turn_on", iid="c")
    assert not _osc(r), "DOSYNC_OSCILLATION_WINDOW<=0 must disable the guard"


def test_a_dropped_oscillation_is_audited():
    hub = _hub(_dev("plug", ["turn_on", "turn_off"]))
    _fire(hub, "plug", "turn_on", iid="a")
    _fire(hub, "plug", "turn_off", iid="b")
    _fire(hub, "plug", "turn_on", iid="c")
    assert any(e.get("type") == "actions_dropped_oscillation" for e in hub.audit_log.entries()), \
        "a dropped oscillation must leave an audit entry"
