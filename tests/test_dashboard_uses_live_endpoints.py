"""The dashboard only calls endpoints the hub still serves, and asks for what it shows.

- Its intent buttons posted to POST /v1/intent, which answers 410 Gone on purpose
  (see execute_intent_legacy in server.py). Every button failed with a "410 Gone"
  toast and fired nothing. The same stale path had already cost 70 dropped
  intents once, from gpio_adapter.py; the dashboard was the caller left behind.
- Its audit panel fetched the whole live chain -- 10,000 entries on a busy hub --
  to show 30, and it does so on every device event over the WebSocket.
"""
import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "dosync" / "dashboard.html"


def _page():
    return DASHBOARD.read_text(encoding="utf-8")


def test_no_call_to_the_removed_synchronous_intent_endpoint():
    assert not re.search(r"api\('POST',\s*'/v1/intent'", _page()), \
        "the dashboard posts to /v1/intent, which answers 410 Gone"


def test_intents_are_fired_async_and_their_outcome_is_polled():
    page = _page()
    assert "api('POST', '/v1/intent/async'" in page
    assert "api('GET', `/v1/intent/${encodeURIComponent(intentId)}`)" in page, \
        "the dashboard fires the intent but never asks how it ended"


def test_the_audit_panel_asks_only_for_what_it_shows():
    assert "api('GET', '/v1/audit?limit=30')" in _page(), \
        "the audit panel fetches the whole chain to show 30 entries"
