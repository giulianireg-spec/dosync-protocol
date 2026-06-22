"""
Tests for dosync/composite_operations.py — the high-level operation that
coordinates a sequence of atomic operations (the molecule above the atom).

Pure logic, fully offline: no drone, no socket, no hub.
"""

from dosync.composite_operations import (
    CompositeOperation, CompositeStep, CompositeState,
    COMPOSITE_TERMINAL_STATES, InvalidCompositeTransition,
)

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  \u2713  {name}")
    else:
        _FAIL += 1
        print(f"  \u2717  {name}")


def _perimeter_steps():
    return [
        CompositeStep("drone-01", "take_off", {"alt": 30}, kind="takeoff"),
        CompositeStep("drone-01", "go_to", {"lat": -31.41, "lon": -64.18}, kind="waypoint"),
        CompositeStep("drone-01", "go_to", {"lat": -31.43, "lon": -64.19}, kind="waypoint"),
        CompositeStep("drone-01", "return_home", {}, kind="return"),
    ]


def _comp():
    return CompositeOperation(
        device_id="drone-01", intent="inspect_area", steps=_perimeter_steps(),
        context={"geofence_center": (-31.42, -64.185), "geofence_radius_m": 1000},
    )


# ── Construction ──────────────────────────────────────────────────────────────

def test_starts_in_planning():
    c = _comp()
    check("starts in planning", c.state == CompositeState.PLANNING)


def test_initial_history_recorded():
    c = _comp()
    check("initial history has one entry", len(c.history) == 1)
    check("initial entry from_state is None", c.history[0].from_state is None)


def test_current_step_is_first():
    c = _comp()
    check("current step is the takeoff", c.current_step.kind == "takeoff")
    check("current index starts at 0", c.current_index == 0)


def test_generic_not_drone_named():
    # The type is generic; the drone-ness lives only in the intent and the steps.
    c = CompositeOperation(device_id="arm-01", intent="pick_and_place",
                           steps=[CompositeStep("arm-01", "reach", kind="step")])
    check("composite serves a non-drone device", c.intent == "pick_and_place")


# ── State machine ─────────────────────────────────────────────────────────────

def test_happy_path_transitions():
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT)
    c.transition_to(CompositeState.RETURNING)
    c.transition_to(CompositeState.COMPLETED)
    check("reaches completed via the happy path", c.state == CompositeState.COMPLETED)
    check("completed is terminal", c.is_terminal)


def test_illegal_transition_rejected():
    c = _comp()
    raised = False
    try:
        c.transition_to(CompositeState.RETURNING)  # planning -> returning is illegal
    except InvalidCompositeTransition:
        raised = True
    check("planning -> returning rejected", raised)


def test_terminal_does_not_resume():
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT)
    c.transition_to(CompositeState.ABORTED)
    raised = False
    try:
        c.transition_to(CompositeState.IN_TRANSIT)
    except InvalidCompositeTransition:
        raised = True
    check("aborted does not resume", raised)


def test_abort_from_in_transit():
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT)
    c.transition_to(CompositeState.ABORTED, reason="geofence breach in flight")
    check("can abort mid-transit", c.state == CompositeState.ABORTED)
    check("aborted is terminal", c.is_terminal)


def test_planning_can_abort_before_takeoff():
    c = _comp()
    c.transition_to(CompositeState.ABORTED, reason="rejected before takeoff")
    check("can abort from planning", c.state == CompositeState.ABORTED)


def test_returning_can_be_interrupted():
    # A human taking control during the return leg aborts the composite.
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT)
    c.transition_to(CompositeState.RETURNING)
    c.transition_to(CompositeState.ABORTED, reason="manual control during return")
    check("return leg can abort", c.state == CompositeState.ABORTED)


def test_all_terminal_states_have_no_exits():
    for st in COMPOSITE_TERMINAL_STATES:
        c = _comp()
        # force into the terminal state via a minimal legal path
        if st == CompositeState.COMPLETED:
            c.transition_to(CompositeState.IN_TRANSIT)
            c.transition_to(CompositeState.RETURNING)
            c.transition_to(CompositeState.COMPLETED)
        elif st == CompositeState.ABORTED:
            c.transition_to(CompositeState.ABORTED)
        elif st == CompositeState.FAILED:
            c.transition_to(CompositeState.FAILED)
        check(f"{st.value} is terminal", c.is_terminal)


# ── Step coordination ─────────────────────────────────────────────────────────

def test_advance_moves_cursor_and_marks_done():
    c = _comp()
    first = c.current_step
    nxt = c.advance()
    check("advance marks previous step done", first.done is True)
    check("advance moves to next step", nxt.kind == "waypoint")
    check("index incremented", c.current_index == 1)


def test_advance_past_end_returns_none():
    c = _comp()
    for _ in range(len(c.steps)):
        c.advance()
    check("advancing past the end returns None", c.current_step is None)
    check("all steps done", c.all_steps_done)


def test_remaining_steps_for_abort():
    c = _comp()
    c.advance()  # takeoff done
    remaining = c.remaining_steps
    check("remaining excludes completed steps", len(remaining) == len(c.steps) - 1)
    check("remaining starts at current", remaining[0].kind == "waypoint")


def test_step_holds_operation_id():
    # The composite references atomic ops by id for hierarchical traceability.
    c = _comp()
    c.current_step.operation_id = "op_abc123"
    check("step can reference its atomic operation_id",
          c.steps[0].operation_id == "op_abc123")


# ── Persistence ───────────────────────────────────────────────────────────────

def test_roundtrip_preserves_everything():
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT, reason="takeoff dispatched")
    c.advance()
    c.current_step.operation_id = "op_xyz"
    d = c.to_dict()
    c2 = CompositeOperation.from_dict(d)
    check("roundtrip preserves state", c2.state == c.state)
    check("roundtrip preserves state_entered_at", c2.state_entered_at == c.state_entered_at)
    check("roundtrip preserves history length", len(c2.history) == len(c.history))
    check("roundtrip preserves current_index", c2.current_index == c.current_index)
    check("roundtrip preserves steps", c2.steps[1].operation_id == "op_xyz")
    check("roundtrip preserves context", c2.context["geofence_radius_m"] == 1000)
    check("roundtrip preserves step done flags", c2.steps[0].done is True)


def test_from_dict_does_not_reset_time():
    # The critical fidelity guarantee: from_dict must not reset state_entered_at to
    # now (which would destroy time_in_state, the guard's stall signal).
    import time
    c = _comp()
    c.transition_to(CompositeState.IN_TRANSIT)
    old_entered = c.state_entered_at - 500  # pretend it entered 500s ago
    c.state_entered_at = old_entered
    d = c.to_dict()
    c2 = CompositeOperation.from_dict(d)
    check("from_dict preserves an old state_entered_at",
          abs(c2.state_entered_at - old_entered) < 0.001)
    check("time_in_state reflects the real elapsed, not 0",
          c2.time_in_state() > 400)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{_PASS}/{_PASS + _FAIL} composite operation tests passed.")
    if _FAIL:
        raise SystemExit(1)
