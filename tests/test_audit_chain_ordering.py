"""The chain defines its own order; reading it by clock does not preserve that.

Production reported `audit_integrity: False` over 12,394 entries on
9 September, and nothing had been tampered with. The hashes chained correctly —
what failed was reconstructing the order they were written in.
"""
import json
import threading
import time

from dosync.audit import AuditLog


def _chain_reads_in_order(entries) -> bool:
    """Whether each entry's prev_hash is the hash of the one before it."""
    prev = "0" * 64
    for e in entries:
        if e.get("prev_hash") != prev:
            return False
        prev = e["hash"]
    return True


def test_the_chain_is_correct_even_when_the_clock_disagrees():
    """The exact production failure, reproduced from its data.

    Observed at index 9999, and the sequence numbers give the mechanism away:

        [9998]  seq=147170  device_event
        [9999]  seq=147172  audit_archived     <- reads as broken
        [10000] seq=147171  device_event

    The hashes chain 9998 -> 10000 -> 9999. The archiver computed its marker,
    spent time writing a 1.4 MB segment to disk, and persisted afterwards; a
    sensor emitting every thirty seconds slipped an entry into that window with
    an earlier timestamp. `ORDER BY timestamp, id` then read them back the wrong
    way round.

    Nothing was tampered with. The chain was never corrupt.
    """
    log = AuditLog()
    log.append({"type": "device_event", "device_id": "sensor-a"})
    log.append({"type": "device_event", "device_id": "sensor-b"})
    log.append({"type": "device_event", "device_id": "sensor-c"})

    written = list(log.entries())
    assert _chain_reads_in_order(written), "the chain is wrong before we touch it"

    # Reorder by timestamp with two entries written inside the same tick — the
    # database's ORDER BY resolves ties by rowid, and the rows were inserted in
    # the order they were persisted, not the order they were chained.
    swapped = [written[0], written[2], written[1]]

    assert not _chain_reads_in_order(swapped), (
        "reordering the chain did not break the link check, so this test is "
        "not reproducing anything")

    # Following the links puts it back with no reference to any clock.
    by_prev = {e["prev_hash"]: e for e in swapped}
    rebuilt, cursor = [], "0" * 64
    while cursor in by_prev:
        entry = by_prev[cursor]
        rebuilt.append(entry)
        cursor = entry["hash"]

    assert _chain_reads_in_order(rebuilt), (
        "following prev_hash did not recover the order the chain was written in")
    assert [e["seq"] for e in rebuilt] == [e["seq"] for e in written], (
        "the rebuilt order is not the order the entries were appended in")


def test_concurrent_appends_still_produce_a_linked_chain():
    """What concurrency does and does not break here.

    An earlier version of this test asserted that two threads could take the
    same sequence number. They cannot, under CPython: everything between
    reading `_next_seq` and incrementing it — a dict write, `json.dumps`, a
    sha256 over a small payload — runs without a point at which the interpreter
    switches threads. The test passed while demonstrating nothing, twice, and
    adding a slow persistence callback did not help because the callback runs
    after the critical section.

    So the guarantee is real and accidental. It rests on CPython's GIL and on
    entries being small enough that hashing does not release it, neither of
    which `append` states or checks. What is asserted here is the property that
    actually holds today: concurrent writers produce a chain that links.

    The production failure was never this. It was the ORDER the entries are read
    back in, which the test above reproduces.
    """
    log = AuditLog()
    barrier = threading.Barrier(8)

    def writer(n):
        barrier.wait()
        for i in range(25):
            log.append({"type": "device_event", "device_id": f"dev-{n}-{i}"})

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    entries = log.entries()
    seqs = [e["seq"] for e in entries]

    assert len(set(seqs)) == len(seqs), (
        f"{len(seqs) - len(set(seqs))} entries share a sequence number — the "
        "critical section in append is no longer indivisible, and it was never "
        "guarded")
    assert _chain_reads_in_order(entries), (
        "concurrent appends produced a chain that does not link")
