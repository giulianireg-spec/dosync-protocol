"""AUDIT-ARCHIVE (2026-07-19) — segment the chain with a hash anchor.

The live chain grows without bound (24k entries at the reference deployment,
roughly doubling every few days), all reloaded into memory at every hub start.
Archiving moves the oldest entries to a self-describing segment file while
keeping the cryptography honest end to end: the segment records the anchor it
chains from, the live chain verifies from the new anchor instead of genesis,
consecutive generations interlock, and the act of archiving leaves its own
chain-bound `audit_archived` entry carrying the segment file's SHA-256 — an
operation this consequential must itself be tamper-evident.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dosync.audit_backup import GENESIS, file_sha256, read_segment, verify_entries
from dosync.hub import DoSyncHub

REPO = Path(__file__).resolve().parent.parent


def _chain(dbp, n=30):
    hub = DoSyncHub(db_path=str(dbp))
    for i in range(n):
        hub.audit_log.append({"type": "test_entry", "n": i})
    assert hub.audit_log.verify()
    return hub


def _manage(dbp, *args):
    return subprocess.run(
        [sys.executable, "manage.py", "--db", str(dbp), "db", *args],
        cwd=str(REPO), capture_output=True, text=True)


def _load_live(dbp):
    con = sqlite3.connect(str(dbp))
    rows = con.execute(
        "SELECT entry_json FROM audit_log ORDER BY timestamp, id").fetchall()
    con.close()
    return [json.loads(r[0]) for r in rows]


def test_archive_splits_and_live_chain_verifies_from_anchor(tmp_path):
    dbp = tmp_path / "a.db"; seg = tmp_path / "seg1.json"
    _chain(dbp, 30)
    r = _manage(dbp, "audit-archive", "--keep", "10", "--out", str(seg), "--apply")
    assert r.returncode == 0, r.stdout + r.stderr

    live = _load_live(dbp)
    # 10 kept + the audit_archived entry documenting the operation
    assert len(live) == 11
    doc = read_segment(str(seg))
    assert doc["manifest"]["count"] == 20
    anchor = doc["manifest"]["last_hash"]
    assert verify_entries(live, anchor), "live chain must verify from the anchor"
    assert not verify_entries(live, GENESIS), "and must NOT verify from genesis"


def test_segment_verifies_standalone_and_binds_its_file(tmp_path):
    dbp = tmp_path / "a.db"; seg = tmp_path / "seg1.json"
    _chain(dbp, 30)
    _manage(dbp, "audit-archive", "--keep", "10", "--out", str(seg), "--apply")

    doc = read_segment(str(seg))
    assert verify_entries(doc["entries"], doc["manifest"]["anchor_prev_hash"])

    # THE provenance property: the live chain's audit_archived entry carries the
    # sha256 of the segment file as written — a silently swapped archive would
    # contradict the hash the chain remembers.
    arch = [e for e in _load_live(dbp) if e.get("type") == "audit_archived"]
    assert len(arch) == 1
    assert arch[0]["segment_sha256"] == file_sha256(str(seg))
    assert arch[0]["archived_count"] == 20
    assert arch[0]["segment_last_hash"] == doc["manifest"]["last_hash"]


def test_hub_restart_honors_anchor_and_chain_continues(tmp_path):
    dbp = tmp_path / "a.db"; seg = tmp_path / "seg1.json"
    _chain(dbp, 30)
    _manage(dbp, "audit-archive", "--keep", "10", "--out", str(seg), "--apply")

    hub2 = DoSyncHub(db_path=str(dbp))          # the restart
    assert hub2.audit_log.anchor_prev_hash != GENESIS
    assert hub2.audit_log.verify(), "restored chain must verify from its anchor"
    hub2.audit_log.append({"type": "test_entry", "n": 999})
    assert hub2.audit_log.verify(), "appends after restart must keep chaining"


def test_generations_interlock(tmp_path):
    dbp = tmp_path / "a.db"
    s1, s2 = tmp_path / "g1.json", tmp_path / "g2.json"
    _chain(dbp, 30)
    _manage(dbp, "audit-archive", "--keep", "10", "--out", str(s1), "--apply")
    _manage(dbp, "audit-archive", "--keep", "3", "--out", str(s2), "--apply")

    m1, m2 = read_segment(str(s1))["manifest"], read_segment(str(s2))["manifest"]
    assert m2["generation"] == 2
    assert m2["anchor_prev_hash"] == m1["last_hash"], \
        "segment N+1 must chain from segment N's last hash"
    # full history: seg1 from genesis, seg2 from seg1, live from seg2
    live = _load_live(dbp)
    assert verify_entries(read_segment(str(s1))["entries"], GENESIS)
    assert verify_entries(read_segment(str(s2))["entries"], m1["last_hash"])
    assert verify_entries(live, m2["last_hash"])


def test_refuses_to_archive_a_broken_chain(tmp_path):
    """Fail-loudly: archiving corruption would enshrine it in a 'trusted' file."""
    dbp = tmp_path / "a.db"
    _chain(dbp, 20)
    con = sqlite3.connect(str(dbp))
    row = con.execute("SELECT id, entry_json FROM audit_log LIMIT 1").fetchone()
    e = json.loads(row[1]); e["n"] = 666
    con.execute("UPDATE audit_log SET entry_json = ? WHERE id = ?",
                (json.dumps(e), row[0]))
    con.commit(); con.close()

    r = _manage(dbp, "audit-archive", "--keep", "5",
                "--out", str(tmp_path / "x.json"), "--apply")
    assert r.returncode != 0
    assert "REFUSED" in r.stdout
    assert not (tmp_path / "x.json").exists()


def test_tampering_detected_on_both_sides(tmp_path):
    dbp = tmp_path / "a.db"; seg = tmp_path / "seg1.json"
    _chain(dbp, 30)
    _manage(dbp, "audit-archive", "--keep", "10", "--out", str(seg), "--apply")

    # live DB tamper → anchored verify fails
    con = sqlite3.connect(str(dbp))
    row = con.execute("SELECT id, entry_json FROM audit_log LIMIT 1").fetchone()
    e = json.loads(row[1]); e["n"] = 777
    con.execute("UPDATE audit_log SET entry_json = ? WHERE id = ?",
                (json.dumps(e), row[0]))
    con.commit(); con.close()
    anchor = read_segment(str(seg))["manifest"]["last_hash"]
    assert not verify_entries(_load_live(dbp), anchor)

    # segment tamper → read refuses via payload checksum
    d = json.load(open(seg)); d["entries"][3]["n"] = 888
    json.dump(d, open(seg, "w"))
    with pytest.raises(ValueError):
        read_segment(str(seg))


def test_dry_run_writes_nothing(tmp_path):
    dbp = tmp_path / "a.db"; seg = tmp_path / "seg1.json"
    _chain(dbp, 30)
    r = _manage(dbp, "audit-archive", "--keep", "10", "--out", str(seg))
    assert "Dry run" in r.stdout
    assert not seg.exists()
    assert len(_load_live(dbp)) == 30


def test_keep_below_one_is_refused_and_small_chains_are_left_alone(tmp_path):
    dbp = tmp_path / "a.db"
    _chain(dbp, 5)
    r = _manage(dbp, "audit-archive", "--keep", "0", "--out", str(tmp_path/"x.json"), "--apply")
    assert r.returncode != 0
    r2 = _manage(dbp, "audit-archive", "--keep", "10", "--out", str(tmp_path/"x.json"), "--apply")
    assert "Nothing to do" in r2.stdout
