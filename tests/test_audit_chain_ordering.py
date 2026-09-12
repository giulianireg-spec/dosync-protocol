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


# ── The fix, and the three detections it must not lose ───────────────────────

def _log_with(n: int) -> AuditLog:
    log = AuditLog()
    for i in range(n):
        log.append({"type": "device_event", "device_id": f"dev-{i}"})
    return log


def test_a_chain_read_out_of_order_still_verifies():
    """The production failure, fixed.

    Entries stored in clock order rather than chain order used to fail two
    checks at once: the prev_hash link, and the requirement that sequence
    numbers be consecutive. Walking the chain satisfies both, because the order
    it recovers is the order they were written in.
    """
    log = _log_with(5)
    assert log.verify(), "the chain does not verify before we disturb it"

    # Exactly what the database returned in production: two adjacent entries
    # swapped, everything else untouched.
    log._entries[2], log._entries[3] = log._entries[3], log._entries[2]

    assert log.verify(), (
        "a chain that is intact but stored out of order still fails — the "
        "production defect is not fixed")


def test_an_altered_entry_still_fails():
    """Detection one. Changing any content breaks its hash, and no amount of
    reordering hides that."""
    log = _log_with(5)
    log._entries[2]["device_id"] = "somebody-elses-device"

    assert not log.verify(), (
        "an entry was edited and the chain still verifies — walking the chain "
        "has replaced tamper detection rather than preserving it")


def test_an_entry_removed_from_the_middle_still_fails():
    """Detection two, and the one this change could most easily have lost.

    Deleting from the middle leaves every surviving link intact. What it breaks
    is reachability: the walk stops at the gap. If `_in_chain_order` returned
    the part it could reach instead of refusing, a chain with its middle cut
    out would verify — which is exactly what someone removing an inconvenient
    entry would want.
    """
    log = _log_with(5)
    del log._entries[2]

    assert not log.verify(), (
        "an entry was deleted from the middle and the chain verifies: the walk "
        "is accepting a partial chain")


def test_a_truncated_tail_still_fails():
    """Detection three. Links alone cannot see this — every remaining link of
    a truncated chain is intact — so it relies on the head mark recorded
    elsewhere, which the reordering must leave working."""
    log = _log_with(5)
    tail = log._entries[-1]
    mark = {"seq": tail["seq"], "hash": tail["hash"]}

    assert log.verify(head_mark=mark), "the mark does not match its own chain"

    del log._entries[-1]
    assert not log.verify(head_mark=mark), (
        "the tail was cut off and the chain verifies against a mark it no "
        "longer reaches")


def test_a_fork_whose_branches_both_continue_fails():
    """Two histories from one point, both going somewhere.

    This test asserted the opposite of what it does now, and the change is the
    substance rather than the wording. It used to say any shared predecessor
    was a forged history — which refused the real production chain, where the
    6 September archiver left a marker sharing a predecessor with the entry
    that overtook it.

    A shared predecessor where one side continues and the other does not is a
    leaf: a slow writer that persisted late. A shared predecessor where BOTH
    sides continue is two histories, and no sequence of appends produces that.
    """
    log = _log_with(6)

    # Re-point entry 4 at entry 1's predecessor, so two chains run on from the
    # same point: 0-1-2-3... and 0-4-5...
    log._entries[4] = dict(log._entries[4],
                           prev_hash=log._entries[1]["prev_hash"])

    assert not log.verify(), (
        "two branches both continue from one predecessor and the chain "
        "verifies — an alternative history was accepted")


def test_a_leaf_is_reported_and_does_not_fail_the_chain(caplog):
    """The production failure of 6 September, reproduced.

    Eleven WiZ devices registered inside the same millisecond while the
    archiver was writing a 659 KB segment. The archiver had computed its marker
    from the chain head; by the time it persisted, those eleven had moved the
    head on. The marker was written, is intact, and nothing chains from it.

    Measured on the hub: 20,904 of 20,905 entries reachable, zero orphans, one
    shared predecessor. The chain is whole — one entry is beside it.

    Failing the whole chain over that costs an operator twenty thousand
    verified entries to flag one that is provably fine, and the thing it
    describes — the archived segment — is on disk with its hash.
    """
    import logging

    log = _log_with(5)
    head = log._entries[-1]

    # An entry computed from an earlier head, persisted after the chain moved:
    # its predecessor is in the chain, and nothing follows it.
    leaf = dict(head, seq=head["seq"] + 1, type="audit_archived",
                prev_hash=log._entries[-2]["prev_hash"])
    log._entries.append(leaf)

    with caplog.at_level(logging.WARNING):
        result = log.verify()

    assert result, (
        "a leaf failed the whole chain — twenty thousand verified entries lost "
        "over one that is intact and simply never joined")
    assert any("leaf" in r.message for r in caplog.records), (
        "the leaf verified silently; an entry hanging off the chain has to be "
        "reported or nobody learns the writer has a defect")


def test_a_leaf_and_a_deletion_are_told_apart():
    """Both leave entries the walk does not visit. Only one is an attack.

    A leaf's predecessor is IN the chain — the walk saw it and carried on past.
    A deletion removes something the chain points at, so the walk stops. If
    these were conflated in the permissive direction, removing an inconvenient
    entry would verify.
    """
    with_leaf = _log_with(5)
    head = with_leaf._entries[-1]
    with_leaf._entries.append(
        dict(head, seq=head["seq"] + 1,
             prev_hash=with_leaf._entries[-2]["prev_hash"]))

    with_hole = _log_with(5)
    del with_hole._entries[2]

    assert with_leaf.verify(), "the leaf case is being treated as a break"
    assert not with_hole.verify(), (
        "an entry deleted from the middle now verifies — leaf handling was "
        "made permissive enough to cover a deletion")


def test_the_walk_follows_the_branch_that_continues():
    """Which of two entries sharing a predecessor the chain goes on through.

    Mutation testing caught this gap: replacing the choice with "take whichever
    came first" passed every other test in this file. Order of insertion
    happened to be right in all of them, so nothing was actually asserting the
    rule.

    It matters because the leaf can arrive first. On 6 September the archiver's
    marker was written before the entry that overtook it, and following the
    first candidate would walk into the leaf and abandon the twenty thousand
    entries behind the other branch.
    """
    log = _log_with(4)

    # A leaf sharing entry 2's predecessor, placed BEFORE it in storage so that
    # "first candidate" picks the wrong one.
    leaf = dict(log._entries[2], seq=999, type="audit_archived",
                hash="f" * 64)
    log._entries.insert(2, leaf)

    assert log.verify(), (
        "the walk followed the leaf and abandoned the rest of the chain")

    ordered = log._in_chain_order(log._entries)
    assert len(ordered) == 4, (
        f"the walk recovered {len(ordered)} entries, not the four that chain")
    assert all(e["hash"] != "f" * 64 for e in ordered), (
        "the leaf is in the reconstructed chain")


def test_the_gap_a_leaf_leaves_is_not_read_as_truncation():
    """A leaf takes a sequence number and then fails to join the chain.

    The walk skips it, so the numbers it visits have a hole — and a hole is
    how a deleted entry looks. On the production hub the chain verified, the
    leaf was reported, and verification still returned False for exactly this:
    147171 to 147173, with 147172 sitting right there as the leaf it had just
    warned about.

    The difference is that the leaf is present. Missing from the sequence,
    not missing from the log.
    """
    log = _log_with(4)
    head = log._entries[-1]

    # The leaf takes its own number and the next entry takes the one after,
    # which is what leaves the hole. A first version had the leaf and the next
    # entry share a number, so the walk saw 0,1,2,3,4 with no gap at all and
    # the test passed without exercising the rule — mutation caught it.
    leaf = dict(head, seq=head["seq"] + 1, type="audit_archived",
                hash="a" * 64, prev_hash=log._entries[-2]["prev_hash"])
    log._entries.append(leaf)
    log._next_seq = leaf["seq"] + 1
    log.append({"type": "device_event", "device_id": "after-the-leaf"})

    walked = log._in_chain_order(log._entries)
    seqs = [e["seq"] for e in walked]
    assert any(b - a > 1 for a, b in zip(seqs, seqs[1:])), (
        "the scenario produces no gap, so it is not testing the rule")

    assert log.verify(), (
        "the hole a leaf leaves in the sequence is being read as truncation — "
        "verification fails over the entry it just reported as intact")


def test_a_gap_with_no_leaf_to_explain_it_still_fails():
    """The other side, and the one that matters.

    Accepting gaps because leaves exist would accept a deletion in any chain
    that also has a leaf. A number is only excused when the entry that took it
    is still in the log.
    """
    log = _log_with(4)

    # The gap is made by skipping numbers on the way in, not by renumbering an
    # entry afterwards. A first version did the latter, which changes the
    # entry's content and breaks its hash — so the test passed on the hash
    # check and never reached the rule it was written for. Mutation caught it:
    # "accept any gap" left it green.
    log._next_seq += 5
    log.append({"type": "device_event", "device_id": "after-the-gap"})

    assert not log.verify(), (
        "a sequence number vanished with no entry accounting for it and the "
        "chain verifies — leaf handling is excusing real gaps")
