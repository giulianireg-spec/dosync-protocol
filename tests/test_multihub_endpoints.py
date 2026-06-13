"""
DoSync Multi-Hub Endpoint Validation (Phase A)

Integration tests for the three multi-hub HTTP surfaces via TestClient:
  - multi_hub_capable flag in /v1/hub/heartbeat (§11.8)
  - GET /v1/hub/peers (monitor view; inert on primary)
  - POST /v1/hub/promote (operator-assisted promotion; 409 on destructive)

These complement test_hub_monitor.py (pure state machine). Here we verify the
HTTP wiring. The live two-machine partition behaviour is the operator's
hands-on test on the real Pi+Mac topology, not a unit test.

Run: DOSYNC_AUTH=false python3 tests/test_multihub_endpoints.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DOSYNC_AUTH", "false")

from fastapi.testclient import TestClient
import server

client = TestClient(server.app)


# ── Heartbeat declares multi-hub capability (§11.8) ───────────────────────────

def test_heartbeat_declares_multi_hub_capable():
    body = client.get("/v1/hub/heartbeat").json()
    assert body.get("multi_hub_capable") is True


# ── Primary mode: monitor inert ───────────────────────────────────────────────

def test_peers_inert_on_primary():
    """Default role is primary — the monitor is not running."""
    body = client.get("/v1/hub/peers").json()
    assert body["role"] == "primary"
    assert body["monitor_state"] == "n/a"


def test_promote_rejected_on_primary():
    """A primary has no monitor — promotion must be rejected, not crash."""
    r = client.post("/v1/hub/promote", json={})
    assert r.status_code == 400, "promote on a primary must 400 (not a standby)"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
    print(f"\n{passed}/{passed+failed} multi-hub endpoint tests passed.")
    sys.exit(1 if failed else 0)
