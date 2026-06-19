"""
DoSync — Operations persistence tests (execution_model, Layer 2).

The panel's hard requirement for long-running operations: an active operation
must survive a hub restart. Without it, a hub reboot orphans a drone still flying
toward a waypoint. These tests prove the persistence layer delivers that —
including an actual close-and-reopen of the database to simulate the restart —
and that the Operation object round-trips through the DB without loss.

Run: DOSYNC_AUTH=false python3 tests/test_operations_persistence.py
"""

import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.db import DoSyncDB
from dosync.operations import Operation, OperationState


def _fresh_db() -> tuple[DoSyncDB, str]:
    path = tempfile.mktemp(suffix=".db")
    db = DoSyncDB(path)
    db.init()
    db.init_operations_table()
    return db, path


def _save(db: DoSyncDB, op: Operation) -> None:
    db.save_operation(op.to_dict(), terminal=op.is_terminal)


# ── Round-trip ──────────────────────────────────────────────────────────────────

def test_save_and_retrieve_operation():
    db, path = _fresh_db()
    try:
        op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
        op.transition_to(OperationState.IN_PROGRESS, reason="started")
        _save(db, op)

        loaded = db.get_operation(op.operation_id)
        assert loaded is not None
        assert loaded["device_id"] == "drone-01"
        assert loaded["state"] == "in_progress"
        assert loaded["telemetry_capable"] is True
    finally:
        db.close() if hasattr(db, "close") else None
        os.path.exists(path) and os.remove(path)


def test_operation_rehydrates_into_object():
    """A stored operation can be rebuilt into a live Operation that keeps advancing
    through the state machine — the history survives too."""
    db, path = _fresh_db()
    try:
        op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
        op.transition_to(OperationState.PREPARING, reason="preparing")
        op.transition_to(OperationState.IN_PROGRESS, reason="navigating")
        _save(db, op)

        stored = db.get_operation(op.operation_id)
        # Rehydrate
        rebuilt = Operation(
            device_id=stored["device_id"],
            action=stored["action"],
            operation_id=stored["operation_id"],
            state=OperationState(stored["state"]),
            created_at=stored["created_at"],
            state_entered_at=stored["state_entered_at"],
            telemetry_capable=stored["telemetry_capable"],
        )
        assert rebuilt.operation_id == op.operation_id
        assert rebuilt.state == OperationState.IN_PROGRESS
        # The rebuilt operation can still advance correctly
        rebuilt.transition_to(OperationState.COMPLETED, reason="arrived")
        assert rebuilt.state == OperationState.COMPLETED
    finally:
        os.path.exists(path) and os.remove(path)


# ── The core requirement: survive a restart ──────────────────────────────────────

def test_active_operation_survives_restart():
    """Save an active operation, CLOSE the DB connection, reopen it (simulating a
    hub restart), and confirm the operation is recovered as active."""
    path = tempfile.mktemp(suffix=".db")
    try:
        # First "boot"
        db1 = DoSyncDB(path)
        db1.init()
        db1.init_operations_table()
        op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
        op.transition_to(OperationState.IN_PROGRESS, reason="flying to waypoint")
        db1.save_operation(op.to_dict(), terminal=op.is_terminal)
        # Hub goes down
        if hasattr(db1, "close"):
            db1.close()
        del db1

        # Second "boot" — fresh connection to the same file
        db2 = DoSyncDB(path)
        db2.init()
        db2.init_operations_table()
        active = db2.get_active_operations()
        assert len(active) == 1, "active operation must survive restart"
        assert active[0]["operation_id"] == op.operation_id
        assert active[0]["state"] == "in_progress"
        assert active[0]["device_id"] == "drone-01"
    finally:
        os.path.exists(path) and os.remove(path)


def test_terminal_operation_not_returned_as_active():
    """A completed/interrupted operation is not 'active' and must not come back in
    get_active_operations — only the still-flying ones need reconciliation."""
    db, path = _fresh_db()
    try:
        active_op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
        active_op.transition_to(OperationState.IN_PROGRESS)
        _save(db, active_op)

        done_op = Operation(device_id="drone-02", action="go_to", telemetry_capable=True)
        done_op.transition_to(OperationState.IN_PROGRESS)
        done_op.transition_to(OperationState.COMPLETED, reason="arrived")
        _save(db, done_op)

        interrupted_op = Operation(device_id="drone-03", action="go_to", telemetry_capable=True)
        interrupted_op.transition_to(OperationState.IN_PROGRESS)
        interrupted_op.transition_to(OperationState.INTERRUPTED, reason="pilot took control")
        _save(db, interrupted_op)

        active = db.get_active_operations()
        ids = {a["operation_id"] for a in active}
        assert active_op.operation_id in ids
        assert done_op.operation_id not in ids
        assert interrupted_op.operation_id not in ids
        assert len(active) == 1
    finally:
        os.path.exists(path) and os.remove(path)


def test_state_update_overwrites_in_place():
    """Saving the same operation_id updates it (INSERT OR REPLACE), not duplicates."""
    db, path = _fresh_db()
    try:
        op = Operation(device_id="drone-01", action="go_to", telemetry_capable=True)
        _save(db, op)  # pending
        op.transition_to(OperationState.IN_PROGRESS)
        _save(db, op)  # in_progress — same id
        op.transition_to(OperationState.COMPLETED, reason="done")
        _save(db, op)  # completed — same id

        # Only one row, and it's terminal now → not active
        active = db.get_active_operations()
        assert len(active) == 0
        stored = db.get_operation(op.operation_id)
        assert stored["state"] == "completed"
    finally:
        os.path.exists(path) and os.remove(path)


# ── Cleanup policy ───────────────────────────────────────────────────────────────

def test_clear_old_operations_keeps_active():
    """Cleanup removes old TERMINAL operations but NEVER active ones — an old but
    still-alive operation is exactly what must be preserved."""
    db, path = _fresh_db()
    try:
        # An active operation with an ancient created_at
        old_active = Operation(device_id="drone-01", action="go_to",
                               telemetry_capable=True, created_at=1.0,
                               state_entered_at=1.0)
        old_active.transition_to(OperationState.IN_PROGRESS, now=1.0)
        db.save_operation(old_active.to_dict(), terminal=False)

        # A terminal operation, also old
        old_done = Operation(device_id="drone-02", action="go_to",
                            telemetry_capable=True, created_at=1.0,
                            state_entered_at=1.0)
        old_done.transition_to(OperationState.IN_PROGRESS, now=1.0)
        old_done.transition_to(OperationState.COMPLETED, now=1.0, reason="done")
        db.save_operation(old_done.to_dict(), terminal=True)
        # Force its updated_at into the deep past via a direct write
        db._conn.execute("UPDATE operations SET updated_at=1.0 WHERE operation_id=?",
                         (old_done.operation_id,))
        db._conn.commit()

        removed = db.clear_old_operations(max_age_hours=24)
        assert removed == 1  # only the terminal one
        # The active one is still there
        active = db.get_active_operations()
        assert len(active) == 1
        assert active[0]["operation_id"] == old_active.operation_id
    finally:
        os.path.exists(path) and os.remove(path)


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
    print(f"\n{passed}/{passed + failed} operations persistence tests passed.")
    sys.exit(1 if failed else 0)
