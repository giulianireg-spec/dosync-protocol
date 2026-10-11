"""Who may place devices, set emergency actions and define intent classes on /v1.

SAMUEL reserves them to the operator (§3): a place decides which intents reach a
device, emergency actions decide what it does in a crisis, and a class is a grant
of authority -- an agent able to register one could give itself what a policy on
operate_device withheld. On /v1 they took the hub credential, which the agent's
tools also hold. Decided 2026-10-11: that stays the default -- the deployment
chooses -- with a warning at start and the setting reported; with
DOSYNC_CONFIGURATION_CREDENTIAL=operator they need the operator credential.
"""
import pytest

from dosync.models import ActuatorSpec, CapabilityManifest, DeviceCategory

CELL = "conf-cred-cell"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    from dosync.hub import DoSyncHub
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "the-operator")
    hub = DoSyncHub(db_path=":memory:")
    hub.register_device(CapabilityManifest(
        device_id=CELL, device_name=CELL, manufacturer="t", model="t", firmware="",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec("lock", "lock", ""), ActuatorSpec("unlock", "unlock", "")],
        location="plant-1/line-3/cell-2"))
    monkeypatch.setattr(srv, "hub", hub)
    with TestClient(srv.app) as c:
        srv.hub = hub
        yield c


CLASS = {"name": "night_access", "urgency": "alert", "resolution_tags": ["access"],
         "resolution_actuators": ["unlock"], "description": "t", "domain": "plant"}


def test_by_default_the_hub_credential_still_configures(client, monkeypatch):
    monkeypatch.delenv("DOSYNC_CONFIGURATION_CREDENTIAL", raising=False)
    assert client.get("/v1/status").json()["configuration_credential"] == "hub"
    assert client.patch(f"/v1/devices/{CELL}", json={"location": "plant-1/line-30"}).status_code == 200
    assert client.post("/v1/intent-classes", json=CLASS).status_code in (200, 201)
    assert client.get("/samuel/v0").json()["configuration"] == "hub"


def test_when_the_deployment_reserves_it_only_the_operator_configures(client, monkeypatch):
    monkeypatch.setenv("DOSYNC_CONFIGURATION_CREDENTIAL", "operator")
    assert client.get("/v1/status").json()["configuration_credential"] == "operator"
    # place, emergency actions, classes: refused without the operator credential
    r = client.patch(f"/v1/devices/{CELL}", json={"location": "plant-1/line-30"})
    assert r.status_code == 403 and "operator_credential_required" in r.text
    assert client.patch(f"/v1/devices/{CELL}",
                        json={"emergency_actions": [{"action": "unlock"}]}).status_code == 403
    assert client.post("/v1/intent-classes", json=CLASS).status_code == 403
    assert client.delete("/v1/intent-classes/anything").status_code == 403
    # a rename is presentation, not configuration
    assert client.patch(f"/v1/devices/{CELL}", json={"device_name": "Cell 2"}).status_code == 200
    # with the operator credential, it goes through
    op = {"X-Operator-Token": "the-operator"}
    assert client.patch(f"/v1/devices/{CELL}", json={"location": "plant-1/line-30"},
                        headers=op).status_code == 200
    assert client.post("/v1/intent-classes", json=CLASS, headers=op).status_code in (200, 201)
    assert client.get("/samuel/v0").json()["configuration"] == "operator"
