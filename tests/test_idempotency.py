"""
DoSync Idempotency Validation (protocol v0.2)

Covers the opt-in idempotency layer on POST /v1/intent/async:
  - No key            -> every request is unique (v0.1 behavior preserved)
  - Key + same body   -> cached intent_id returned, NOT re-executed (idempotent replay)
  - Key + diff body   -> 409 Conflict (anti-suppression)
  - Body hash         -> stable, excludes the idempotency key itself

DELIVERY MODEL: at-least-once with optional deduplication. This makes the retry
advised by the consistency model (§6, "the AI agent can fire the intent again")
safe for physical actions — a lock must not unlock twice on a network retry.

Run: DOSYNC_AUTH=false python3 -m pytest tests/test_idempotency.py -v
  or: DOSYNC_AUTH=false python3 tests/test_idempotency.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DOSYNC_AUTH", "false")

from fastapi.testclient import TestClient
import server

client = TestClient(server.app)


def _fire(body):
    return client.post("/v1/intent/async", json=body)


# ── No key: v0.1 behavior preserved ───────────────────────────────────────────

def test_no_key_each_request_is_unique():
    """Without an idempotency key, two identical requests get distinct intent_ids."""
    body = {"intent": "notify", "urgency": "info"}
    r1 = _fire(body)
    r2 = _fire(body)
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = r1.json()["intent_id"]
    id2 = r2.json()["intent_id"]
    assert id1 != id2, "without a key, each request must be a new intent (v0.1 behavior)"


def test_no_key_no_replay_flag():
    """Responses without a key must not carry the idempotent_replay flag."""
    r = _fire({"intent": "notify", "urgency": "info"})
    assert r.json().get("idempotent_replay") is not True


# ── Key + same body: idempotent replay ─────────────────────────────────────────

def test_same_key_same_body_returns_cached_intent():
    """A retry with the same key and body must return the SAME intent_id."""
    body = {"intent": "notify", "urgency": "info", "idempotency_key": "test-key-aaa"}
    r1 = _fire(body)
    r2 = _fire(body)
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = r1.json()["intent_id"]
    id2 = r2.json()["intent_id"]
    assert id1 == id2, "same key + same body must return the cached intent_id"


def test_same_key_same_body_marks_replay():
    """The deduplicated response must flag itself as an idempotent replay."""
    body = {"intent": "notify", "urgency": "info", "idempotency_key": "test-key-bbb"}
    _fire(body)  # first
    r2 = _fire(body)  # retry
    assert r2.json().get("idempotent_replay") is True, "retry must be marked as replay"


# ── Key + different body: anti-suppression ─────────────────────────────────────

def test_same_key_different_body_rejected_409():
    """Reusing a key with a different body must be rejected (anti-suppression)."""
    _fire({"intent": "notify", "urgency": "info", "idempotency_key": "test-key-ccc"})
    # Same key, different intent → must NOT silently dedup nor execute
    r = _fire({"intent": "ensure_safety", "urgency": "emergency", "idempotency_key": "test-key-ccc"})
    assert r.status_code == 409, "key reuse with different body must be 409 Conflict"


def test_different_keys_are_independent():
    """Different keys with the same body produce different intents."""
    b1 = {"intent": "notify", "urgency": "info", "idempotency_key": "test-key-d1"}
    b2 = {"intent": "notify", "urgency": "info", "idempotency_key": "test-key-d2"}
    id1 = _fire(b1).json()["intent_id"]
    id2 = _fire(b2).json()["intent_id"]
    assert id1 != id2, "distinct keys must yield distinct intents"


# ── Body hash stability ────────────────────────────────────────────────────────

def test_body_hash_excludes_idempotency_key():
    """The body hash must be identical whether or not the key field is present,
    so the same logical intent is recognized as the same."""
    from server import _intent_body_hash, IntentRequest
    a = IntentRequest(intent="notify", urgency="info", idempotency_key="k1")
    b = IntentRequest(intent="notify", urgency="info", idempotency_key="k2")
    c = IntentRequest(intent="notify", urgency="info")
    assert _intent_body_hash(a) == _intent_body_hash(b) == _intent_body_hash(c), \
        "body hash must exclude the idempotency key itself"


def test_body_hash_differs_on_content():
    from server import _intent_body_hash, IntentRequest
    a = IntentRequest(intent="notify", urgency="info")
    b = IntentRequest(intent="ensure_safety", urgency="emergency")
    assert _intent_body_hash(a) != _intent_body_hash(b), \
        "different intent content must hash differently"


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
    print(f"\n{passed}/{passed+failed} idempotency tests passed.")
    sys.exit(1 if failed else 0)
