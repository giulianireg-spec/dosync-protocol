"""
DoSync Auth + Security Validation

Covers the authentication layer (auth.py) and the PKI status logic (security.py):
token hashing (never stored plaintext), AuthManager API-key lifecycle including
the dev-bypass and demo-token paths, DeviceAuthManager permissive/strict modes
with the critical "provisioned device + wrong token is always rejected" rule,
and the CertInfo/PKIStatus expiry + readiness properties.

Run: python3 -m pytest tests/test_auth.py -v
  or: python3 tests/test_auth.py
"""

import sys, os
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.db import DoSyncDB
from dosync.auth import hash_token, AuthManager, DeviceAuthManager
from dosync.security import CertInfo, PKIStatus


def fresh_db():
    db = DoSyncDB(":memory:")
    db.init()
    # Production initializes the device_tokens table separately (server.py:168),
    # right before constructing the DeviceAuthManager. Mirror that contract here.
    db.init_device_tokens_table()
    return db


# ── Token hashing ─────────────────────────────────────────────────────────────

def test_hash_token_is_sha256_hex():
    h = hash_token("my-secret-token")
    assert len(h) == 64, "SHA-256 hex digest must be 64 chars"
    assert all(c in "0123456789abcdef" for c in h), "must be lowercase hex"


def test_hash_token_deterministic():
    assert hash_token("abc") == hash_token("abc"), "same input → same hash"
    assert hash_token("abc") != hash_token("abd"), "different input → different hash"


def test_hash_token_never_returns_plaintext():
    secret = "super-secret-value"
    assert secret not in hash_token(secret), "hash must not contain the plaintext"


# ── AuthManager: API key lifecycle ────────────────────────────────────────────

def test_auth_generate_and_verify():
    mgr = AuthManager(fresh_db(), enabled=True)
    token = mgr.generate_key("dashboard")
    assert mgr.verify(token) is True, "freshly generated key must verify"
    assert mgr.verify("wrong-token") is False, "unknown token must not verify"


def test_auth_stores_only_hash_not_plaintext():
    """Critical: the DB must never hold the plaintext token."""
    db = fresh_db()
    mgr = AuthManager(db, enabled=True)
    token = mgr.generate_key("k1")
    keys = db.list_api_keys()
    serialized = str(keys)
    assert token not in serialized, "plaintext token must never be persisted"


def test_auth_disabled_passes_everything():
    """enabled=False is the dev bypass — every token (even garbage) passes."""
    mgr = AuthManager(fresh_db(), enabled=False)
    assert mgr.verify("anything") is True
    assert mgr.verify("") is True


def test_ensure_default_key_generates_once():
    mgr = AuthManager(fresh_db(), enabled=True)
    first = mgr.ensure_default_key()
    assert first is not None, "first call must generate a key"
    second = mgr.ensure_default_key()
    assert second is None, "second call must NOT generate another key"


def test_ensure_default_key_uses_demo_token_env(monkeypatch=None):
    """When DOSYNC_DEMO_TOKEN is set, it must be used as the initial token."""
    os.environ["DOSYNC_DEMO_TOKEN"] = "demo-token-12345"
    try:
        mgr = AuthManager(fresh_db(), enabled=True)
        result = mgr.ensure_default_key()
        assert result == "demo-token-12345", "demo token must be used verbatim"
        assert mgr.verify("demo-token-12345") is True, "demo token must verify"
    finally:
        del os.environ["DOSYNC_DEMO_TOKEN"]


def test_ensure_default_key_disabled_returns_none():
    mgr = AuthManager(fresh_db(), enabled=False)
    assert mgr.ensure_default_key() is None, "disabled auth must not generate keys"


def test_auth_delete_key():
    db = fresh_db()
    mgr = AuthManager(db, enabled=True)
    token = mgr.generate_key("k1")
    key_hash = hash_token(token)
    assert mgr.delete_key(key_hash) is True
    assert mgr.verify(token) is False, "deleted key must no longer verify"


# ── DeviceAuthManager: per-device provisioning ────────────────────────────────

def test_device_provision_and_verify():
    mgr = DeviceAuthManager(fresh_db())
    token = mgr.provision("lock-01", "front door")
    valid, reason = mgr.verify("lock-01", token)
    assert valid is True and reason == "ok", "correct device token must verify"


def test_device_provisioned_with_wrong_token_always_rejected():
    """Security-critical: a provisioned device with a bad token is rejected
    even in permissive mode."""
    mgr = DeviceAuthManager(fresh_db())
    assert mgr.strict is False, "default mode is permissive"
    mgr.provision("lock-01", "front door")
    valid, reason = mgr.verify("lock-01", "WRONG-TOKEN")
    assert valid is False, "provisioned device + wrong token must be rejected"
    assert "Invalid token" in reason


def test_device_unprovisioned_permissive_allows():
    """In permissive mode, an unprovisioned device is allowed (legacy compat)."""
    mgr = DeviceAuthManager(fresh_db())
    valid, reason = mgr.verify("never-seen", "any-token")
    assert valid is True, "permissive mode allows unprovisioned devices"
    assert "permissive" in reason


def test_device_unprovisioned_strict_rejects():
    """In strict mode, an unprovisioned device is rejected."""
    os.environ["DOSYNC_DEVICE_AUTH"] = "strict"
    try:
        mgr = DeviceAuthManager(fresh_db())
        assert mgr.strict is True
        valid, reason = mgr.verify("never-seen", "any-token")
        assert valid is False, "strict mode rejects unprovisioned devices"
        assert "not provisioned" in reason
    finally:
        del os.environ["DOSYNC_DEVICE_AUTH"]


def test_device_revoke():
    mgr = DeviceAuthManager(fresh_db())
    token = mgr.provision("lock-01")
    assert mgr.is_provisioned("lock-01") is True
    assert mgr.revoke("lock-01") is True
    assert mgr.is_provisioned("lock-01") is False


# ── security.py: CertInfo / PKIStatus logic (no real certs needed) ────────────

def make_cert(days_until_expiry):
    return CertInfo(
        subject="CN=hub", issuer="CN=DoSync CA",
        not_before="2026-01-01", not_after="2027-01-01",
        serial="01", is_ca=False, path=Path("/tmp/hub.crt"),
        days_until_expiry=days_until_expiry,
    )


def test_cert_is_expired():
    assert make_cert(-1).is_expired is True, "negative days → expired"
    assert make_cert(0).is_expired is True, "zero days → expired"
    assert make_cert(10).is_expired is False, "positive days → not expired"


def test_cert_is_expiring_soon():
    assert make_cert(15).is_expiring_soon is True, "within 30 days → expiring soon"
    assert make_cert(30).is_expiring_soon is True, "exactly 30 → expiring soon"
    assert make_cert(31).is_expiring_soon is False, "beyond 30 → not soon"
    assert make_cert(0).is_expiring_soon is False, "already expired → not 'soon'"


def test_pki_status_ready_requires_ca_and_hub():
    assert PKIStatus(ca_exists=True, hub_cert_exists=True).is_ready is True
    assert PKIStatus(ca_exists=True, hub_cert_exists=False).is_ready is False
    assert PKIStatus(ca_exists=False, hub_cert_exists=True).is_ready is False


def test_pki_status_not_ready_with_errors():
    status = PKIStatus(ca_exists=True, hub_cert_exists=True, errors=["cert mismatch"])
    assert status.is_ready is False, "any error must make PKI not ready"


# ── Framework-agnostic core (regression guard) ────────────────────────────────

def test_auth_core_imports_without_fastapi():
    """Regression guard: the auth core must import even when FastAPI is absent.

    A prior bug had require_auth defined at module level using a FastAPI symbol
    that only existed when the import succeeded — so `import dosync.auth` blew up
    in any environment without FastAPI. The fix moved the FastAPI dependencies to
    dosync/auth_fastapi.py. This test simulates a FastAPI-less environment and
    asserts the core still imports and works, and that the glue module correctly
    requires FastAPI."""
    import builtins, importlib
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "fastapi" or name.startswith("fastapi."):
            raise ImportError(f"No module named '{name}' (simulated)")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = blocked_import
    try:
        # Drop cached modules so the import actually re-runs under the block
        for mod in list(sys.modules):
            if mod == "dosync.auth" or mod == "dosync.auth_fastapi":
                del sys.modules[mod]

        core = importlib.import_module("dosync.auth")
        assert hasattr(core, "hash_token"), "core must expose hash_token without FastAPI"
        assert len(core.hash_token("x")) == 64, "core function must work without FastAPI"

        # The glue module MUST fail without FastAPI — that's correct.
        try:
            importlib.import_module("dosync.auth_fastapi")
            assert False, "auth_fastapi must require FastAPI"
        except ImportError:
            pass
    finally:
        builtins.__import__ = real_import
        # Restore clean modules for any later tests
        for mod in list(sys.modules):
            if mod == "dosync.auth" or mod == "dosync.auth_fastapi":
                del sys.modules[mod]
        importlib.import_module("dosync.auth")


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
    print(f"\n{passed}/{passed+failed} auth + security tests passed.")
    sys.exit(1 if failed else 0)
