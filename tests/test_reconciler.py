"""
DoSync — Operation reconciler tests (execution_model, Layer 3).

The reconciler turns abstract telemetry facts into state transitions, with one
rule: telemetry wins, and silence is never success. These tests cover the six
lifecycle scenarios the expert panel identified, plus the messy realities of real
telemetry: duplicate packets, out-of-order packets, and stale packets arriving
after an operation already finished. All pure logic — no hardware, no MAVLink.

Run: python3 tests/test_reconciler.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.operations import Operation, OperationState
from dosync.reconciler import OperationReconciler, TelemetryEvent


def _drone_op() -> Operation:
    return Operation(device_id="drone-01", action="go_to", telemetry_capable=True)


def _oven_op() -> Operation:
    return Operation(device_id="oven-01", action="preheat", telemetry_capable=False)


# ── The six panel scenarios ──────────────────────────────────────────────────────

def test_scenario_finished_completes():
    """Happy path: started → finished. FINISHED is the only path to completed."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.FINISHED)
    assert res.changed
    assert op.state == OperationState.COMPLETED


def test_scenario_failed_midway():
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.FAILED, reason="motor fault")
    assert res.changed
    assert op.state == OperationState.FAILED


def test_scenario_rejected_by_vehicle():
    """The vehicle refuses before starting — distinct from failing."""
    r = OperationReconciler()
    op = _drone_op()
    res = r.reconcile(op, TelemetryEvent.REJECTED_BY_DEVICE, reason="no GPS fix")
    assert res.changed
    assert op.state == OperationState.REJECTED


def test_scenario_cancelled():
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.CANCELLED, reason="operator request")
    assert res.changed
    assert op.state == OperationState.CANCELLED


def test_scenario_interrupted_by_human():
    """A pilot takes the sticks — interrupted, not failed, and terminal."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.MANUAL_CONTROL_TAKEN)
    assert res.changed
    assert op.state == OperationState.INTERRUPTED
    assert op.is_terminal


def test_scenario_device_paused_then_resumed():
    """The vehicle pauses itself (wind) and later resumes on its own."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res1 = r.reconcile(op, TelemetryEvent.DEVICE_PAUSED, reason="high wind")
    assert res1.changed
    assert op.state == OperationState.PAUSED_BY_DEVICE
    res2 = r.reconcile(op, TelemetryEvent.DEVICE_RESUMED, reason="wind eased")
    assert res2.changed
    assert op.state == OperationState.IN_PROGRESS


# ── Full arming/takeoff sequence (telemetry profile) ─────────────────────────────

def test_full_lifecycle_sequence():
    """A richer device's full path: preparing → in_progress → completed. The aerial
    sub-phases (arming, taking_off) live in the operation's phase field, not as
    separate telemetry events or protocol states."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.PREPARING)
    assert op.state == OperationState.PREPARING
    op.phase = "arming"  # domain sub-phase detail
    r.reconcile(op, TelemetryEvent.STARTED)
    assert op.state == OperationState.IN_PROGRESS
    r.reconcile(op, TelemetryEvent.FINISHED)
    assert op.state == OperationState.COMPLETED


# ── Silence is not success ────────────────────────────────────────────────────────

def test_silence_does_not_complete():
    """No telemetry = no progress. An operation with only a 'started' signal must
    NOT drift to completed on its own — there is no path that does so."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    # ... time passes, no further telemetry ...
    assert op.state == OperationState.IN_PROGRESS
    assert op.state != OperationState.COMPLETED


# ── Noisy / out-of-order / stale telemetry ───────────────────────────────────────

def test_duplicate_telemetry_is_noop():
    """A repeated 'started' while already in progress is harmless, not an error."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.STARTED)
    assert not res.changed
    assert "already in" in res.note
    assert op.state == OperationState.IN_PROGRESS


def test_stale_telemetry_after_terminal_ignored():
    """A late packet arriving after the operation finished must not revive or
    change it. Telemetry wins, but a terminal operation is settled."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    r.reconcile(op, TelemetryEvent.MANUAL_CONTROL_TAKEN)  # interrupted (terminal)
    res = r.reconcile(op, TelemetryEvent.FINISHED)  # stale "arrived" arrives late
    assert not res.changed
    assert op.state == OperationState.INTERRUPTED  # unchanged
    assert "terminal" in res.note


def test_illegal_telemetry_is_noop_not_crash():
    """A fact implying an illegal jump (e.g. 'preparing' while already in_progress)
    is a no-op with a note — it must never crash the hub."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)  # in_progress
    res = r.reconcile(op, TelemetryEvent.PREPARING)  # preparing illegal from in_progress
    assert not res.changed
    assert op.state == OperationState.IN_PROGRESS
    assert "illegal" in res.note


# ── Restart reconciliation ───────────────────────────────────────────────────────

def test_reconcile_after_restart_enters_reconciling():
    """On restart, a recovered telemetry-capable operation enters RECONCILING and
    waits for telemetry to resolve it — never assumes it's still where it was."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)  # was in_progress when hub went down
    res = r.reconcile_after_restart(op)
    assert res.changed
    assert op.state == OperationState.RECONCILING


def test_reconcile_after_restart_resolved_by_telemetry():
    """After entering RECONCILING, the next real telemetry fact resolves it — here
    the failsafe had brought the drone home, so it resolves to interrupted, NOT
    completed. The hub learns reality from telemetry rather than assuming success."""
    r = OperationReconciler()
    op = _drone_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    r.reconcile_after_restart(op)  # now RECONCILING
    res = r.reconcile(op, TelemetryEvent.MANUAL_CONTROL_TAKEN,
                      reason="failsafe RTL had triggered during outage")
    assert res.changed
    assert op.state == OperationState.INTERRUPTED


def test_reconcile_after_restart_core_device_noop():
    """A core-only device (no telemetry) has nothing to reconcile — left as-is."""
    r = OperationReconciler()
    op = _oven_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile_after_restart(op)
    assert not res.changed
    assert op.state == OperationState.IN_PROGRESS
    assert "core-only" in res.note


# ── Core device runs on the minimal vocabulary ───────────────────────────────────

def test_oven_core_lifecycle():
    """An oven (no telemetry) runs the core path with the same reconciler: it never
    touches a telemetry-only state."""
    r = OperationReconciler()
    op = _oven_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    assert op.state == OperationState.IN_PROGRESS
    res = r.reconcile(op, TelemetryEvent.FINISHED, reason="reached temp")
    assert op.state == OperationState.COMPLETED
    assert res.changed


def test_oven_cannot_be_driven_into_telemetry_state():
    """Even if a (wrong) telemetry-only fact is fed for a core device, the gate in
    the state machine refuses it — the reconciler surfaces a no-op, not a crash."""
    r = OperationReconciler()
    op = _oven_op()
    r.reconcile(op, TelemetryEvent.STARTED)
    res = r.reconcile(op, TelemetryEvent.DEVICE_PAUSED)
    assert not res.changed
    assert op.state == OperationState.IN_PROGRESS


# ── Result object carries audit detail ───────────────────────────────────────────

def test_result_carries_transition_detail():
    """The ReconcileResult exposes from/to/note so the hub can persist and audit."""
    r = OperationReconciler()
    op = _drone_op()
    res = r.reconcile(op, TelemetryEvent.STARTED, reason="confirmed underway")
    assert res.changed
    assert res.from_state == OperationState.PENDING
    assert res.to_state == OperationState.IN_PROGRESS
    assert res.note == "confirmed underway"


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
    print(f"\n{passed}/{passed + failed} reconciler tests passed.")
    sys.exit(1 if failed else 0)
