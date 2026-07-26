import pytest
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


# ── Choosing your own token (2026-07-26) ────────────────────────────────────

def test_operator_can_choose_the_token():
    """Raised by the operator: the only way into the dashboard was a 43-character
    random string, which nobody memorises, so it lives in a note or gets lost —
    which is exactly what happened to this project's author. Software people
    self-host lets them pick a password."""
    from dosync.auth import AuthManager
    from dosync.db import DoSyncDB

    db = DoSyncDB(":memory:"); db.init()
    auth = AuthManager(db)
    token = auth.generate_key(label="dashboard", token="my-house-2026-kitchen")
    assert token == "my-house-2026-kitchen"
    assert auth.verify(token) is True
    assert auth.verify("something-else-entirely") is False


def test_a_chosen_token_still_has_a_floor():
    """A bearer token is checked with no rate limit or lockout, so it is guessed
    offline at full speed — which makes a short one materially worse than a
    short login password."""
    import pytest

    from dosync.auth import AuthManager
    from dosync.db import DoSyncDB

    db = DoSyncDB(":memory:"); db.init()
    auth = AuthManager(db)
    with pytest.raises(ValueError):
        auth.generate_key(token="1234")
    with pytest.raises(ValueError):
        auth.generate_key(token="dosync")


def test_generated_tokens_are_still_the_default():
    """Choosing is an option, not a requirement — a program that will store the
    value should still get something random."""
    from dosync.auth import AuthManager
    from dosync.db import DoSyncDB

    db = DoSyncDB(":memory:"); db.init()
    auth = AuthManager(db)
    a = auth.generate_key(label="one")
    b = auth.generate_key(label="two")
    assert a != b and len(a) > 30


# ── Access management without a shell (2026-07-26) ──────────────────────────

@pytest.fixture
def access_hub(tmp_path, monkeypatch):
    """A freshly reloaded server module, RESTORED afterwards.

    These tests need module-level auth state rebuilt from the environment, which
    means reloading `dosync.server`. That replaces globals other test files
    already hold references to — the first version of this helper left
    authentication switched on for everything that ran later and broke five
    unrelated tests. Reloading is fine; not putting it back is not.
    """
    import importlib
    import os

    import dosync.server as srv

    original = {k: os.environ.get(k) for k in ("DOSYNC_DB", "DOSYNC_AUTH")}

    def build(env_auth=None):
        monkeypatch.setenv("DOSYNC_DB", str(tmp_path / "a.db"))
        if env_auth is None:
            monkeypatch.delenv("DOSYNC_AUTH", raising=False)
        else:
            monkeypatch.setenv("DOSYNC_AUTH", env_auth)
        importlib.reload(srv)
        from fastapi.testclient import TestClient
        tok = srv._auth_manager.ensure_default_key() or "seed"
        return srv, TestClient(srv.app), {"Authorization": f"Bearer {tok}"}

    yield build

    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(srv)


def _client(access_hub, env_auth=None):
    return access_hub(env_auth)


def test_operator_can_set_a_password_from_the_api(access_hub):
    srv, c, H = _client(access_hub)
    r = c.post("/v1/auth/token", headers=H,
               json={"token": "my-house-2026-kitchen", "label": "dashboard"})
    assert r.status_code == 200
    assert c.get("/v1/devices",
                 headers={"Authorization": "Bearer my-house-2026-kitchen"}
                 ).status_code == 200


def test_a_weak_password_is_refused_over_the_api(access_hub):
    srv, c, H = _client(access_hub)
    assert c.post("/v1/auth/token", headers=H, json={"token": "1234"}).status_code == 422


def test_turning_auth_off_requires_confirmation(access_hub):
    """One click should not open a hub. The confirmation is the difference
    between a decision and an accident."""
    srv, c, H = _client(access_hub)
    assert c.post("/v1/auth/mode", headers=H,
                  json={"auth_required": False}).status_code == 422
    assert c.post("/v1/auth/mode", headers=H,
                  json={"auth_required": False, "confirm": True}).status_code == 200
    assert c.get("/v1/devices").status_code == 200, "no token needed now"


def test_access_changes_land_in_the_audit_chain(access_hub):
    """'When did this hub become open, and who did it' must have an answer.
    The token VALUE is never written — the chain is readable by whoever can read
    the chain."""
    srv, c, H = _client(access_hub)
    c.post("/v1/auth/token", headers=H, json={"token": "a-chosen-passphrase"})
    c.post("/v1/auth/mode", headers=H, json={"auth_required": False, "confirm": True})

    types = [e["type"] for e in srv.hub.audit_log.entries()]
    assert "auth_token_created" in types
    assert "auth_mode_changed" in types
    raw = str(srv.hub.audit_log.entries())
    assert "a-chosen-passphrase" not in raw, "the token must never enter the chain"


def test_the_environment_wins_over_the_dashboard(access_hub):
    """An operator who wrote DOSYNC_AUTH into a unit file expects it to hold; a
    click in a browser must not quietly override the machine's declaration. The
    hub says so rather than letting the toggle appear broken."""
    srv, c, H = _client(access_hub, env_auth="true")
    mode = c.get("/v1/auth/mode", headers=H).json()
    assert mode["source"] == "environment" and mode["env_override"] is True
    r = c.post("/v1/auth/mode", headers=H,
               json={"auth_required": False, "confirm": True})
    assert r.status_code == 409, "and refuses with an explanation, not silently"
    assert "environment" in r.json()["detail"]


def test_auth_default_is_on(access_hub):
    """With nothing configured anywhere, a hub must not start open."""
    srv, c, H = _client(access_hub)
    assert c.get("/v1/auth/mode", headers=H).json()["auth_required"] is True
    assert c.get("/v1/devices").status_code == 401
