"""
DoSync Server HTTP Layer Validation

Covers the FastAPI HTTP layer with the in-memory TestClient: response-contract
fields, auth enforcement (401), and Pydantic input validation (422). No network,
no Pi, no hardware.

TESTING PHILOSOPHY (decided by architecture panel):
This suite is deliberately SCOPED. The certification suite already exercises the
happy path of all 30 endpoints against the real hub on the Pi — re-testing that
here would be coverage theatre. What this suite adds, and the cert does NOT:
  - Speed / shift-left: runs in CI in milliseconds with no Pi, so a broken
    endpoint is caught in the PR, not after deploy.
  - Error-path validation: systematic checks of malformed payloads, missing
    fields, and auth rejection that the cert's happy-path runs don't cover.
  - Isolation: pure HTTP-layer behaviour, decoupled from device state / network.
Each test notes why the cert doesn't already cover it. We do NOT re-test happy
paths the cert owns.

Run: DOSYNC_AUTH=false python3 -m pytest tests/test_server.py -v
  or: DOSYNC_AUTH=false python3 tests/test_server.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Auth is configured at import time from DOSYNC_AUTH. The default here disables
# it so most contract/validation tests don't need a token; the auth-enforcement
# test re-enables it explicitly via a separate manager.
os.environ.setdefault("DOSYNC_AUTH", "false")

from fastapi.testclient import TestClient
import server
from dosync.auth import AuthManager, set_auth_manager

client = TestClient(server.app)


# ── Response contract ─────────────────────────────────────────────────────────

def test_api_root_contract():
    """Why not cert: cert checks /v1/status; this pins the /api summary contract
    (name, protocol, version) in isolation, fast, without the Pi."""
    r = client.get("/api")
    assert r.status_code == 200
    body = r.json()
    for field in ["name", "version", "protocol", "status", "devices_registered"]:
        assert field in body, f"/api response must include '{field}'"
    assert body["protocol"] == "dosync/0.1"


def test_status_endpoint_has_versions():
    """Why not cert: shift-left guard so a missing version field fails in CI,
    not at deploy time against the Pi (cert S19)."""
    r = client.get("/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert "protocol_version" in body
    assert "api_version" in body


# ── Input validation (422) — error paths the cert doesn't systematically hit ──

def test_register_device_missing_fields_422():
    """Why not cert: cert registers VALID devices; this pins that a malformed
    body (missing required device_id) is rejected with 422 by Pydantic."""
    r = client.post("/v1/devices/register", json={"device_name": "no id"})
    assert r.status_code == 422, "missing required fields must yield 422"


def test_event_missing_severity_422():
    """Why not cert: EventRequest requires severity; cert sends well-formed
    events. This pins the validation boundary."""
    r = client.post("/v1/event", json={"device_id": "x", "event_id": "motion"})
    assert r.status_code == 422, "missing required 'severity' must yield 422"


def test_intent_explain_unknown_class_handled():
    """Why not cert: the explain endpoint must not 500 on an unknown intent
    class — it should return a normal status with an empty/zero breakdown."""
    r = client.get("/v1/intents/not_a_real_intent/explain")
    assert r.status_code in (200, 404, 422), \
        f"unknown intent must be handled gracefully, got {r.status_code}"
    assert r.status_code != 500, "must never 500 on unknown intent class"


# ── Auth enforcement (401) ────────────────────────────────────────────────────

def test_protected_endpoint_rejects_without_token_when_auth_on():
    """Why not cert: cert B04 checks 401 against the Pi; this isolates the same
    guarantee in-memory so an auth regression fails fast in CI. We flip auth ON
    for this test only, then restore the disabled manager."""
    # Enable auth with an in-memory DB manager
    from dosync.db import DoSyncDB
    db = DoSyncDB(":memory:"); db.init()
    enabled_mgr = AuthManager(db, enabled=True)
    enabled_mgr.generate_key("test")
    set_auth_manager(enabled_mgr)
    try:
        # A protected endpoint without Authorization header must be 401/403
        r = client.get("/v1/devices")
        assert r.status_code in (401, 403), \
            f"protected endpoint must reject missing token, got {r.status_code}"
    finally:
        # Restore disabled auth so later tests are unaffected
        set_auth_manager(AuthManager(db, enabled=False))


def test_protected_endpoint_allows_with_auth_disabled():
    """With auth disabled (the default in this suite), protected endpoints are
    reachable — confirms the dev-bypass path end to end through HTTP."""
    set_auth_manager(AuthManager.__new__(AuthManager))  # placeholder safety
    from dosync.db import DoSyncDB
    db = DoSyncDB(":memory:"); db.init()
    set_auth_manager(AuthManager(db, enabled=False))
    r = client.get("/v1/devices")
    assert r.status_code == 200, "auth disabled must allow protected endpoints"


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
    print(f"\n{passed}/{passed+failed} server tests passed.")
    sys.exit(1 if failed else 0)
