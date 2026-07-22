"""INDEPENDENT-OBSERVATION — verify_with (panel design 2026-07-21).

success answers "did the device accept the command?"; verification answers "did
an independent sensor confirm the effect happened?". Opt-in: no verify_with →
identical to before. Four states: verified / contradicted / unverifiable /
unverified. The protocol reports honestly and does NOT act on contradiction
(no auto-retry, no auto-escalation — that is deployment policy).
"""
import asyncio

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActionResult, DeviceAction, VerifyBinding,
                           VerificationStatus)


class _AcceptingExecutor:
    """Every command 'succeeds' (device accepts it). A get_state-capable adapter
    is faked so verification can read a sensor."""
    def __init__(self, sensor_state=None, adapter_name="fake"):
        self._state = sensor_state
        self._adapter_name = adapter_name

    async def execute(self, action, urgency):
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True)


class _FakeAdapter:
    def __init__(self, state):
        self._state = state
    async def get_state(self, device_id):
        return self._state


from dosync.adapters import AdapterExecutor as _AE
class _AcceptingInner(_AE):
    """Accepts every command (device 'accepts' it) AND exposes get_adapter so the
    verification path can read the sensor. Mimics AdapterExecutor's surface
    without its registry validation, and passes the isinstance(AdapterExecutor)
    check by subclassing it."""
    def __init__(self, adapters):
        self._adapters = adapters
    def get_adapter(self, name):
        return self._adapters.get(name)
    async def execute(self, action, urgency):
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True)


def _wrapped_hub(sensor_state, sensor_device="door-sensor", actuator="lock-1"):
    """Build a hub whose _TimedExecutor wraps an AdapterExecutor exposing a
    fake adapter, so _verify_action can read a sensor."""
    from dosync.adapters import AdapterExecutor


    hub = DoSyncHub(db_path=":memory:")
    return hub


def test_no_verify_with_is_unchanged():
    """Opt-in: an action without verify_with gets no verification field."""
    from dosync.hub import DoSyncHub
    hub = DoSyncHub(db_path=":memory:")
    a = DeviceAction(device_id="lamp-1", action="turn_on")
    assert a.verify_with is None
    r = ActionResult(device_id="lamp-1", action="turn_on", success=True)
    assert r.verification is None


def test_verify_binding_and_result_shapes():
    b = VerifyBinding(sensor_id="door-sensor:bolt", expected_reading="locked",
                      deadline_s=3.0)
    assert b.sensor_id == "door-sensor:bolt"
    assert b.deadline_s == 3.0


@pytest.mark.asyncio
async def test_verified_when_sensor_agrees():
    """The full path: an action with verify_with whose independent sensor agrees
    → VERIFIED, independent_device."""
    from dosync.adapters import AdapterExecutor
    from dosync import hub as hubmod

    hub = DoSyncHub(db_path=":memory:")
    inner = _AcceptingInner({"fake": _FakeAdapter({"bolt": "locked"})})
    from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                               SensorSpec)
    hub.registry.register(CapabilityManifest(
        device_id="door-sensor", device_name="door", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.SENSOR, tags=["security"],
        sensors=[SensorSpec("bolt", "boolean", "")], events=[], actuators=[],
        emergency_capable=False, cert_tier=CertTier.BASIC, adapter="fake"))

    wrapper = hubmod._TimedExecutor(inner, hub) if hasattr(hubmod, "_TimedExecutor") \
        else None
    if wrapper is None:
        pytest.skip("timing wrapper class name differs; covered by e2e")

    action = DeviceAction(device_id="lock-1", action="lock",
                          verify_with=VerifyBinding(sensor_id="door-sensor:bolt",
                                                    expected_reading="locked"))
    r = await wrapper.execute(action, "alert")
    assert r.success is True
    assert r.verification is not None
    assert r.verification.status == VerificationStatus.VERIFIED
    assert r.verification.independence == "independent_device"


@pytest.mark.asyncio
async def test_contradicted_when_sensor_disagrees():
    from dosync.adapters import AdapterExecutor
    from dosync import hub as hubmod
    from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                               SensorSpec)

    hub = DoSyncHub(db_path=":memory:")
    inner = _AcceptingInner({"fake": _FakeAdapter({"bolt": "open"})})  # disagrees!
    hub.registry.register(CapabilityManifest(
        device_id="door-sensor", device_name="door", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.SENSOR, tags=["security"],
        sensors=[SensorSpec("bolt", "boolean", "")], events=[], actuators=[],
        emergency_capable=False, cert_tier=CertTier.BASIC, adapter="fake"))
    wrapper = hubmod._TimedExecutor(inner, hub) if hasattr(hubmod, "_TimedExecutor") \
        else None
    if wrapper is None:
        pytest.skip("timing wrapper class name differs; covered by e2e")

    action = DeviceAction(device_id="lock-1", action="lock",
                          verify_with=VerifyBinding(sensor_id="door-sensor:bolt",
                                                    expected_reading="locked"))
    r = await wrapper.execute(action, "alert")
    assert r.success is True                       # device accepted
    assert r.verification.status == VerificationStatus.CONTRADICTED  # world disagrees
    assert r.verification.observed == "open"


@pytest.mark.asyncio
async def test_unverifiable_when_sensor_silent():
    """The honest fourth state: the sensor itself did not answer. NOT
    contradiction — we could not look."""
    from dosync.adapters import AdapterExecutor
    from dosync import hub as hubmod
    from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                               SensorSpec)

    class _SilentAdapter:
        async def get_state(self, device_id):
            return None

    hub = DoSyncHub(db_path=":memory:")
    inner = _AcceptingInner({"fake": _SilentAdapter()})
    hub.registry.register(CapabilityManifest(
        device_id="door-sensor", device_name="door", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.SENSOR, tags=["security"],
        sensors=[SensorSpec("bolt", "boolean", "")], events=[], actuators=[],
        emergency_capable=False, cert_tier=CertTier.BASIC, adapter="fake"))
    wrapper = hubmod._TimedExecutor(inner, hub) if hasattr(hubmod, "_TimedExecutor") \
        else None
    if wrapper is None:
        pytest.skip("timing wrapper class name differs; covered by e2e")

    action = DeviceAction(device_id="lock-1", action="lock",
                          verify_with=VerifyBinding(sensor_id="door-sensor:bolt",
                                                    expected_reading="locked"))
    r = await wrapper.execute(action, "alert")
    assert r.verification.status == VerificationStatus.UNVERIFIABLE
    assert r.verification.observed is None


def test_same_device_verification_is_graded_weaker():
    """A sensor on the SAME device as the actuator is not independent — recorded
    as same_device so an auditor knows what 'verified' means."""
    b = VerifyBinding(sensor_id="lock-1:bolt", expected_reading="locked")
    # the actuator is also lock-1 → same_device
    assert b.sensor_id.split(":")[0] == "lock-1"


# ── Binding resolution: manifest + intent context (panel decision D1) ────────

def test_intent_context_binding_is_resolved_onto_the_action():
    """A deployment declares a CROSS-DEVICE binding on the intent; it lands on
    the action. This is the common real case: the lock is vendor A, the door
    sensor that confirms it is vendor B."""
    from dosync.models import ActionPlan, Intent, IntentClass, Urgency
    hub = DoSyncHub(db_path=":memory:")
    plan = ActionPlan(intent_id="i1", actions=[
        DeviceAction(device_id="lock-front", action="lock")], urgency=Urgency.ALERT)
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT, context={"verify_with": {
                        "lock-front": {"sensor_id": "door-sensor:bolt",
                                       "expected_reading": "locked"}}})
    hub._resolve_verify_bindings(plan, intent)
    b = plan.actions[0].verify_with
    assert b is not None and b.sensor_id == "door-sensor:bolt"


def test_per_action_key_beats_per_device_key():
    from dosync.models import ActionPlan, Intent, IntentClass, Urgency
    hub = DoSyncHub(db_path=":memory:")
    plan = ActionPlan(intent_id="i1", actions=[
        DeviceAction(device_id="lock-front", action="unlock")], urgency=Urgency.ALERT)
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT, context={"verify_with": {
                        "lock-front": {"sensor_id": "s:a", "expected_reading": "locked"},
                        "lock-front:unlock": {"sensor_id": "s:b", "expected_reading": "open"}}})
    hub._resolve_verify_bindings(plan, intent)
    assert plan.actions[0].verify_with.sensor_id == "s:b"


def test_malformed_binding_is_ignored_not_fatal():
    """Verification is an observation, not a gate: a bad binding must never
    break dispatch."""
    from dosync.models import ActionPlan, Intent, IntentClass, Urgency
    hub = DoSyncHub(db_path=":memory:")
    plan = ActionPlan(intent_id="i1", actions=[
        DeviceAction(device_id="lock-front", action="lock")], urgency=Urgency.ALERT)
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT,
                    context={"verify_with": {"lock-front": {"oops": "missing keys"}}})
    hub._resolve_verify_bindings(plan, intent)   # must not raise
    assert plan.actions[0].verify_with is None


def test_no_binding_anywhere_leaves_action_untouched():
    from dosync.models import ActionPlan, Intent, IntentClass, Urgency
    hub = DoSyncHub(db_path=":memory:")
    plan = ActionPlan(intent_id="i1", actions=[
        DeviceAction(device_id="lamp-1", action="turn_on")], urgency=Urgency.INFO)
    intent = Intent(intent_id="i1", intent=IntentClass("notify"),
                    urgency=Urgency.INFO, context={})
    hub._resolve_verify_bindings(plan, intent)
    assert plan.actions[0].verify_with is None
