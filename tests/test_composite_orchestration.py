"""
Integration tests for the hub's composite-intent orchestration
(DoSyncHub.execute_composite_intent and its dispatch/read_state/guard wiring).

Uses a REAL hub (in-memory DB, real PolicyEngine, real CompositeOperation /
RouteComposer / OperationSupervisor / OperationGuards) with a SIMULATED executor and
SIMULATED telemetry — so the full orchestration path is exercised without a drone or
sockets. Telemetry confirmation is simulated by flipping the operation's reconciled
state to `completed` in the DB the way apply_telemetry would.
"""

import asyncio

from dosync.hub import DoSyncHub
from dosync.operation_supervisor import SupervisorConfig
from dosync.operation_guards import GuardSet, GeofenceGuard
from dosync.composite_operations import CompositeState
from dosync.models import Intent, IntentClass, Urgency

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


CLAT, CLON = -31.4201, -64.1888


class _SimExecutor:
    """A simulated executor: every dispatch succeeds (unless fail_action matches).
    Records what it dispatched. Arrival confirmation is simulated separately by
    patching the hub's _read_operation_state (see _make_hub) so we don't fight the
    dispatch helper's own write-ahead transitions."""
    def __init__(self, hub, fail_action=None):
        self.hub = hub
        self.dispatched = []
        self.fail_action = fail_action  # an action name to fail, or None

    async def execute(self, action, urgency):
        self.dispatched.append(action.action)

        class _Res:
            pass
        r = _Res()
        if self.fail_action == action.action:
            r.success = False
            r.error = "simulated failure"
            return r
        r.success = True
        r.error = None
        return r


def _make_hub():
    hub = DoSyncHub(db_path=":memory:")
    # The operations table is created at server startup in production; create it here.
    hub.db.init_operations_table()
    # Attach a real PolicyEngine so admission is genuinely exercised.
    from dosync.policies import PolicyEngine
    hub.policy_engine = PolicyEngine()
    # Simulate telemetry confirming arrival: the real _read_operation_state reads the
    # reconciled DB state (which telemetry would advance to `completed`). Here, with no
    # drone, we simulate that confirmation by reporting `completed` on the second poll
    # of each operation — so the supervisor waits (proving it polls) then advances.
    _polls = {}
    real_read = hub._read_operation_state

    def fake_read(operation_id):
        # If the real state is already terminal (e.g. a failed dispatch), honor it.
        row = hub.db.get_operation(operation_id)
        if row and row.get("state") in ("failed", "rejected", "cancelled", "interrupted"):
            return row.get("state")
        _polls[operation_id] = _polls.get(operation_id, 0) + 1
        return "completed" if _polls[operation_id] >= 2 else "in_progress"

    hub._read_operation_state = fake_read
    return hub


def _intent():
    return Intent(IntentClass("inspect_area"), {}, urgency=Urgency.INFO, source="test")


def _ctx(**over):
    c = {"device_id": "drone-01", "center": (CLAT, CLON), "radius_m": 1000,
         "altitude_m": 30}
    c.update(over)
    return c


def _run(coro):
    return asyncio.run(coro)


# ── Happy path ────────────────────────────────────────────────────────────────

def test_composite_completes():
    hub = _make_hub()
    ex = _SimExecutor(hub)
    final = _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("composite intent completes", final == CompositeState.COMPLETED)


def test_dispatches_full_sequence_in_order():
    hub = _make_hub()
    ex = _SimExecutor(hub)
    _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("dispatches take_off first", ex.dispatched[0] == "take_off")
    check("dispatches return_home last", ex.dispatched[-1] == "return_home")
    check("dispatches the 4 perimeter waypoints",
          ex.dispatched.count("go_to") == 4)


def test_audit_log_records_composite_lifecycle():
    hub = _make_hub()
    ex = _SimExecutor(hub)
    _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("audit log has composite_started", "composite_started" in types)
    check("audit log has composite_finished", "composite_finished" in types)
    check("audit log records each atomic op", types.count("operation_created") >= 6)


# ── PolicyEngine admission (the critical guarantee) ───────────────────────────

def test_admission_geofence_blocks_out_of_range_step():
    # Register an admission GeofencePolicy with a SMALL radius so the perimeter
    # waypoints (1000m out) fall OUTSIDE it → the first go_to is blocked → abort.
    hub = _make_hub()
    from dosync.policies import GeofencePolicy
    hub.policy_engine.add(GeofencePolicy(CLAT, CLON, max_radius_m=100.0))
    ex = _SimExecutor(hub)
    final = _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(radius_m=1000),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("admission geofence aborts an out-of-range composite",
          final == CompositeState.ABORTED)
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("a step block is audited", "composite_step_blocked" in types)


def test_admission_allows_in_range_perimeter():
    # A generous admission radius admits the whole 1000m perimeter.
    hub = _make_hub()
    from dosync.policies import GeofencePolicy
    hub.policy_engine.add(GeofencePolicy(CLAT, CLON, max_radius_m=2000.0))
    ex = _SimExecutor(hub)
    final = _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(radius_m=1000),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("in-range perimeter passes admission and completes",
          final == CompositeState.COMPLETED)


# ── Dispatch failure ──────────────────────────────────────────────────────────

def test_step_dispatch_failure_aborts():
    hub = _make_hub()
    ex = _SimExecutor(hub, fail_action="go_to")  # waypoints fail to dispatch
    final = _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(),
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("a failed step dispatch aborts the composite",
          final == CompositeState.ABORTED)


# ── Guards wired through the hub ──────────────────────────────────────────────

def test_guard_provider_builds_without_crash():
    # The guard provider reads active operations; with guards attached, a healthy
    # run still completes (manual_control stays False, position guards abstain).
    hub = _make_hub()
    ex = _SimExecutor(hub)
    gs = GuardSet().add(GeofenceGuard(CLAT, CLON, 5000.0))  # generous; won't fire
    final = _run(hub.execute_composite_intent(
        _intent(), ex, _ctx(), guard_set=gs,
        config=SupervisorConfig(poll_interval_s=0.001, step_timeout_s=5.0)))
    check("composite with guards attached completes (guards abstain on no position)",
          final == CompositeState.COMPLETED)


def test_requires_device_id():
    hub = _make_hub()
    ex = _SimExecutor(hub)
    raised = False
    try:
        _run(hub.execute_composite_intent(_intent(), ex, {"center": (CLAT, CLON)}))
    except ValueError:
        raised = True
    check("missing device_id is rejected", raised)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} composite orchestration tests passed.")
    if _FAIL:
        raise SystemExit(1)
