"""/v1/audit can return just the tail of the chain.

The endpoint returned every live entry -- 10,000 by default on a busy hub -- so a
caller wanting the last ten (a dashboard tail, the MCP audit tool showing recent
activity) received the whole chain and threw almost all of it away. `limit`
returns only the most recent N; `count` still reports the full total, so a caller
can tell how much history exists behind what it asked for.
"""
from fastapi.testclient import TestClient


def _client_with(n_entries):
    import dosync.server as srv
    for i in range(n_entries):
        srv.hub.audit_log.append({"type": "probe", "i": i})
    return TestClient(srv.app), srv


def test_the_tail_is_the_most_recent_entries():
    client, srv = _client_with(12)
    body = client.get("/v1/audit?limit=3").json()

    assert len(body["entries"]) == 3
    assert body["returned"] == 3
    assert body["count"] == len(srv.hub.audit_log.entries()), \
        "count must report the whole chain, not the slice"
    # newest last, and they are the newest
    assert body["entries"] == srv.hub.audit_log.entries()[-3:]


def test_without_a_limit_the_whole_chain_is_returned():
    client, srv = _client_with(5)
    body = client.get("/v1/audit").json()
    assert len(body["entries"]) == body["count"] == len(srv.hub.audit_log.entries())


def test_an_out_of_range_limit_is_refused():
    client, _ = _client_with(1)
    assert client.get("/v1/audit?limit=0").status_code == 422
    assert client.get("/v1/audit?limit=5000").status_code == 422
