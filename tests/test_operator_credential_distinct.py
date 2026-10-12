"""The operator credential is not an agent credential (H5, SAMUEL 0.2 §11).

An operator token equal to the hub token, the demo token or a stored API key
separates nothing: the agent could release the actions it got held and, where
configuration is reserved, place devices and grant itself classes. The hub keeps
running (a hub switched off governs nothing) but does not honour that value as
the operator's, and says so. Creating an agent key equal to the operator token
is refused.
"""
import asyncio

import pytest

from dosync.executor import SimulatedExecutor
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)

SECRET = "operator-and-agent-the-same"


def _dev(device_id, acts):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts])


@pytest.fixture
def make_client(monkeypatch):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    from dosync.hub import DoSyncHub

    def _make(**env):
        for k in ("DOSYNC_TOKEN", "DOSYNC_DEMO_TOKEN", "DOSYNC_OPERATOR_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        hub = DoSyncHub(db_path=":memory:")
        hub.default_executor = SimulatedExecutor()
        hub.register_device(_dev("valve-7", ["open", "close"]))
        monkeypatch.setattr(srv, "hub", hub)
        client = TestClient(srv.app)
        client.__enter__()
        srv.hub = hub
        return client, hub, srv

    yield _make


def _hold(hub, iid):
    hub.set_confirmation_marks([{"actions": ["open"]}])
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": "valve-7", "action": "open"}]})
    intent.intent_id = iid
    asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


@pytest.mark.parametrize("agent_env", ["DOSYNC_TOKEN", "DOSYNC_DEMO_TOKEN"])
def test_an_operator_token_equal_to_the_agents_is_not_honoured(make_client, agent_env):
    client, hub, srv = make_client(**{agent_env: SECRET, "DOSYNC_OPERATOR_TOKEN": SECRET})
    assert srv.operator_credential_state() == "same_as_agent"
    assert client.get("/v1/status").json()["operator_credential"] == "same_as_agent"
    assert client.get("/samuel/v0").json()["operator_credential"] == "same_as_agent"
    g = client.get("/v1/governance").json()
    assert g["operator_configured"] is False and g["operator_credential"] == "same_as_agent"
    _hold(hub, "held-1")
    r = client.post("/v1/intents/held-1/confirm", headers={"X-DoSync-Operator-Token": SECRET})
    assert r.status_code == 403, "the agent's own token must not release what it asked for"
    assert client.put("/v1/governance/confirmation-marks", json={"marks": []},
                      headers={"X-DoSync-Operator-Token": SECRET}).status_code == 403


def test_a_stored_api_key_equal_to_the_operator_token_also_collides(make_client):
    client, hub, srv = make_client(DOSYNC_OPERATOR_TOKEN=SECRET)
    assert srv.operator_credential_state() == "ok"
    from dosync.auth import hash_token
    hub.db.save_api_key(hash_token(SECRET), "made-before-the-operator-token")
    assert srv.operator_credential_state() == "same_as_agent"


def test_distinct_credentials_work(make_client):
    client, hub, srv = make_client(DOSYNC_TOKEN="the-agent-token-xyz", DOSYNC_OPERATOR_TOKEN=SECRET)
    assert srv.operator_credential_state() == "ok"
    _hold(hub, "held-2")
    r = client.post("/v1/intents/held-2/confirm", headers={"X-DoSync-Operator-Token": SECRET})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


def test_an_agent_key_cannot_be_created_equal_to_the_operator_token(make_client):
    client, hub, srv = make_client(DOSYNC_OPERATOR_TOKEN=SECRET)
    from dosync.auth import AuthManager
    with pytest.raises(ValueError, match="operator credential"):
        AuthManager(hub.db).generate_key("agent", token=SECRET)
    assert srv.operator_credential_state() == "ok"


def test_unset_is_reported_as_unset(make_client):
    client, hub, srv = make_client()
    assert srv.operator_credential_state() == "unset"
    assert client.get("/v1/confirmations").json()["operator_credential"] == "unset"
