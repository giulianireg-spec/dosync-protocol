"""dosync_get_scenarios lists the intents the hub has, not a fixed list.

The tool returned a hardcoded text that had drifted from the hub. On the
reference hub it offered six intents that were not registered -- bedtime_routine,
morning_routine, away_mode and others, each of which the hub would refuse as
not_registered -- and left out some that were. The same file already built
dosync_fire_intent's enum from GET /v1/intent-classes precisely so the MCP would
not carry a copy that diverges; this tool had been left out of that change.
"""
import asyncio

from dosync import mcp_server


def _scenarios(monkeypatch, hub):
    monkeypatch.setattr(mcp_server, "hub_request", hub)
    return asyncio.run(mcp_server.call_tool("dosync_get_scenarios", {}))[0].text


def _hub_with(classes):
    async def _h(method, path, body=None):
        assert path == "/v1/intent-classes"
        return {"intent_classes": classes}
    return _h


def test_it_lists_exactly_what_the_hub_has(monkeypatch):
    text = _scenarios(monkeypatch, _hub_with([
        {"name": "ensure_safety", "urgency": "emergency", "description": "Protect people"},
        {"name": "report_status", "urgency": "info", "description": "Read state"},
    ]))
    assert "ensure_safety" in text and "report_status" in text
    for stale in ("bedtime_routine", "morning_routine", "away_mode", "remind_chore"):
        assert stale not in text, f"offered {stale}, which this hub never declared"


def test_the_most_urgent_intents_come_first(monkeypatch):
    # Names chosen so alphabetical order contradicts urgency order: a sort by
    # name alone would put audit_ping first.
    text = _scenarios(monkeypatch, _hub_with([
        {"name": "audit_ping", "urgency": "info"},
        {"name": "zone_evacuate", "urgency": "emergency"},
    ]))
    assert text.index("zone_evacuate") < text.index("audit_ping")


def test_a_composition_intent_says_it_needs_geographic_context(monkeypatch):
    text = _scenarios(monkeypatch, _hub_with([
        {"name": "inspect_area", "urgency": "info", "composition_kind": "perimeter"},
        {"name": "notify", "urgency": "info"},
    ]))
    geo = [l for l in text.splitlines() if "geographic context" in l]
    assert len(geo) == 1 and "inspect_area" in geo[0]


def test_an_unreachable_hub_lists_nothing_rather_than_a_guess(monkeypatch):
    async def _down(method, path, body=None):
        raise ConnectionError("connection refused")
    text = _scenarios(monkeypatch, _down)
    assert "Could not reach the hub" in text
    assert "ensure_safety" not in text


def test_a_hub_with_no_intents_says_so(monkeypatch):
    text = _scenarios(monkeypatch, _hub_with([]))
    assert "no intents registered" in text


def test_each_intent_says_what_a_location_does(monkeypatch):
    # The agent needs to know whether sending a location narrows where the
    # intent acts or only says where the situation is.
    text = _scenarios(monkeypatch, _hub_with([
        {"name": "light_room", "urgency": "info", "location_role": "restricts"},
        {"name": "alert_anomaly", "urgency": "alert", "location_role": "informs"},
    ]))
    lines = {l.split()[0]: l for l in text.splitlines() if l.startswith("  ")}
    assert "(location: restricts)" in lines["light_room"]
    assert "(location: informs)" in lines["alert_anomaly"]
