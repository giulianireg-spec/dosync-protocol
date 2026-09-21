"""verify() is atomic against concurrent writes -- it never cries tampering on a
sound chain.

The DB lock added earlier serialized database writes, not the audit log's
in-memory chain. verify() walks _entries (and rebuilds it via collapse/drop)
while append() mutates it from other request threads, so it read a torn state and
returned integrity=false on a chain that was in fact intact -- the transient
false alarm seen on /v1/status. A reentrant lock on the audit log now serializes
append, verify and archive.
"""
import threading
import time

from dosync.hub import DoSyncHub


def test_verify_never_false_under_concurrent_appends():
    hub = DoSyncHub(db_path=":memory:")
    al = hub.audit_log
    for i in range(20):
        al.append({"type": "seed", "i": i})

    stop = threading.Event()
    false_seen = []

    def appender(tid):
        i = 0
        while not stop.is_set():
            al.append({"type": "probe", "tid": tid, "i": i})
            i += 1

    def verifier():
        while not stop.is_set():
            if al.verify() is False:
                false_seen.append(True)

    appenders = [threading.Thread(target=appender, args=(t,)) for t in range(4)]
    verifier_t = threading.Thread(target=verifier)
    for t in appenders:
        t.start()
    verifier_t.start()
    time.sleep(1.0)
    stop.set()
    for t in appenders:
        t.join()
    verifier_t.join()

    assert not false_seen, (
        f"verify() returned False {len(false_seen)} times on a sound chain -- "
        "it read a half-updated chain during a concurrent append")


def test_archive_does_not_lose_an_append_landing_mid_archive():
    """maybe_archive snapshots the entries, writes a segment, then rebuilds
    _entries. An append landing in that window must not be dropped when the list
    is rebuilt -- the lock makes the whole archive atomic against append."""
    import os
    import tempfile
    os.environ["DOSYNC_AUDIT_SEGMENT_DIR"] = tempfile.mkdtemp()
    os.environ["DOSYNC_AUDIT_MAX_LIVE"] = "20"
    hub = DoSyncHub(db_path=":memory:")
    al = hub.audit_log
    for i in range(30):
        al.append({"type": "seed", "i": i})

    stop = threading.Event()

    def appender():
        i = 0
        while not stop.is_set():
            al.append({"type": "probe", "i": i})
            i += 1

    t = threading.Thread(target=appender)
    t.start()
    for _ in range(20):
        hub.maybe_archive()          # repeatedly archive while appends land
    stop.set()
    t.join()

    # the chain must still verify -- no entry was lost creating a seq gap
    assert al.verify() is True
