"""With auth on, the real server refuses a request without a valid token.

The suite runs with auth off (see conftest.py), so nothing else here would
notice if the server stopped enforcing it. require_auth reads the manager's
`enabled` flag on every request, so this switches it on for one test and checks
the three cases a caller can hit.
"""
from fastapi.testclient import TestClient


def test_the_server_enforces_its_token_when_auth_is_on(monkeypatch):
    import dosync.server as srv
    from dosync.auth import get_auth_manager

    manager = get_auth_manager()
    monkeypatch.setattr(manager, "enabled", True)
    client = TestClient(srv.app)

    assert client.get("/v1/devices").status_code == 401, "no token was accepted"
    assert client.get("/v1/devices",
                      headers={"Authorization": "Bearer not-a-real-key"}).status_code == 401, \
        "an invalid token was accepted"

    token = manager.generate_key(label="test-server-enforces-auth")
    assert client.get("/v1/devices",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 200, \
        "a valid token was refused"
