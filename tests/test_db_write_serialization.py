"""Every write is serialized on the DB lock -- `_cursor` and the raw
`@_synchronized` writers alike.

One connection shared across FastAPI's thread pool let two requests commit each
other's transactions. A lock now serializes every write, so a rolled-back write
on one thread cannot be committed by another mid-flight.
"""
import os
import sqlite3
import tempfile
import threading
import time

from dosync.db import DoSyncDB


def _db_with_t():
    db = DoSyncDB(os.path.join(tempfile.mkdtemp(), "w.db"))
    db.init()
    db._conn.execute("CREATE TABLE t (x INTEGER)")
    db._conn.execute("INSERT INTO t VALUES (0)")
    db._conn.commit()
    return db


def _read(db):
    return sqlite3.connect(db.db_path).execute("SELECT x FROM t").fetchone()[0]


def _rolls_back_holding_the_lock(db, gate):
    """A _cursor that writes, waits at the gate holding the lock, then rolls
    back. If writes are serialized, no other thread can commit in between."""
    try:
        with db._cursor() as cur:
            cur.execute("UPDATE t SET x = 999")
            gate.wait()
            time.sleep(0.05)
            raise RuntimeError("fail -> rollback")
    except RuntimeError:
        pass


def test_a_cursor_rollback_is_not_committed_by_a_concurrent_cursor():
    db = _db_with_t()
    gate = threading.Barrier(2)

    def thread_b():
        gate.wait()
        with db._cursor() as cur:            # blocks on the lock until A rolls back
            cur.execute("SELECT 1")

    ta = threading.Thread(target=_rolls_back_holding_the_lock, args=(db, gate))
    tb = threading.Thread(target=thread_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert _read(db) == 0, "a concurrent _cursor committed a rolled-back write"


def test_a_synchronized_raw_write_is_serialized_too():
    """The raw writers carry @_synchronized, so a raw write racing a _cursor
    rollback cannot commit the rolled-back write either -- the point of covering
    the raw sites, not only _cursor."""
    db = _db_with_t()
    gate = threading.Barrier(2)

    def thread_b():
        gate.wait()
        db.append_rate_limit_event("dev-1", 1.0)   # @_synchronized raw write

    ta = threading.Thread(target=_rolls_back_holding_the_lock, args=(db, gate))
    tb = threading.Thread(target=thread_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert _read(db) == 0, "a raw write committed a rolled-back _cursor write -- not serialized"
