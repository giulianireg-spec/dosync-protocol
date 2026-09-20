"""A hashless in-memory entry is dropped and reported, never a crash.

An entry with no `hash` key cannot be a chain link -- nothing hash-chains from
it -- and cannot be re-hashed. In production one appeared in the live chain in
memory (the disk was clean) and crashed verify() on /v1/status: the walk read
e["hash"] and the re-hash popped it. Such an entry is now dropped and reported
once, like a duplicate, before the chain is walked.
"""
import os
import tempfile

from dosync.hub import DoSyncHub
from dosync.audit import walk_chain


def _hub_with_chain(n=4):
    d = tempfile.mkdtemp()
    os.environ["DOSYNC_AUDIT_SEGMENT_DIR"] = os.path.join(d, "seg")
    hub = DoSyncHub(db_path=os.path.join(d, "h.db"))
    for i in range(n):
        hub.audit_log.append({"type": "probe", "i": i})
    return hub


def test_verify_drops_a_hashless_entry_and_still_passes():
    hub = _hub_with_chain()
    last = hub.audit_log._entries[-1]
    # a chained entry with no hash -- the production artifact
    hub.audit_log._entries.append({"type": "probe", "prev_hash": last["hash"], "seq": 999})

    assert hub.audit_log.verify() is True                     # no crash; chain still verifies
    assert all("hash" in e for e in hub.audit_log._entries)   # the artifact was dropped


def test_walk_chain_does_not_crash_on_a_hashless_entry():
    good = [{"prev_hash": "0" * 64, "hash": "aaa"},
            {"prev_hash": "aaa", "hash": "bbb"}]
    hashless = {"prev_hash": "bbb", "seq": 7}                 # chained, but no hash key
    # Must not raise KeyError: the walk uses .get for hashes everywhere.
    walk_chain(good + [hashless], "0" * 64)
