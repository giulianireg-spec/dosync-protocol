"""
DoSync Persistence + Audit Chain Validation

Covers the DoSyncDB persistence layer and the AuditLog tamper-evident chain:
device registry, intent classes (including is_universal preservation),
API keys, rate-limit events, audit log persistence, and — most importantly —
the SHA-256 chain integrity guarantees (valid chain, tampered entry detected,
broken link detected).

Run: python3 -m pytest tests/test_db.py -v
  or: python3 tests/test_db.py
"""

import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.db import DoSyncDB
from dosync.hub import AuditLog


# ── Fixtures ──────────────────────────────────────────────────────────────────

def fresh_db():
    """In-memory DB, initialized with schema + universal intent seed."""
    db = DoSyncDB(":memory:")
    db.init()
    return db


# ── Device registry ───────────────────────────────────────────────────────────

def test_save_and_load_device():
    db = fresh_db()
    manifest = {"device_id": "lock-01", "tags": ["lock"], "actuators": []}
    db.save_device("lock-01", manifest)

    devices = db.load_devices()
    ids = [d["device_id"] for d in devices]
    assert "lock-01" in ids, "saved device must appear in load_devices()"


def test_save_device_is_upsert():
    """Saving the same device_id twice updates, does not duplicate."""
    db = fresh_db()
    db.save_device("lock-01", {"device_id": "lock-01", "tags": ["lock"]})
    db.save_device("lock-01", {"device_id": "lock-01", "tags": ["lock", "entrance"]})

    devices = [d for d in db.load_devices() if d["device_id"] == "lock-01"]
    assert len(devices) == 1, "upsert must not create a duplicate row"
    assert "entrance" in devices[0]["tags"], "second save must update the manifest"


def test_delete_device():
    db = fresh_db()
    db.save_device("lock-01", {"device_id": "lock-01", "tags": ["lock"]})
    db.delete_device("lock-01")
    ids = [d["device_id"] for d in db.load_devices()]
    assert "lock-01" not in ids, "deleted device must be gone"


def test_device_count():
    db = fresh_db()
    assert db.device_count() == 0
    db.save_device("a", {"device_id": "a"})
    db.save_device("b", {"device_id": "b"})
    assert db.device_count() == 2


# ── Intent classes ────────────────────────────────────────────────────────────

def test_universal_intents_seeded():
    """init() must seed the 5 universal intent classes."""
    db = fresh_db()
    names = {ic["name"] for ic in db.list_intent_classes()}
    for required in ["ensure_safety", "alert_anomaly", "control_access", "report_status", "notify"]:
        assert required in names, f"universal intent '{required}' must be seeded"


def test_universal_intents_marked_universal():
    db = fresh_db()
    ensure = db.get_intent_class("ensure_safety")
    assert ensure is not None
    assert ensure["is_universal"] is True, "seeded intents must be flagged universal"


def test_save_custom_intent_class():
    db = fresh_db()
    db.save_intent_class(
        "save_energy", "info",
        ["light", "plug"], ["turn_off"],
        "Reduce power", "residential",
    )
    ic = db.get_intent_class("save_energy")
    assert ic is not None
    assert ic["resolution_tags"] == ["light", "plug"]
    assert ic["is_universal"] is False, "custom intent must not be universal"


def test_save_intent_class_preserves_is_universal():
    """Updating a universal intent must NOT flip its is_universal flag."""
    db = fresh_db()
    # ensure_safety is universal; update it via save_intent_class
    db.save_intent_class(
        "ensure_safety", "emergency",
        ["emergency", "alarm"], ["alarm"],
        "Updated description", "universal",
    )
    ic = db.get_intent_class("ensure_safety")
    assert ic["is_universal"] is True, "is_universal must be preserved on update"


def test_delete_intent_class():
    db = fresh_db()
    db.save_intent_class("temp", "info", [], [], "tmp", "test")
    assert db.delete_intent_class("temp") is True
    assert db.get_intent_class("temp") is None
    assert db.delete_intent_class("temp") is False, "deleting a missing class returns False"


def test_get_missing_intent_class_returns_none():
    db = fresh_db()
    assert db.get_intent_class("does_not_exist") is None


# ── API keys ──────────────────────────────────────────────────────────────────

def test_api_key_save_and_verify():
    db = fresh_db()
    db.save_api_key("hash-abc", "dashboard")
    assert db.verify_api_key("hash-abc") is True
    assert db.verify_api_key("wrong-hash") is False


def test_has_any_key():
    db = fresh_db()
    assert db.has_any_key() is False
    db.save_api_key("hash-1", "k1")
    assert db.has_any_key() is True


def test_delete_api_key():
    db = fresh_db()
    db.save_api_key("hash-1", "k1")
    assert db.delete_api_key("hash-1") is True
    assert db.verify_api_key("hash-1") is False


# ── Rate limit events ─────────────────────────────────────────────────────────

def test_rate_limit_event_roundtrip():
    db = fresh_db()
    now = time.time()
    db.append_rate_limit_event("dev-1", now)
    db.append_rate_limit_event("dev-1", now)
    events = db.load_rate_limit_events(window_seconds=60)
    assert "dev-1" in events
    assert len(events["dev-1"]) == 2, "both events within window must load"


def test_rate_limit_purge_old_events():
    db = fresh_db()
    old = time.time() - 1000
    db.append_rate_limit_event("dev-old", old)
    purged = db.purge_rate_limit_events(window_seconds=60)
    assert purged >= 1, "old event outside window must be purged"
    events = db.load_rate_limit_events(window_seconds=60)
    assert "dev-old" not in events


# ── Audit log persistence ─────────────────────────────────────────────────────

def test_audit_append_and_count():
    db = fresh_db()
    db.append_audit({"event": "intent_executed", "hash": "abc", "timestamp": time.time()})
    assert db.audit_count() == 1


def test_audit_load_preserves_order():
    db = fresh_db()
    db.append_audit({"event": "first",  "hash": "h1", "timestamp": 100.0})
    db.append_audit({"event": "second", "hash": "h2", "timestamp": 200.0})
    log_entries = db.load_audit_log()
    assert [e["event"] for e in log_entries] == ["first", "second"], \
        "audit log must load in timestamp order"


# ── AuditLog chain integrity (the tamper-evident core) ────────────────────────

def test_audit_chain_valid():
    audit = AuditLog()
    audit.append({"event": "a"})
    audit.append({"event": "b"})
    audit.append({"event": "c"})
    assert audit.verify() is True, "an untampered chain must verify"


def test_audit_chain_first_hash_links_to_genesis():
    audit = AuditLog()
    audit.append({"event": "a"})
    entries = audit.entries()
    assert entries[0]["prev_hash"] == "0" * 64, "first entry must link to the genesis hash"


def test_audit_chain_each_links_to_previous():
    audit = AuditLog()
    audit.append({"event": "a"})
    audit.append({"event": "b"})
    entries = audit.entries()
    assert entries[1]["prev_hash"] == entries[0]["hash"], \
        "each entry's prev_hash must equal the previous entry's hash"


def test_audit_chain_detects_tampered_payload():
    """Modifying an entry's content after the fact must break verification."""
    audit = AuditLog()
    audit.append({"event": "a"})
    audit.append({"event": "b"})
    # Tamper: change the payload of the first entry without recomputing the hash
    audit._entries[0]["event"] = "MODIFIED"
    assert audit.verify() is False, "tampered payload must be detected"


def test_audit_chain_detects_broken_link():
    """Corrupting a prev_hash must break the chain even if the entry hash matches."""
    audit = AuditLog()
    audit.append({"event": "a"})
    audit.append({"event": "b"})
    # Tamper: break the link of the second entry
    audit._entries[1]["prev_hash"] = "f" * 64
    assert audit.verify() is False, "broken chain link must be detected"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
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
    print(f"\n{passed}/{passed+failed} db + audit tests passed.")
    sys.exit(1 if failed else 0)
