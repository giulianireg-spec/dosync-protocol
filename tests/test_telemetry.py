"""The telemetry bridge, tested against its own module.

The eighth extraction moved apply_telemetry out of hub.py into telemetry.py,
taking the database and audit log as arguments. Its behaviour -- finding the
device's active operation, reconciling a fact, persisting and auditing -- is
exercised here against `dosync.telemetry` directly, not only through the hub
that delegates to it.
"""
from dosync.hub import DoSyncHub
from dosync.telemetry import apply_telemetry
from dosync.operations import Operation, OperationState
from dosync.reconciler import TelemetryEvent


def _hub_with_active_op():
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    op = Operation(device_id="drone-01", action="take_off", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS, reason="test setup")
    hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)
    return hub


def test_no_active_operation_is_ignored():
    """A stray fact for a device with no active operation is not an error."""
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    res = apply_telemetry(hub.db, hub.audit_log, "drone-01", TelemetryEvent.FINISHED)
    assert res["matched"] is False


def test_a_fact_advances_the_operation_and_is_audited():
    """A telemetry fact reconciles the active operation forward and records the
    outcome in the audit log -- the whole point of the bridge."""
    hub = _hub_with_active_op()
    before = len(hub.audit_log.entries())

    res = apply_telemetry(hub.db, hub.audit_log, "drone-01", TelemetryEvent.FINISHED)

    assert res["matched"] is True
    assert res["changed"] is True
    assert res["from_state"] == "in_progress"
    assert res["to_state"] == "completed"

    entries = hub.audit_log.entries()
    assert len(entries) == before + 1, "the outcome was not audited"
    assert entries[-1]["type"] == "operation_telemetry"
