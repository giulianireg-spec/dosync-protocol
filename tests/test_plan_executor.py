"""The plan execution engine, tested as its own unit.

The ninth extraction moved the plan-execution engine out of hub.py into
plan_executor.py as PlanExecutor, taking five hub services as constructor
arguments. It is exercised through the hub's delegates across the suite; here it
is checked as a standalone unit -- constructible, owning its own
callback-failure counter (which the hub re-exposes), and classifying actions on
its own.
"""
from dosync.hub import DoSyncHub
from dosync.plan_executor import PlanExecutor


def test_hub_owns_a_plan_executor_and_reexposes_its_counter():
    hub = DoSyncHub(db_path=":memory:")
    assert isinstance(hub._plan_executor, PlanExecutor)
    # the counter lives on the PlanExecutor; the hub surfaces it unchanged
    assert hub.progress_cb_failures == hub._plan_executor.progress_cb_failures == 0


def test_unknown_action_is_classified_instant_never_long_running():
    """An action on an unknown device defaults to instant / no-telemetry, so a
    missing manifest can never strand an operation as long-running."""
    hub = DoSyncHub(db_path=":memory:")

    class _Action:
        device_id = "not-registered"
        action = "anything"

    model, emits = hub._plan_executor._action_execution_model(_Action())
    assert model == "instant"
    assert emits is False
