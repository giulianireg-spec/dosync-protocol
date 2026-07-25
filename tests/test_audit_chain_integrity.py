"""What the audit chain can and cannot detect (2026-07-25).

Written after auditing the project's own claim of a "tamper-evident record"
against three concrete attacks. Modification in place was already caught. The
other two were not, and the honest conclusion was that hash links alone cannot
catch them:

  * TRUNCATION — drop the last entry and every surviving link is still intact,
    so the chain verifies. Now caught by a head record kept in a DIFFERENT table
    (`audit_meta`), which the deletion did not touch.
  * FULL REWRITE — an adversary with write access to the whole database rewrites
    the entries, recomputes the hashes, and updates the head to match. Nothing
    stored on the machine can detect this, because the attacker owns every
    record the check would consult. Only a signed checkpoint EXPORTED elsewhere
    can, and only because it is out of reach.

These tests pin all three outcomes, including the one that remains undetectable
locally — because a security property that is documented but unproven is the
kind of claim this project exists to avoid making.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dosync.hub import DoSyncHub

REPO = Path(__file__).resolve().parent.parent


def _chain(db_path, n=6):
    hub = DoSyncHub(db_path=str(db_path))
    for i in range(n):
        hub.audit_log.append({"type": "action", "n": i})
    # The head is written in batches (DOSYNC_AUTH_HEAD_EVERY, default 25), so a
    # short test chain would leave none recorded. Production does this at
    # shutdown and before any operation that rewrites the log.
    hub.audit_log.flush_head()
    return hub


def _manage(db_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "dosync.manage", "--db", str(db_path), "db", *args],
        cwd=str(REPO), capture_output=True, text=True)


# ── Sequence numbers ─────────────────────────────────────────────────────────

def test_entries_carry_a_monotonic_sequence(tmp_path):
    hub = _chain(tmp_path / "a.db")
    seqs = [e["seq"] for e in hub.audit_log.entries()]
    assert seqs == list(range(len(seqs)))


def test_sequence_survives_restart_without_reuse(tmp_path):
    """Two entries must never share a number, or 'entry 4' stops identifying
    anything."""
    dbp = tmp_path / "a.db"
    _chain(dbp, 3)
    hub2 = DoSyncHub(db_path=str(dbp))       # restart
    hub2.audit_log.append({"type": "after_restart"})
    seqs = [e["seq"] for e in hub2.audit_log.entries()]
    assert len(seqs) == len(set(seqs)), "sequence numbers must be unique"
    assert seqs[-1] == max(seqs)


def test_a_gap_in_the_sequence_fails_verification():
    """A chain whose HASHES are all valid but whose numbering skips an entry.

    Built by hand on purpose. Editing `seq` on a finished entry would break its
    hash, and the check would then pass for the wrong reason — proving the hash
    test works, not the sequence test. The gap has to be baked in before each
    entry is sealed for this to test what it claims to.
    """
    hub = DoSyncHub(db_path=":memory:")
    prev = "0" * 64
    entries = []
    for seq in (0, 1, 3):                       # 2 is missing
        e = {"type": "action", "seq": seq, "prev_hash": prev,
             "timestamp": time.time()}
        e["hash"] = hashlib.sha256(
            json.dumps(e, sort_keys=True).encode()).hexdigest()
        prev = e["hash"]
        entries.append(e)
    hub.audit_log._entries = entries

    # Every link is intact...
    for i, e in enumerate(entries):
        stored = e.pop("hash")
        recomputed = hashlib.sha256(
            json.dumps(e, sort_keys=True).encode()).hexdigest()
        e["hash"] = stored
        assert recomputed == stored, f"entry {i} hash must be valid for this test"

    # ...and verification still refuses it, because of the missing number.
    assert hub.audit_log.verify() is False


def test_chain_without_sequence_numbers_still_verifies():
    """Backward compatibility is not optional: a production chain written before
    this change must keep verifying across the upgrade."""
    hub = DoSyncHub(db_path=":memory:")
    for e in hub.audit_log._entries:
        pass
    # simulate a legacy chain: entries built the old way, no `seq`
    prev = "0" * 64
    legacy = []
    for i in range(3):
        e = {"type": "legacy", "n": i, "prev_hash": prev, "timestamp": time.time()}
        e["hash"] = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
        prev = e["hash"]
        legacy.append(e)
    hub.audit_log._entries = legacy
    assert hub.audit_log.verify() is True


# ── Attack 1: modification in place (was already caught) ─────────────────────

def test_modified_entry_is_detected(tmp_path):
    hub = _chain(tmp_path / "a.db")
    hub.audit_log._entries[2]["n"] = 999
    assert hub.audit_log.verify() is False


# ── Attack 2: truncation (newly caught) ──────────────────────────────────────

def test_truncation_is_invisible_to_links_alone(tmp_path):
    """Stated explicitly so nobody mistakes this for a bug later: the links
    CANNOT see a removed tail. That is why the head record exists."""
    hub = _chain(tmp_path / "a.db")
    hub.audit_log._entries.pop()
    assert hub.audit_log.verify() is True


def test_truncation_is_detected_against_the_head_record(tmp_path):
    dbp = tmp_path / "a.db"
    hub = _chain(dbp)
    head = hub.db.get_audit_head()
    assert head is not None, "the head must be recorded when flushed"

    hub.audit_log._entries.pop()
    assert hub.audit_log.verify(head_mark=head) is False, \
        "the chain no longer reaches the recorded mark — that is a truncation"


def test_manage_verify_reports_a_truncated_tail(tmp_path):
    dbp = tmp_path / "a.db"
    _chain(dbp)
    con = sqlite3.connect(str(dbp))
    last = con.execute("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.execute("DELETE FROM audit_log WHERE id = ?", (last,))
    con.commit(); con.close()

    r = _manage(dbp, "audit-verify")
    assert "TRUNCATED" in r.stdout, r.stdout
    assert r.returncode != 0


# ── Attack 3: full rewrite (undetectable locally, caught by checkpoint) ──────

def _rewrite_everything(db_path):
    """What an adversary with full database access can do."""
    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT id, entry_json FROM audit_log ORDER BY timestamp, id").fetchall()
    prev = "0" * 64
    for rid, ej in rows:
        e = json.loads(ej)
        e["n"] = 6666
        e["prev_hash"] = prev
        raw = json.dumps({k: v for k, v in e.items() if k != "hash"}, sort_keys=True)
        e["hash"] = hashlib.sha256(raw.encode()).hexdigest()
        prev = e["hash"]
        con.execute("UPDATE audit_log SET entry_json=?, hash=? WHERE id=?",
                    (json.dumps(e), e["hash"], rid))
    # and keeps the head record consistent
    con.execute("INSERT INTO audit_meta (key,value_json) VALUES ('chain_head',?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (json.dumps({"seq": len(rows) - 1, "hash": prev, "at": time.time()}),))
    con.commit(); con.close()


def test_full_rewrite_defeats_every_local_check(tmp_path):
    """The honest limit. Anyone who claims otherwise has not tried it."""
    dbp = tmp_path / "a.db"
    _chain(dbp)
    _rewrite_everything(dbp)

    r = _manage(dbp, "audit-verify")
    assert "Chain valid: yes" in r.stdout, r.stdout
    assert "consistent" in r.stdout, "local checks are satisfied — this is the point"


def test_exported_checkpoint_detects_a_full_rewrite(tmp_path):
    """And the answer to it: a checkpoint the attacker never had."""
    dbp = tmp_path / "a.db"
    cp = tmp_path / "cp.json"
    _chain(dbp)

    made = _manage(dbp, "audit-checkpoint", "--out", str(cp))
    assert made.returncode == 0, made.stdout + made.stderr
    assert cp.exists()

    before = _manage(dbp, "audit-verify", "--checkpoint", str(cp))
    assert "attested head found" in before.stdout, before.stdout

    _rewrite_everything(dbp)

    after = _manage(dbp, "audit-verify", "--checkpoint", str(cp))
    assert "NOT PRESENT" in after.stdout, after.stdout
    assert after.returncode != 0


def test_checkpoint_is_signed_and_tamper_evident(tmp_path):
    """A checkpoint an attacker could edit would prove nothing."""
    from dosync import cert_signing

    dbp = tmp_path / "a.db"
    cp = tmp_path / "cp.json"
    _chain(dbp)
    _manage(dbp, "audit-checkpoint", "--out", str(cp))

    doc = json.loads(cp.read_text())
    ok, _ = cert_signing.verify_report(doc)
    assert ok, "checkpoint must carry a valid signature"

    doc["head_hash"] = "0" * 64
    bad, msg = cert_signing.verify_report(doc)
    assert not bad, "an edited checkpoint must fail verification"


def test_checkpoint_of_empty_chain_is_refused(tmp_path):
    """Nothing to attest to, so nothing is written — a checkpoint of an empty
    chain would be a signed statement about no history at all."""
    dbp = tmp_path / "empty.db"
    DoSyncHub(db_path=str(dbp))          # creates the database, appends nothing
    out = tmp_path / "x.json"
    r = _manage(dbp, "audit-checkpoint", "--out", str(out))
    assert "nothing to checkpoint" in r.stdout.lower(), r.stdout
    assert not out.exists()


def test_checkpoint_refuses_a_missing_database(tmp_path):
    r = _manage(tmp_path / "does-not-exist.db", "audit-checkpoint",
                "--out", str(tmp_path / "y.json"))
    assert "not found" in r.stdout.lower()
    assert not (tmp_path / "y.json").exists()


def test_sequence_continues_correctly_after_archiving(tmp_path):
    """Regression found while building this (2026-07-25).

    Archiving removes rows and appends an `audit_archived` marker written by
    direct SQL, which carries no `seq`. Restoring from the LAST entry therefore
    found no number and fell back to the row count — which after archiving is
    far smaller than the numbers the surviving entries already hold. The series
    wound backwards and the next append produced a chain that failed its own
    verification. The starting point has to be the highest number present, not
    how many rows are left.
    """
    dbp = tmp_path / "a.db"
    seg = tmp_path / "seg.json"
    _chain(dbp, 12)

    r = _manage(dbp, "audit-archive", "--keep", "3", "--out", str(seg), "--apply")
    assert r.returncode == 0, r.stdout + r.stderr

    hub = DoSyncHub(db_path=str(dbp))            # restart after archiving
    survivors = [e["seq"] for e in hub.audit_log.entries() if e.get("seq") is not None]
    hub.audit_log.append({"type": "after_archive"})

    new_seq = hub.audit_log.entries()[-1]["seq"]
    assert new_seq > max(survivors), \
        f"new entry got seq {new_seq}, at or below existing {max(survivors)}"
    assert hub.audit_log.verify(), "the chain must still verify after archiving"


# ── Panel review findings (2026-07-25): no false alarms on legitimate ops ────
#
# The design passed review; the implementation did not. Running the LEGITIMATE
# operation — archiving after taking a checkpoint — made verification report
# tampering twice and exit non-zero. As Paredes put it: a control that cries
# wolf during a documented operation teaches operators to ignore it, so the next
# real alarm gets waved through. These pin the three blockers closed.

def test_archiving_does_not_trip_the_head_record(tmp_path):
    """B1: `audit-archive` appends its marker by direct SQL. If it does not also
    update the head, the mark points at an entry that is no longer the tail and
    verification reports a truncation that never happened."""
    dbp = tmp_path / "a.db"
    seg = tmp_path / "seg.json"
    _chain(dbp, 10)
    _manage(dbp, "audit-archive", "--keep", "3", "--out", str(seg), "--apply")

    r = _manage(dbp, "audit-verify")
    assert "TRUNCATED" not in r.stdout, r.stdout
    assert "ALTERED" not in r.stdout, r.stdout
    assert r.returncode == 0, f"legitimate archiving must verify clean:\n{r.stdout}"


def test_archiving_does_not_trip_the_checkpoint_check(tmp_path):
    """B2: comparing the attested head's POSITION against the checkpoint's entry
    count assumes the chain only ever grows. Archiving shortens it, and the
    comparison then accuses the operator of rewriting history."""
    dbp = tmp_path / "a.db"
    cp = tmp_path / "cp.json"
    seg = tmp_path / "seg.json"
    _chain(dbp, 10)
    _manage(dbp, "audit-checkpoint", "--out", str(cp))
    _manage(dbp, "audit-archive", "--keep", "3", "--out", str(seg), "--apply")

    r = _manage(dbp, "audit-verify", "--checkpoint", str(cp))
    assert "was altered" not in r.stdout, r.stdout
    assert "NOT PRESENT" not in r.stdout, r.stdout
    assert "attested head found" in r.stdout, r.stdout


def test_legitimate_archiving_exits_zero(tmp_path):
    """B3: the exit code is an API. The threat model tells operators to run this
    on a schedule; a nightly archive must not break their monitoring."""
    dbp = tmp_path / "a.db"
    cp = tmp_path / "cp.json"
    seg = tmp_path / "seg.json"
    _chain(dbp, 10)
    _manage(dbp, "audit-checkpoint", "--out", str(cp))
    _manage(dbp, "audit-archive", "--keep", "3", "--out", str(seg), "--apply")

    assert _manage(dbp, "audit-verify").returncode == 0
    assert _manage(dbp, "audit-verify", "--checkpoint", str(cp)).returncode == 0


def test_attacks_still_detected_after_the_false_positive_fix(tmp_path):
    """The obvious risk in silencing false alarms is silencing real ones. Both
    attacks must still fail verification with a non-zero exit."""
    # truncation
    dbp = tmp_path / "t.db"
    _chain(dbp, 8)
    con = sqlite3.connect(str(dbp))
    con.execute("DELETE FROM audit_log WHERE id=(SELECT MAX(id) FROM audit_log)")
    con.commit(); con.close()
    r = _manage(dbp, "audit-verify")
    assert "TRUNCATED" in r.stdout and r.returncode != 0, r.stdout

    # full rewrite, caught only by the exported checkpoint
    dbp2 = tmp_path / "r.db"
    cp = tmp_path / "cp2.json"
    _chain(dbp2, 8)
    _manage(dbp2, "audit-checkpoint", "--out", str(cp))
    _rewrite_everything(dbp2)
    r2 = _manage(dbp2, "audit-verify", "--checkpoint", str(cp))
    assert "NOT PRESENT" in r2.stdout and r2.returncode != 0, r2.stdout


def test_head_writes_are_batched(tmp_path):
    """R1: writing the head on every append cost 57% per entry, which lands
    during an emergency when one intent produces dozens of entries. Batching is
    safe because the mark is a high-water mark: lagging behind means the chain
    GREW, which is not an attack."""
    hub = DoSyncHub(db_path=str(tmp_path / "b.db"))
    assert hub.audit_log._head_every > 1, "head must not be written every append"
    for i in range(3):
        hub.audit_log.append({"type": "a", "n": i})
    assert hub.db.get_audit_head() is None, "a short burst should not write the head"
    hub.audit_log.flush_head()
    assert hub.db.get_audit_head() is not None, "flush must persist the mark"


def test_batched_head_never_causes_a_false_alarm(tmp_path):
    """The safety property that makes batching acceptable."""
    dbp = tmp_path / "c.db"
    hub = _chain(dbp, 6)                      # flushes the head
    for i in range(5):                        # grow past the mark
        hub.audit_log.append({"type": "later", "n": i})
    head = hub.db.get_audit_head()
    assert hub.audit_log.verify(head_mark=head) is True, \
        "a chain that grew past the mark is healthy, not tampered with"


def test_archiving_advances_the_head_mark(tmp_path):
    """B1, pinned precisely.

    The high-water-mark semantics alone already prevent the FALSE ALARM after
    archiving, so removing the head update from `audit-archive` does not bring
    the false positive back — which is why the outcome test above cannot isolate
    this. What the update buys is DETECTION STRENGTH: it advances the mark to
    the newest entry, so a truncation occurring after an archive is still
    caught. Leaving the mark behind would let everything appended since the last
    flush be removed unnoticed.
    """
    dbp = tmp_path / "a.db"
    seg = tmp_path / "seg.json"
    hub = _chain(dbp, 10)
    mark_before = hub.db.get_audit_head()["seq"]

    _manage(dbp, "audit-archive", "--keep", "3", "--out", str(seg), "--apply")

    mark_after = hub.db.get_audit_head()["seq"]
    assert mark_after is not None
    assert mark_after > mark_before, (
        f"the mark stayed at {mark_before} after archiving; entries appended "
        "since would be removable without detection")

    # And the strength is real: truncating right after the archive is caught.
    con = sqlite3.connect(str(dbp))
    con.execute("DELETE FROM audit_log WHERE id=(SELECT MAX(id) FROM audit_log)")
    con.commit(); con.close()
    r = _manage(dbp, "audit-verify")
    assert "TRUNCATED" in r.stdout, r.stdout


def test_checkpoint_of_a_legacy_chain_omits_the_null_sequence(tmp_path):
    """Production condition at the reference deployment: 16,223 entries written
    before sequence numbers existed. The checkpoint must still be usable, and a
    signed compliance artifact must not hand an auditor a null field to
    interpret — `entry_count` and `head_hash` already identify the attested
    point, and verification matches on the hash."""
    import hashlib as _h

    dbp = tmp_path / "legacy.db"
    hub = DoSyncHub(db_path=str(dbp))
    prev = "0" * 64
    for i in range(5):
        e = {"type": "legacy", "n": i, "prev_hash": prev, "timestamp": time.time()}
        e["hash"] = _h.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
        prev = e["hash"]
        hub.db.append_audit(e)

    cp = tmp_path / "cp.json"
    r = _manage(dbp, "audit-checkpoint", "--out", str(cp))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "predates sequence numbers" in r.stdout

    doc = json.loads(cp.read_text())
    assert "seq" not in doc, "a signed artifact must not carry a null sequence"
    assert doc["entry_count"] == 5 and doc["head_hash"]

    # and it still does its job on the chain it attests to
    good = _manage(dbp, "audit-verify", "--checkpoint", str(cp))
    assert "attested head found" in good.stdout and good.returncode == 0

    _rewrite_everything(dbp)
    bad = _manage(dbp, "audit-verify", "--checkpoint", str(cp))
    assert "NOT PRESENT" in bad.stdout and bad.returncode != 0


# ── Evidence protection (2026-07-25) ─────────────────────────────────────────
# Both found by watching the reference deployment use the feature, not by review.

def test_checkpoint_refuses_to_overwrite_evidence(tmp_path):
    """A checkpoint is evidence, and the OLDEST is the most valuable — it
    attests to the longest stretch of history. In production a suggested
    filename with only date granularity silently replaced that morning's
    checkpoint, losing the one that covered 16,223 entries."""
    dbp = tmp_path / "a.db"
    cp = tmp_path / "cp.json"
    _chain(dbp, 5)

    first = _manage(dbp, "audit-checkpoint", "--out", str(cp))
    assert first.returncode == 0, first.stdout + first.stderr
    original = cp.read_text()

    second = _manage(dbp, "audit-checkpoint", "--out", str(cp))
    assert second.returncode != 0, "overwriting evidence must not be silent"
    assert "REFUSED" in second.stdout
    assert cp.read_text() == original, "the existing evidence must be untouched"

    forced = _manage(dbp, "audit-checkpoint", "--out", str(cp), "--force")
    assert forced.returncode == 0, "--force is the deliberate escape hatch"


def test_runbook_does_not_use_a_template_only_specifier():
    """The runbook is operational instruction, and a wrong systemd specifier
    there destroys evidence on a schedule: `%i` is the INSTANCE NAME, valid only
    in template units, and expands to nothing elsewhere — so every daily run
    would overwrite the same file.

    Scans whole `ini` blocks, not lines starting with `ExecStart`. The first
    version of this test did the latter and MISSED the bug when it was
    reintroduced, because the filename sits on a continuation line — the exact
    failure the 'assert the mechanism' principle describes, committed while
    adding that principle. A systemd directive is its first line AND everything
    it continues onto.
    """
    doc = (REPO / "docs" / "AUDIT-THREAT-MODEL.md").read_text()

    blocks, current, inside = [], [], False
    for line in doc.splitlines():
        if not inside and line.strip().startswith("```ini"):
            inside, current = True, []
        elif inside and line.strip().startswith("```"):
            blocks.append("\n".join(current))
            inside = False
        elif inside:
            current.append(line)

    assert blocks, "the runbook must contain systemd units"
    assert any("audit-checkpoint" in b for b in blocks), \
        "the runbook must contain a checkpoint unit"
    for b in blocks:
        for line in b.splitlines():
            if line.lstrip().startswith("#"):
                continue          # the comment names %i deliberately
            assert "%i" not in line, (
                "%i expands to nothing outside a template unit, so every run "
                f"overwrites the same file:\n  {line.strip()}")


def test_runbook_marks_the_schedule_as_deployment_not_protocol():
    """The project's central distinction — protocol vs deployment configuration —
    applies to this document too. The checkpoint MECHANISM is standardised; the
    routine that uses it is the operator's. Presenting the example systemd unit
    as 'copy-paste, in order' blurred exactly the line the rest of the project
    keeps sharp."""
    doc = (REPO / "docs" / "AUDIT-THREAT-MODEL.md").read_text().lower()
    assert "one worked example" in doc, \
        "the units must be marked as an example, not a requirement"
    assert "the protocol provides the mechanism" in doc
    assert "the deployment provides everything else" in doc


def test_spec_separates_normative_mechanism_from_deployment_routine():
    """The distinction must survive rewording: what a conforming hub MUST do is
    protocol; what an operator chooses is not. Asserts the substance rather than
    one sentence, since the wording has already changed once."""
    spec = (REPO / "spec" / "DoSync-SPEC-v0.1.md").read_text()
    assert "**Normative:**" in spec
    assert "Not normative" in spec
    assert "cannot\nverify that an operator uses it" in spec or \
           "cannot verify that an operator uses it" in spec


# ── Checkpoints are generated by default (2026-07-25) ───────────────────────
# The first design left the schedule entirely to the operator, on the grounds
# that frequency is a risk trade-off. But this specification already assigns
# defaults to comparable risk parameters — DOSYNC_UNREACHABLE_TTL at 1800s,
# DOSYNC_INTENT_TIMEOUT at 5000/10000ms — so "it depends on the deployment" was
# not a reason, it was an inconsistency. A guarantee that requires opt-in is one
# most installations will not have.

def test_hub_generates_checkpoints_on_a_schedule(tmp_path):
    import asyncio as _a

    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    for i in range(4):
        hub.audit_log.append({"type": "a", "n": i})
    out = tmp_path / "cps"

    async def run():
        t = _a.create_task(hub.start_checkpoint_scheduler(
            interval=1, directory=str(out)))
        await _a.sleep(2.5)
        t.cancel()
    _a.run(run())

    written = list(out.glob("cp-*.json"))
    assert written, "the hub must produce checkpoints without being asked"
    doc = json.loads(written[0].read_text())
    assert doc["entry_count"] == 4 and doc["head_hash"]


def test_generated_checkpoint_is_signed_and_verifiable(tmp_path):
    """A checkpoint the hub wrote must be exactly as usable as one the CLI
    wrote — the document's meaning cannot depend on which code path produced
    it, or it is worthless as evidence."""
    from dosync import cert_signing

    dbp = tmp_path / "a.db"
    hub = _chain(dbp, 5)
    path = hub.write_checkpoint(directory=str(tmp_path / "cps"))
    assert path is not None

    doc = json.loads(pathlib_read(path))
    ok, msg = cert_signing.verify_report(doc)
    assert ok, msg

    good = _manage(dbp, "audit-verify", "--checkpoint", path)
    assert "attested head found" in good.stdout, good.stdout

    _rewrite_everything(dbp)
    bad = _manage(dbp, "audit-verify", "--checkpoint", path)
    assert "NOT PRESENT" in bad.stdout and bad.returncode != 0


def pathlib_read(p):
    return Path(p).read_text()


def test_checkpoint_filenames_are_unique_per_run():
    """The production defect that motivated this: a date-only name overwrote
    that morning's checkpoint, and the one lost — attesting to a longer history
    — was the more valuable of the two."""
    from dosync.audit_backup import checkpoint_filename
    a = checkpoint_filename(now=1000.0)
    b = checkpoint_filename(now=1001.0)
    assert a != b, "one second apart must not collide"
    assert a.endswith("Z.json"), f"expected a UTC timestamp, got {a}"
    assert "T" in a, "date and time must both be present"


def test_scheduler_can_be_disabled_deliberately(tmp_path):
    """Zero disables it — but that is a decision someone made, not a default."""
    import asyncio as _a

    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})
    out = tmp_path / "cps"
    _a.run(hub.start_checkpoint_scheduler(interval=0, directory=str(out)))
    assert not out.exists() or not list(out.glob("*.json"))


def test_empty_chain_produces_no_checkpoint(tmp_path):
    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    assert hub.write_checkpoint(directory=str(tmp_path / "cps")) is None


def test_spec_states_the_default_interval():
    """The default belongs in the specification, not only in code — an
    implementer reading the spec must learn what a conforming hub does."""
    spec = (REPO / "spec" / "DoSync-SPEC-v0.1.md").read_text()
    assert "DOSYNC_CHECKPOINT_INTERVAL" in spec
    assert "86400" in spec
    assert "SHOULD generate checkpoints automatically" in spec


def test_the_default_is_enabled_and_daily(monkeypatch, tmp_path):
    """The point of the whole change, and the one thing the other tests do NOT
    check — they all pass `interval=1` explicitly, so hardcoding the default to
    0 leaves every one of them green. Fifth instance of 'assert the mechanism,
    not a symptom': the scheduler being *able* to run is not the same claim as
    it running *by default*.
    """
    import asyncio as _a
    import inspect

    monkeypatch.delenv("DOSYNC_CHECKPOINT_INTERVAL", raising=False)
    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})

    captured = {}

    async def fake_sleep(seconds):
        captured["interval"] = seconds
        raise _a.CancelledError          # one cycle is enough to read the value

    monkeypatch.setattr(_a, "sleep", fake_sleep)
    _a.run(hub.start_checkpoint_scheduler(directory=str(tmp_path / "cps")))

    assert "interval" in captured, \
        "with no configuration the scheduler must RUN, not return early"
    assert captured["interval"] == 86400, \
        f"the default interval must be daily, got {captured['interval']}"

    # and the number is where an implementer would look for it
    src = inspect.getsource(hub.start_checkpoint_scheduler)
    assert '"86400"' in src


# ── Export is a standard configuration point (2026-07-25) ───────────────────
# The destination cannot have a universal default — but its ABSENCE must not be
# silent, because a hub quietly producing artifacts nobody collects is the
# failure mode this whole layer exists to prevent.

def test_missing_export_is_reported_not_silent(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.delenv("DOSYNC_CHECKPOINT_EXPORT_DIR", raising=False)
    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})

    with caplog.at_level(logging.WARNING):
        hub.write_checkpoint(directory=str(tmp_path / "cps"))

    assert hub._checkpoint_export_state == "not_configured"
    assert any("NOT exported" in str(r.msg) for r in caplog.records), \
        "a hub that is not exporting must say so"


def test_configured_export_copies_the_artifact(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    monkeypatch.setenv("DOSYNC_CHECKPOINT_EXPORT_DIR", str(remote))
    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})

    path = hub.write_checkpoint(directory=str(tmp_path / "cps"))
    assert path
    copied = list(remote.glob("cp-*.json"))
    assert copied, "the checkpoint must reach the configured destination"
    assert copied[0].read_text() == Path(path).read_text(), \
        "the exported copy must be identical, or it attests to something else"
    assert hub._checkpoint_export_state == "ok"


def test_failed_export_is_an_error_not_a_shrug(tmp_path, monkeypatch, caplog):
    """A silent export failure leaves an operator believing they have evidence
    they do not have — worse than not exporting at all."""
    import logging
    # a path that cannot be created: a file where a directory must go
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setenv("DOSYNC_CHECKPOINT_EXPORT_DIR", str(blocker / "sub"))

    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})
    with caplog.at_level(logging.ERROR):
        path = hub.write_checkpoint(directory=str(tmp_path / "cps"))

    assert path, "a failed export must not lose the local checkpoint"
    assert hub._checkpoint_export_state == "failed"
    assert any("FAILED" in str(r.msg) for r in caplog.records)


def test_spec_documents_the_export_configuration_point():
    """Both settings must be in the spec: an implementer reading it has to learn
    that pull is supported, or they will reproduce the bug where the warning
    fires loudest at the strongest arrangement."""
    spec = (REPO / "spec" / "DoSync-SPEC-v0.1.md").read_text()
    assert "DOSYNC_CHECKPOINT_EXPORT_DIR" in spec
    assert "DOSYNC_CHECKPOINT_EXPORT_EXTERNAL" in spec
    # The substance, not one phrasing: neither setting has a default, and the
    # absence of both is required to be loud.
    assert "has a default" in spec or "no default" in spec
    assert "MUST warn" in spec


def test_external_collection_is_a_declarable_arrangement(tmp_path, monkeypatch, caplog):
    """Pull — something outside fetching checkpoints from the hub — is the
    STRONGEST arrangement, and in it DOSYNC_CHECKPOINT_EXPORT_DIR is correctly
    unset: pointing it at a mount the hub can write to would be a downgrade. So
    'unset' alone cannot mean 'misconfigured', or the warning fires loudest at
    the best setup and teaches operators to ignore warnings."""
    import logging

    monkeypatch.delenv("DOSYNC_CHECKPOINT_EXPORT_DIR", raising=False)
    monkeypatch.setenv("DOSYNC_CHECKPOINT_EXPORT_EXTERNAL", "true")

    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})
    with caplog.at_level(logging.WARNING):
        path = hub.write_checkpoint(directory=str(tmp_path / "cps"))

    assert path, "the checkpoint is still written — something else collects it"
    assert hub._checkpoint_export_state == "external"
    assert not [r for r in caplog.records if "NOT exported" in str(r.msg)], \
        "a declared pull arrangement must not be warned about"


def test_neither_setting_still_warns(tmp_path, monkeypatch, caplog):
    """The declaration is a statement of fact, not a way to silence the check:
    with neither set, the warning stands."""
    import logging

    monkeypatch.delenv("DOSYNC_CHECKPOINT_EXPORT_DIR", raising=False)
    monkeypatch.delenv("DOSYNC_CHECKPOINT_EXPORT_EXTERNAL", raising=False)

    hub = DoSyncHub(db_path=str(tmp_path / "a.db"))
    hub.audit_log.append({"type": "a"})
    with caplog.at_level(logging.WARNING):
        hub.write_checkpoint(directory=str(tmp_path / "cps"))

    assert hub._checkpoint_export_state == "not_configured"
    assert any("NOT exported" in str(r.msg) for r in caplog.records)


# ── Restarts must not starve the schedule (2026-07-25) ──────────────────────

def test_a_hub_that_restarts_often_still_produces_checkpoints(tmp_path):
    """Found by pulling `cp-*.json` after a day of work and getting nothing.

    The scheduler slept BEFORE its first write, so every restart reset a 24-hour
    timer that never elapsed — a hub restarting more often than the interval
    produced no evidence at all, silently. Deployments restart for updates,
    power and configuration; an interval that only survives uninterrupted uptime
    is not an interval.
    """
    import asyncio as _a

    dbp = tmp_path / "a.db"
    out = tmp_path / "cps"

    async def short_life(cycle):
        hub = DoSyncHub(db_path=str(dbp))
        hub.audit_log.append({"type": "a", "n": cycle})
        t = _a.create_task(hub.start_checkpoint_scheduler(
            interval=3600, directory=str(out)))     # far longer than the hub lives
        await _a.sleep(0.3)
        t.cancel()

    async def run():
        for c in range(3):
            await short_life(c)
    _a.run(run())

    written = list(out.glob("cp-*.json"))
    assert written, "a short-lived hub must still produce a checkpoint"


def test_restarts_do_not_produce_a_checkpoint_each_time(tmp_path):
    """The other half: writing on every start would flood the directory on a
    hub that restart-loops, and each file claims to be periodic evidence."""
    import asyncio as _a

    dbp = tmp_path / "a.db"
    out = tmp_path / "cps"

    async def run():
        for c in range(4):
            hub = DoSyncHub(db_path=str(dbp))
            hub.audit_log.append({"type": "a", "n": c})
            t = _a.create_task(hub.start_checkpoint_scheduler(
                interval=3600, directory=str(out)))
            await _a.sleep(0.3)
            t.cancel()
    _a.run(run())

    assert len(list(out.glob("cp-*.json"))) == 1, \
        "only the first start was overdue; the rest must respect the interval"


def test_the_checkpoint_time_survives_the_collector_deleting_files(tmp_path):
    """In a pull arrangement the collector may remove checkpoints once fetched.
    Reading 'no files' as 'never checkpointed' would write a fresh one on every
    restart, so the timestamp lives in the database instead."""
    import asyncio as _a

    dbp = tmp_path / "a.db"
    out = tmp_path / "cps"

    async def first():
        hub = DoSyncHub(db_path=str(dbp))
        hub.audit_log.append({"type": "a"})
        t = _a.create_task(hub.start_checkpoint_scheduler(
            interval=3600, directory=str(out)))
        await _a.sleep(0.3)
        t.cancel()
    _a.run(first())

    for f in out.glob("cp-*.json"):        # the collector took them
        f.unlink()

    async def second():
        hub = DoSyncHub(db_path=str(dbp))
        t = _a.create_task(hub.start_checkpoint_scheduler(
            interval=3600, directory=str(out)))
        await _a.sleep(0.3)
        t.cancel()
    _a.run(second())

    assert not list(out.glob("cp-*.json")), \
        "an empty directory is not evidence of a missed interval"
