"""Refused intents are counted by reason and shown where an operator looks.

On the reference hub a sensor script fired an intent that was no longer
registered -- 1,837 times. The hub refused and counted every one, but only in
/metrics as intent_class="_invalid", which does not say *why* and which nobody
was reading. Refusals are now counted by reason and reported in /v1/status and
by the MCP status tool. Also fixed: the urgency label list was a copy that
omitted "warning", and a reused idempotency key (409) was not counted at all.
"""
import asyncio

from fastapi.testclient import TestClient


def _post(client, body):
    return client.post("/v1/intent/async", json=body)


def _rejected(client):
    return client.get("/v1/status").json()["intents_rejected"]


def _client():
    import dosync.server as srv
    return TestClient(srv.app), srv


def test_each_refusal_is_counted_under_its_reason():
    client, srv = _client()
    known = client.get("/v1/intent-classes").json()["intent_classes"][0]["name"]
    before = _rejected(client)

    assert _post(client, {"intent": "Not-A-Name", "urgency": "info"}).status_code == 422
    assert _post(client, {"intent": "never_registered", "urgency": "info"}).status_code == 422
    assert _post(client, {"intent": known, "urgency": "whenever"}).status_code == 422
    body = {"intent": known, "urgency": "info", "idempotency_key": "k-rejections-1"}
    assert _post(client, body).status_code == 200
    assert _post(client, {**body, "context": {"different": True}}).status_code == 409

    after = _rejected(client)
    for reason in ("invalid_name", "not_registered", "invalid_urgency", "idempotency_conflict"):
        assert after[reason] == before[reason] + 1, f"{reason} was not counted"


def test_a_warning_urgency_is_labelled_warning_not_invalid():
    client, srv = _client()
    from dosync import metrics as M

    def warning_rejections():
        return sum(n for (cls, urg, out), n in M.intents_total.samples().items()
                   if out == "rejected" and urg == "warning")

    before = warning_rejections()
    _post(client, {"intent": "never_registered_2", "urgency": "warning"})
    assert warning_rejections() == before + 1, "a warning-urgency refusal was labelled _invalid"


def test_the_mcp_status_tool_reports_refusals(monkeypatch):
    from dosync import mcp_server

    async def _hub(method, path, body=None):
        return {"version": "x", "devices": 1, "audit_integrity": True,
                "intents_rejected": {"invalid_name": 0, "not_registered": 1837,
                                     "invalid_urgency": 0, "idempotency_conflict": 0}}

    monkeypatch.setattr(mcp_server, "hub_request", _hub)
    text = asyncio.run(mcp_server.call_tool("dosync_get_status", {}))[0].text
    assert "1837" in text and "not_registered" in text


def test_the_mcp_status_tool_is_quiet_when_nothing_was_refused(monkeypatch):
    from dosync import mcp_server

    async def _hub(method, path, body=None):
        return {"version": "x", "devices": 1, "audit_integrity": True,
                "intents_rejected": {"invalid_name": 0, "not_registered": 0,
                                     "invalid_urgency": 0, "idempotency_conflict": 0}}

    monkeypatch.setattr(mcp_server, "hub_request", _hub)
    text = asyncio.run(mcp_server.call_tool("dosync_get_status", {}))[0].text
    assert "refused" not in text
