"""Tests for audit log backup/restore/verify (REL-2)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from dosync import audit_backup as ab
from dosync.hub import AuditLog


def _chained_entries(n=5):
    """Produce n properly-chained entries using the real AuditLog."""
    log = AuditLog()
    for i in range(n):
        log.append({"action": "intent_executed", "intent": f"i{i}", "device": f"d{i}"})
    return log.entries()


def test_backup_roundtrip_preserves_chain():
    entries = _chained_entries()
    doc = ab.build_backup(entries)
    assert doc["manifest"]["count"] == 5
    assert doc["manifest"]["chain_valid_at_backup"] is True
    assert ab.verify_entries(doc["entries"]) is True


def test_write_read_roundtrip(tmp_path):
    entries = _chained_entries()
    p = tmp_path / "b.json"
    ab.write_backup(entries, str(p))
    doc = ab.read_backup(str(p))
    assert doc["entries"] == entries


def test_altered_backup_file_is_detected(tmp_path):
    entries = _chained_entries()
    p = tmp_path / "b.json"
    ab.write_backup(entries, str(p))
    doc = json.loads(p.read_text())
    doc["entries"][2]["device"] = "TAMPERED"     # change payload, keep manifest checksum
    p.write_text(json.dumps(doc))
    try:
        ab.read_backup(str(p))
        assert False, "altered backup should have been rejected"
    except ValueError as e:
        assert "checksum mismatch" in str(e)


def test_broken_chain_detected():
    entries = _chained_entries()
    entries[3]["prev_hash"] = "0" * 64           # break the link
    assert ab.verify_entries(entries) is False


def test_missing_hash_is_invalid():
    entries = _chained_entries()
    del entries[1]["hash"]
    assert ab.verify_entries(entries) is False


def test_cli_backup_verify_restore_cycle(tmp_path):
    """End-to-end through manage.py against a throwaway DB."""
    db = tmp_path / "t.db"
    repo = Path(__file__).resolve().parent.parent

    # seed a couple of audit entries via the DB layer directly
    sys.path.insert(0, str(repo))
    from dosync.db import DoSyncDB
    from dosync.hub import AuditLog
    d = DoSyncDB(str(db)); d.init()
    log = AuditLog(); log._persist_cb = d.append_audit
    for i in range(4):
        log.append({"action": "intent_executed", "intent": f"i{i}"})

    def run(*a):
        return subprocess.run([sys.executable, "manage.py", "--db", str(db), "db", *a],
                              cwd=str(repo), capture_output=True, text=True)

    bak = tmp_path / "bak.json"
    r = run("audit-backup", "--out", str(bak))
    assert r.returncode == 0 and bak.exists(), r.stdout + r.stderr
    assert "Entries:       4" in r.stdout

    r = run("audit-verify")
    assert r.returncode == 0 and "yes" in r.stdout, r.stdout + r.stderr

    # restore into a fresh DB and verify
    db2 = tmp_path / "t2.db"
    DoSyncDB(str(db2)).init()
    r = subprocess.run([sys.executable, "manage.py", "--db", str(db2), "db",
                        "audit-restore", "--file", str(bak)],
                       cwd=str(repo), capture_output=True, text=True)
    assert r.returncode == 0 and "Chain valid: yes" in r.stdout, r.stdout + r.stderr


def test_backup_reports_write_failure_cleanly(tmp_path):
    """A systemd timer runs this unattended: an OSError must produce an
    actionable message and exit 1, not a Python traceback in the journal.

    Found 2026-07-14 the hard way — the daily backup timer died with a raw
    PermissionError traceback because the documented install steps created
    /var/backups/dosync as root while the unit runs as a normal user.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    db = tmp_path / "t.db"
    sys.path.insert(0, str(repo))
    from dosync.db import DoSyncDB
    from dosync.hub import AuditLog
    d = DoSyncDB(str(db)); d.init()
    log = AuditLog(); log._persist_cb = d.append_audit
    log.append({"action": "test"})

    r = subprocess.run(
        [sys.executable, "manage.py", "--db", str(db), "db", "audit-backup",
         "--out", "/nonexistent-dir-for-tests/x.json"],
        cwd=str(repo), capture_output=True, text=True)

    assert r.returncode == 1, "must exit non-zero so systemd marks the failure"
    assert "Traceback" not in (r.stdout + r.stderr), "raw traceback in an unattended tool"
    assert "FAILED" in r.stdout and "Cannot write" in r.stdout, r.stdout
