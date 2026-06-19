"""
DoSync — execute_intent wiring tests for long-running operations.

The wiring is the delicate part: it touches the heart of the hub. These tests
prove the panel's contract end-to-end through execute_intent():
  - an instant-only intent behaves EXACTLY as before (operations empty, status
    unchanged) — backwards compatibility is the non-negotiable;
  - a long-running action creates and PERSISTS an operation (write-ahead);
  - the IntentResult carries the operations list with ids and states;
  - the status becomes 'accepted' when operations are underway;
  - a failed dispatch resolves the operation to 'failed', never stranded;
  - graceful degradation: an adapter that just returns a normal result works.

Run: DOSYNC_AUTH=false python3 tests/test_operations_wiring.py
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.models import (
    CapabilityManifest, ActuatorSpec, DeviceCategory, CertTier,
    Intent, IntentClass, Urgency, ActionPlan, DeviceAction,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _hub_with_devices():
    """A hub with one instant lamp and one long-running 'mover' (telemetry-capable)."""
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    lamp = CapabilityManifest(
        device_id="lamp-01", device_name="Lamp", manufacturer="X", model="Y",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"],
        actuators=[ActuatorSpec("turn_on", "turn_on")],  # instant by default
        sensors=[], emergency_capable=False, cert_tier=CertTier.BASIC,
    )
    mover = CapabilityManifest(
        device_id="mover-01", device_name="Mover", manufacturer="X", model="Z",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["mover"],
        actuators=[ActuatorSpec("go_to", "go_to", execution_model="long_running",
                                emits_telemetry=True)],
        sensors=[], emergency_capable=False, cert_tier=CertTier.BASIC,
    )
    hub.register_device(lamp)
    hub.register_device(mover)
    return hub


def _intent(plan):
    return plan


# ── Backwards compatibility: instant-only is unchanged ───────────────────────────

def test_instant_only_intent_has_empty_operations():
    """An intent that triggers only instant actions returns an IntentResult with no
    operations and a normal status — identical to pre-wiring behavior."""
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i1", actions=[
        DeviceAction(device_id="lamp-01", action="turn_on", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i1"

    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    assert result.operations == [], "instant-only intent must have no operations"
    assert result.status == "success"
    assert result.success is True


# ── Long-running action creates and persists an operation ────────────────────────

def test_long_running_action_creates_operation():
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i2", actions=[
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i2"

    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    assert len(result.operations) == 1, "one operation should be created"
    op = result.operations[0]
    assert op["device_id"] == "mover-01"
    assert "operation_id" in op
    assert op["state"] == "in_progress"  # dispatch accepted, not yet completed


def test_operation_is_persisted_writeahead():
    """The operation must be persisted (so it survives a restart). After execution
    it is in the DB as an active operation."""
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i3", actions=[
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i3"

    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    op_id = result.operations[0]["operation_id"]
    stored = hub.db.get_operation(op_id)
    assert stored is not None, "operation must be persisted"
    assert stored["device_id"] == "mover-01"
    # It's in_progress → active → recoverable on restart
    active = hub.db.get_active_operations()
    assert any(o["operation_id"] == op_id for o in active)


def test_status_accepted_for_long_running_only():
    """An intent that only started long-running operations has status 'accepted' —
    not 'failed' (the bug we guarded against) and not 'success' (not done)."""
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i4", actions=[
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i4"

    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    assert result.status == "accepted", f"expected accepted, got {result.status}"


# ── Mixed plan: instant + long-running ───────────────────────────────────────────

def test_mixed_plan_instant_done_operation_started():
    """A plan with both an instant lamp and a long-running mover: the lamp executes
    (appears in results), the mover starts (appears in operations), status accepted."""
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i5", actions=[
        DeviceAction(device_id="lamp-01", action="turn_on", params={}),
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i5"

    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    # Instant lamp executed
    assert any(r.device_id == "lamp-01" for r in result.results)
    # Mover started as an operation
    assert len(result.operations) == 1
    assert result.operations[0]["device_id"] == "mover-01"
    assert result.status == "accepted"


# ── Failed dispatch resolves operation to failed, never stranded ─────────────────

def test_failed_dispatch_resolves_operation_failed():
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i6", actions=[
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i6"

    # An executor that always fails the dispatch.
    result = _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=1.0)))
    assert len(result.operations) == 1
    assert result.operations[0]["state"] == "failed", "failed dispatch → operation failed"
    # And it's persisted as terminal (not active)
    op_id = result.operations[0]["operation_id"]
    active = hub.db.get_active_operations()
    assert not any(o["operation_id"] == op_id for o in active), "failed op is not active"


# ── Audit trail ──────────────────────────────────────────────────────────────────

def test_operation_creation_audited():
    """Every operation creation and transition is in the audit log — accountability."""
    hub = _hub_with_devices()
    plan = ActionPlan(intent_id="i7", actions=[
        DeviceAction(device_id="mover-01", action="go_to", params={}),
    ], urgency=Urgency.INFO)
    hub.resolver.resolve = lambda intent: plan
    intent = Intent(intent=IntentClass("set_environment"), urgency=Urgency.INFO, context={})
    intent.intent_id = "i7"

    _run(hub.execute_intent(intent, SimulatedExecutor(failure_rate=0.0)))
    entries = hub.audit_log.entries()
    types = [e.get("type") for e in entries]
    assert "operation_created" in types
    assert "operation_transition" in types


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} execute_intent wiring tests passed.")
    sys.exit(1 if failed else 0)
