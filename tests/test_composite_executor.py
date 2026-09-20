"""The composite-intent orchestrator, tested as its own unit.

The tenth extraction moved composite orchestration out of hub.py into
composite_executor.py as CompositeExecutor, taking the audit log, the database,
the policy engine and the plan executor's action-execution-model classifier as
constructor arguments. It is exercised through the hub's delegates across the
suite; here it is checked as a standalone unit -- constructible, and reading an
operation's reconciled state straight from the database (what the supervisor
polls each tick).
"""
from dosync.hub import DoSyncHub
from dosync.composite_executor import CompositeExecutor
from dosync.operations import Operation, OperationState


def test_hub_owns_a_composite_executor():
    hub = DoSyncHub(db_path=":memory:")
    assert isinstance(hub._composite_executor, CompositeExecutor)


def test_read_operation_state_reflects_the_db():
    """The supervisor polls _read_operation_state; it must return the operation's
    reconciled state from the DB, or None when the operation is unknown."""
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    op = Operation(device_id="d1", action="go", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS, reason="setup")
    hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)

    assert hub._composite_executor._read_operation_state(op.operation_id) == "in_progress"
    assert hub._composite_executor._read_operation_state("nonexistent") is None
