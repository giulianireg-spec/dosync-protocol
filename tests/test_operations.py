"""
DoSync — Long-running operation state machine tests.

This is the foundation of Carril 6 (robotics). The whole drone effort rests on
this state machine being correct, so it is tested exhaustively as pure logic —
no hardware, no MAVLink, no network. Every panel-identified scenario is covered:
the core happy path, rejection by vehicle state, human interruption (which does
NOT resume), vehicle self-pause, the telemetry gate that protects simple devices,
illegal transitions, and time-in-state.

Run: python3 tests/test_operations.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.operations import (
    Operation, OperationState, InvalidTransition,
    TERMINAL_STATES, TELEMETRY_ONLY_STATES,
)


# ── Core happy path ────────────────────────────────────────────────────────────

def test_core_happy_path():
    """pending -> in_progress -> completed (the minimal core every device has)."""
    op = Operation(device_id="oven-01", action="preheat")
    assert op.state == OperationState.PENDING
    op.transition_to(OperationState.IN_PROGRESS, reason="confirmed started")
    assert op.state == OperationState.IN_PROGRESS
    op.transition_to(OperationState.COMPLETED, reason="telemetry: reached temp")
    assert op.state == OperationState.COMPLETED
    assert op.is_terminal


def test_core_failure_path():
    """pending -> in_progress -> failed."""
    op = Operation(device_id="oven-01", action="preheat")
    op.transition_to(OperationState.IN_PROGRESS)
    op.transition_to(OperationState.FAILED, reason="element burned out")
    assert op.state == OperationState.FAILED
    assert op.is_terminal


# ── First-class added states ────────────────────────────────────────────────────

def test_rejected_only_from_pending():
    """A device can refuse by physical state before starting — pending -> rejected."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.REJECTED, reason="not armed / no GPS fix")
    assert op.state == OperationState.REJECTED
    assert op.is_terminal


def test_rejected_not_allowed_after_in_progress():
    """'rejected' means 'never started' — illegal once in progress."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS)
    try:
        op.transition_to(OperationState.REJECTED)
        assert False, "should not be able to reject an already-in-progress operation"
    except InvalidTransition:
        pass


def test_interrupted_is_normal_not_failure():
    """Human intervention (pilot takes the sticks) is a normal outcome, distinct
    from failure."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS)
    op.transition_to(OperationState.INTERRUPTED, reason="pilot took manual control")
    assert op.state == OperationState.INTERRUPTED
    assert op.is_terminal
    assert op.state != OperationState.FAILED  # explicitly not a failure


def test_interrupted_does_not_resume():
    """An interrupted operation is terminal — it cannot go back to in_progress.
    If the system retakes control, that must be a NEW operation."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS)
    op.transition_to(OperationState.INTERRUPTED, reason="pilot took control")
    try:
        op.transition_to(OperationState.IN_PROGRESS)
        assert False, "interrupted operation must not resume"
    except InvalidTransition as e:
        assert "does not resume" in str(e) or "terminal" in str(e)


# ── Telemetry-gated sub-states ───────────────────────────────────────────────────

def test_preparing_phase_for_richer_device():
    """A richer device exposes a preparing phase before the main action. The
    generic state is `preparing`; the device-specific sub-phase lives in `phase`.
    Here, a camera focusing before it records."""
    op = Operation(device_id="camera-01", action="record", telemetry_capable=True)
    op.transition_to(OperationState.PREPARING, reason="focusing")
    op.phase = "focusing"
    op.transition_to(OperationState.IN_PROGRESS, reason="recording")
    op.transition_to(OperationState.COMPLETED, reason="telemetry: finished")
    assert op.state == OperationState.COMPLETED


def test_aerial_phase_detail_in_phase_field():
    """The aerial domain specializes `preparing` via the phase field — arming then
    taking_off — without those being protocol-level states."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.PREPARING, reason="arming motors")
    op.phase = "arming"
    assert op.state == OperationState.PREPARING
    op.phase = "taking_off"  # still PREPARING, sub-phase advances
    assert op.state == OperationState.PREPARING
    op.transition_to(OperationState.IN_PROGRESS, reason="navigating")
    assert op.state == OperationState.IN_PROGRESS


def test_preparing_fails_before_main_action():
    """An operation can fail during preparing, before the main action starts."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.PREPARING)
    op.transition_to(OperationState.FAILED, reason="preparing refused: not ready")
    assert op.state == OperationState.FAILED


def test_paused_by_device_then_resume():
    """The device pauses itself (a speaker losing its stream) and later resumes on
    its own. Distinct from failed, interrupted, or in_progress."""
    op = Operation(device_id="speaker-01", action="play_album", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS)
    op.transition_to(OperationState.PAUSED_BY_DEVICE, reason="stream dropped")
    assert op.state == OperationState.PAUSED_BY_DEVICE
    assert not op.is_terminal  # paused is not terminal
    op.transition_to(OperationState.IN_PROGRESS, reason="stream restored")
    assert op.state == OperationState.IN_PROGRESS


def test_telemetry_only_state_blocked_for_core_device():
    """A core-only device (no telemetry) must never be driven into a telemetry
    sub-state. This protects the dumb-body principle: a simple oven can't be
    asked to report an arming phase it has no concept of."""
    op = Operation(device_id="oven-01", action="preheat", telemetry_capable=False)
    assert not op.can_transition_to(OperationState.PREPARING)
    try:
        op.transition_to(OperationState.PREPARING)
        assert False, "core-only device must not enter a telemetry-only state"
    except InvalidTransition as e:
        assert "telemetry" in str(e).lower()


def test_paused_by_device_blocked_for_core_device():
    op = Operation(device_id="oven-01", action="preheat", telemetry_capable=False)
    op.transition_to(OperationState.IN_PROGRESS)
    assert not op.can_transition_to(OperationState.PAUSED_BY_DEVICE)


# ── Reconciliation after restart ─────────────────────────────────────────────────

def test_reconciling_resolves_to_outcome():
    """After a hub restart, an operation can be placed in 'reconciling' and then
    resolved against telemetry — here, the failsafe brought the drone home so the
    operation is interrupted rather than assumed complete."""
    op = Operation(device_id="drone-01", action="go_to",
                   state=OperationState.RECONCILING, telemetry_capable=True)
    assert op.state == OperationState.RECONCILING
    op.transition_to(OperationState.INTERRUPTED,
                     reason="telemetry after restart: failsafe RTL had triggered")
    assert op.state == OperationState.INTERRUPTED


def test_can_enter_reconciling_from_active_states():
    """A recovered operation must be able to ENTER reconciling from whatever active
    state it was in when the hub went down — not just exist there from creation."""
    for active in (OperationState.PENDING, OperationState.PREPARING,
                   OperationState.IN_PROGRESS,
                   OperationState.PAUSED_BY_DEVICE):
        op = Operation(device_id="drone-01", action="go_to",
                       state=active, telemetry_capable=True)
        assert op.can_transition_to(OperationState.RECONCILING), \
            f"must be able to reconcile from {active.value}"
        op.transition_to(OperationState.RECONCILING, reason="hub restart")
        assert op.state == OperationState.RECONCILING


# ── Illegal transitions are rejected, never silently accepted ────────────────────

def test_illegal_skip_rejected():
    """pending -> completed directly is illegal: silence is not success, you can't
    jump to completed without confirmation of being in progress."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    try:
        op.transition_to(OperationState.COMPLETED)
        assert False, "must not jump pending -> completed"
    except InvalidTransition:
        pass


def test_no_transition_out_of_terminal():
    """Every terminal state has no outgoing transitions."""
    for term in TERMINAL_STATES:
        op = Operation(device_id="d", action="a", state=term, telemetry_capable=True)
        assert op.is_terminal
        # Pick any other state and confirm it's refused.
        target = (OperationState.IN_PROGRESS if term != OperationState.IN_PROGRESS
                  else OperationState.COMPLETED)
        try:
            op.transition_to(target)
            assert False, f"terminal {term.value} must not transition"
        except InvalidTransition:
            pass


def test_cancelled_terminal():
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS)
    op.transition_to(OperationState.CANCELLED, reason="operator cancelled")
    assert op.is_terminal


# ── Time-in-state and history ────────────────────────────────────────────────────

def test_time_in_state_uses_injected_clock():
    """time_in_state is first-class data for the Policy Engine. Verify it reflects
    the entry timestamp using an injected clock (no real waiting)."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True,
                   created_at=1000.0, state_entered_at=1000.0)
    op.transition_to(OperationState.IN_PROGRESS, now=1000.0)
    op.transition_to(OperationState.PAUSED_BY_DEVICE, reason="wind", now=1005.0)
    # 60 seconds later, still paused
    assert op.time_in_state(now=1065.0) == 60.0


def test_history_records_every_transition():
    """The full transition history is the audit trail. Every move is recorded,
    including the initial creation entry."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.PREPARING, reason="arming")
    op.transition_to(OperationState.IN_PROGRESS, reason="navigating")
    op.transition_to(OperationState.INTERRUPTED, reason="pilot took control")
    # creation + 3 transitions = 4 entries
    assert len(op.history) == 4
    assert op.history[0].from_state is None  # creation
    assert op.history[0].to_state == OperationState.PENDING
    assert op.history[-1].to_state == OperationState.INTERRUPTED
    assert op.history[-1].reason == "pilot took control"


def test_to_dict_roundtrip_shape():
    """Serialization includes state, timing, telemetry flag, and full history."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS, reason="started")
    d = op.to_dict()
    assert d["device_id"] == "drone-01"
    assert d["state"] == "in_progress"
    assert d["telemetry_capable"] is True
    assert len(d["history"]) == 2
    assert d["history"][0]["from_state"] is None
    assert d["history"][1]["to_state"] == "in_progress"


def test_can_transition_to_is_nondestructive():
    """can_transition_to answers without changing state."""
    op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
    assert op.can_transition_to(OperationState.IN_PROGRESS) is True
    assert op.can_transition_to(OperationState.COMPLETED) is False
    assert op.state == OperationState.PENDING  # unchanged


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
    print(f"\n{passed}/{passed + failed} operation state machine tests passed.")
    sys.exit(1 if failed else 0)
