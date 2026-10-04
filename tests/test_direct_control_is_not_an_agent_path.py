"""Rule 8: direct control is not an agent's path.

The intent guarantees bind intents. POST /v1/device/action names one device and
one action, with no class to bound its authority and no place to confine it; the
reference MCP server exposed it as a second tool, and exported Thing Descriptions
pointed their actions at it. Agents and operators held the same credential, so
the bound the governed direct mode gives had a side door open by default --
found by the third review of the IIWOT paper. Now the path is closed by default,
opened for an operator credential an agent never holds, or opened outright for
development; and when open, a device still performs only what it declares, and
in an emergency only its declared emergency actions.
"""
import pytest
from fastapi.testclient import TestClient

from dosync.models import ActuatorSpec, CapabilityManifest, DeviceCategory


def _door(device_id, emergency_actions=None):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec("lock", "lock", "lock"), ActuatorSpec("unlock", "unlock", "unlock")],
        emergency_capable=bool(emergency_actions), emergency_actions=emergency_actions or [])


@pytest.fixture
def client():
    import dosync.server as srv
    srv.hub.register_device(_door("r8-door"))
    srv.hub.register_device(_door("r8-exit", emergency_actions=[{"action": "unlock", "params": {}}]))
    return TestClient(srv.app), srv


def _act(c, action="unlock", device="r8-door", urgency="info", token=None):
    headers = {"X-DoSync-Operator-Token": token} if token is not None else {}
    return c.post("/v1/device/action", json={"device_id": device, "action": action, "urgency": urgency},
                  headers=headers)


def test_closed_by_default_and_the_refusal_is_audited(client, monkeypatch):
    monkeypatch.delenv("DOSYNC_DIRECT_CONTROL", raising=False)
    c, srv = client
    r = _act(c)
    assert r.status_code == 403 and r.json()["detail"].startswith("direct_control_disabled")
    assert "proposed_actions" in r.json()["detail"], "the refusal tells the agent what to do instead"
    entry = [e for e in srv.hub.audit_log.entries() if e.get("type") == "direct_action_refused"][-1]
    assert entry["reason"] == "direct_control_disabled" and entry["device_id"] == "r8-door"
    assert c.get("/v1/status").json()["direct_control"] == "off"


def test_operator_mode_needs_the_operator_credential(client, monkeypatch):
    monkeypatch.setenv("DOSYNC_DIRECT_CONTROL", "operator")
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "operator-secret")
    c, _ = client
    for token in (None, "", "wrong"):
        r = _act(c, token=token)
        assert r.status_code == 403 and r.json()["detail"].startswith("operator_credential_required"), token
    assert _act(c, token="operator-secret").status_code == 200
    monkeypatch.delenv("DOSYNC_OPERATOR_TOKEN")
    assert _act(c, token="").status_code == 403, "no operator credential configured: nobody gets in"


def test_when_open_a_device_does_only_what_it_declares(client, monkeypatch):
    monkeypatch.setenv("DOSYNC_DIRECT_CONTROL", "on")
    c, _ = client
    r = _act(c, action="fly")
    assert r.status_code == 422 and r.json()["detail"].startswith("not_declared")


def test_when_open_an_emergency_runs_only_declared_emergency_actions(client, monkeypatch):
    monkeypatch.setenv("DOSYNC_DIRECT_CONTROL", "on")
    c, _ = client
    assert _act(c, device="r8-exit", action="lock", urgency="emergency").status_code == 403
    assert _act(c, device="r8-exit", action="unlock", urgency="emergency").status_code == 200


def test_an_unknown_mode_is_closed(client, monkeypatch):
    monkeypatch.setenv("DOSYNC_DIRECT_CONTROL", "sometimes")
    c, _ = client
    assert _act(c).status_code == 403
