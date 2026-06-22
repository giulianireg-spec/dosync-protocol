"""
Tests for dosync/operation_supervisor.py — the closed-loop supervisor that drives a
CompositeOperation step by step, waiting for confirmed arrival and reacting to guards.

Pure logic, fully offline: dispatch, state-read, guard, sleep and clock are all
injected, so the whole loop runs deterministically with no drone, socket, or hub.
"""

import asyncio

from dosync.operation_supervisor import OperationSupervisor, SupervisorConfig
from dosync.composite_operations import CompositeOperation, CompositeStep, CompositeState

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


def _steps():
    return [
        CompositeStep("drone-01", "take_off", {"alt": 30}, kind="takeoff"),
        CompositeStep("drone-01", "go_to", {"lat": -31.41, "lon": -64.18}, kind="waypoint"),
        CompositeStep("drone-01", "go_to", {"lat": -31.43, "lon": -64.19}, kind="waypoint"),
        CompositeStep("drone-01", "return_home", {}, kind="return"),
    ]


def _comp():
    return CompositeOperation(device_id="drone-01", intent="inspect_area", steps=_steps())


async def _no_sleep(_s):
    return None


def _fast_config():
    return SupervisorConfig(poll_interval_s=0.001, step_timeout_s=1000.0)


class _World:
    """A fake world: dispatch creates an op id; each op reaches `completed` after
    `reads_to_complete` polls. Optionally force a given op into a negative state."""
    def __init__(self, reads_to_complete=1, force_negative_at=None, negative_state="failed"):
        self.reads_to_complete = reads_to_complete
        self.force_negative_at = force_negative_at  # step index whose op goes negative
        self.negative_state = negative_state
        self.dispatched = []
        self.read_counts = {}

    async def dispatch(self, step):
        oid = f"op_{len(self.dispatched)}"
        self.dispatched.append((step.action, oid, step.kind))
        self.read_counts[oid] = 0
        return oid

    def read_state(self, oid):
        self.read_counts[oid] += 1
        idx = int(oid.split("_")[1])
        if self.force_negative_at == idx and self.read_counts[oid] >= self.reads_to_complete:
            return self.negative_state
        return "completed" if self.read_counts[oid] >= self.reads_to_complete else "in_progress"


def _run(sup, comp):
    return asyncio.run(sup.run(comp))


# ── Happy path ────────────────────────────────────────────────────────────────

def test_happy_path_completes():
    w = _World(reads_to_complete=2)
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    final = _run(sup, _comp())
    check("composite completes", final == CompositeState.COMPLETED)


def test_steps_dispatched_in_order():
    w = _World()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, _comp())
    actions = [a for a, _, _ in w.dispatched]
    check("steps dispatched in order",
          actions == ["take_off", "go_to", "go_to", "return_home"])


def test_waits_for_each_arrival_before_next():
    # With reads_to_complete=3, each step must be polled 3x before the next dispatch.
    w = _World(reads_to_complete=3)
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, _comp())
    # Every op was polled at least 3 times → the supervisor waited for confirmation.
    check("each step polled until confirmed arrival",
          all(c >= 3 for c in w.read_counts.values()))


def test_mission_state_progression():
    w = _World()
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, comp)
    states = [t.to_state.value for t in comp.history]
    check("passes through in_transit", "in_transit" in states)
    check("passes through returning", "returning" in states)
    check("ends completed", states[-1] == "completed")


def test_all_steps_marked_done():
    w = _World()
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, comp)
    check("all steps done on success", comp.all_steps_done)


def test_operation_ids_recorded_on_steps():
    w = _World()
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, comp)
    check("each step recorded its atomic operation_id",
          all(s.operation_id is not None for s in comp.steps))


# ── Negative terminal (a step fails) ──────────────────────────────────────────

def test_step_failure_aborts():
    # The 2nd step (index 1) goes to "failed".
    w = _World(reads_to_complete=2, force_negative_at=1, negative_state="failed")
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    final = _run(sup, comp)
    check("a failed step aborts the composite", final == CompositeState.ABORTED)


def test_interrupted_step_aborts():
    # A human takes control during step 0 → interrupted → composite aborts.
    w = _World(reads_to_complete=2, force_negative_at=0, negative_state="interrupted")
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    final = _run(sup, comp)
    check("an interrupted step aborts the composite", final == CompositeState.ABORTED)


def test_abort_stops_dispatching_further_steps():
    w = _World(reads_to_complete=1, force_negative_at=1, negative_state="failed")
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    _run(sup, comp)
    # take_off (0) + the failing go_to (1) dispatched; the remaining steps never are.
    check("abort halts further dispatch", len(w.dispatched) == 2)


# ── Guards ────────────────────────────────────────────────────────────────────

def test_guard_before_first_step_aborts():
    w = _World()
    comp = _comp()
    guard = lambda c: "geofence breach"  # fires immediately
    sup = OperationSupervisor(w.dispatch, w.read_state, guard=guard,
                              sleep=_no_sleep, config=_fast_config())
    final = _run(sup, comp)
    check("a guard firing before dispatch aborts", final == CompositeState.ABORTED)
    check("no step dispatched when guard fires first", len(w.dispatched) == 0)


def test_guard_mid_step_aborts():
    # Guard returns None for the first N ticks, then fires — simulating a breach in
    # flight between waypoints.
    state = {"ticks": 0}
    def guard(c):
        state["ticks"] += 1
        return "battery critical" if state["ticks"] > 3 else None
    # Make the step take many polls so the guard has time to fire mid-step.
    w = _World(reads_to_complete=999)
    comp = _comp()
    sup = OperationSupervisor(w.dispatch, w.read_state, guard=guard,
                              sleep=_no_sleep, config=_fast_config())
    final = _run(sup, comp)
    check("a guard firing mid-step aborts", final == CompositeState.ABORTED)


def test_no_guard_means_no_interference():
    w = _World()
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    final = _run(sup, _comp())
    check("default (no guard) completes normally", final == CompositeState.COMPLETED)


# ── Stall (timeout backstop) ──────────────────────────────────────────────────

def test_stall_aborts():
    # A step that never reaches terminal: read_state always returns in_progress.
    async def dispatch(step):
        return "op_stuck"
    def read_state(_oid):
        return "in_progress"
    # Tiny timeout, real (but injected) monotonic clock via a counter.
    clock = {"t": 0.0}
    def now():
        clock["t"] += 0.05
        return clock["t"]
    config = SupervisorConfig(poll_interval_s=0.001, step_timeout_s=0.2)
    comp = _comp()
    sup = OperationSupervisor(dispatch, read_state, sleep=_no_sleep, config=config, now=now)
    final = _run(sup, comp)
    check("a stalled step aborts (silence never hangs forever)",
          final == CompositeState.ABORTED)


# ── Dispatch failure ──────────────────────────────────────────────────────────

def test_dispatch_exception_aborts():
    async def dispatch(step):
        raise RuntimeError("adapter exploded")
    def read_state(_oid):
        return "completed"
    comp = _comp()
    sup = OperationSupervisor(dispatch, read_state, sleep=_no_sleep, config=_fast_config())
    final = _run(sup, comp)
    check("a dispatch exception aborts cleanly", final == CompositeState.ABORTED)


# ── Guard contract ────────────────────────────────────────────────────────────

def test_supervisor_rejects_non_planning_composite():
    w = _World()
    comp = _comp()
    comp.transition_to(CompositeState.IN_TRANSIT)  # already started
    sup = OperationSupervisor(w.dispatch, w.read_state, sleep=_no_sleep, config=_fast_config())
    raised = False
    try:
        _run(sup, comp)
    except ValueError:
        raised = True
    check("supervisor rejects a non-PLANNING composite", raised)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{_PASS}/{_PASS + _FAIL} operation supervisor tests passed.")
    if _FAIL:
        raise SystemExit(1)
