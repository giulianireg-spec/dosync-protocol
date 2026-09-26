"""The MCP status tool reports emergencies that acted everywhere.

An emergency naming a place no device is at is never refused: it acts on every
capable device and the hub counts it (emergency_location_fallbacks). Counted but
not shown is how 1,837 refused intents went unnoticed, so the agent's status
tool shows it whenever it has happened, and stays quiet when it has not.
"""
import asyncio

from dosync import mcp_server


def _status(monkeypatch, fallbacks):
    async def _hub(method, path, body=None):
        return {"version": "x", "devices": 1, "audit_integrity": True,
                "emergency_location_fallbacks": fallbacks}
    monkeypatch.setattr(mcp_server, "hub_request", _hub)
    return asyncio.run(mcp_server.call_tool("dosync_get_status", {}))[0].text


def test_an_emergency_that_acted_everywhere_is_shown(monkeypatch):
    text = _status(monkeypatch, 2)
    assert "Emergencies at an unknown location since start: 2" in text


def test_nothing_is_shown_when_it_never_happened(monkeypatch):
    assert "unknown location" not in _status(monkeypatch, 0)
