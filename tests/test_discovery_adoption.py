"""Scan, then adopt — approval and naming as one step (2026-07-26).

Panel finding on H6: the discovery machinery already existed (207 lines, two
endpoints, `run_periodic`) and the dashboard called none of it, so a hub with no
devices was a dead end whose only exit was a hand-written JSON manifest.

Two design decisions came out of that session and are pinned here:

  * **Scanning must not register.** `POST /v1/discovery/run` finds and registers
    in one step, which is fine for a scripted setup but wrong as the only path:
    in a protocol whose argument is accountability, devices appearing because
    they answered a broadcast — approved by nobody — contradicts the premise.
  * **The operator names them.** `wiz-a4c138` is what the bulb calls itself;
    "Kitchen light" is what makes every later screen readable.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import dosync.server as srv
    return srv, TestClient(srv.app)


def test_scanning_registers_nothing(client):
    """The endpoint reports candidates and their registration state; it must not
    change that state."""
    srv, c = client
    before = len(srv.hub.registry.all())
    r = c.get("/v1/discovery/scan")
    assert r.status_code == 200
    assert "found" in r.json()
    assert len(srv.hub.registry.all()) == before, "a scan must have no side effects"


def test_adopting_uses_the_name_the_operator_chose(client):
    srv, c = client
    r = c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-test-adopt", "ip": "192.168.1.50",
        "device_name": "Kitchen light", "room": "kitchen"})
    assert r.status_code == 200 and r.json()["adopted"] is True

    dev = srv.hub.registry.get("wiz-test-adopt")
    assert dev is not None
    assert dev.device_name == "Kitchen light", \
        "the chosen name is the one that makes later screens readable"


def test_adoption_is_recorded_as_operator_approved(client):
    """'How did this device get here' is the same class of question as 'who
    turned authentication off'."""
    srv, c = client
    c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-test-audit", "ip": "1.2.3.4",
        "device_name": "Audited lamp"})

    entries = [e for e in srv.hub.audit_log.entries()
               if e.get("type") == "device_adopted"
               and e.get("device_id") == "wiz-test-audit"]
    assert entries, "adoption must appear in the chain"
    assert entries[-1]["approved_by_operator"] is True
    assert entries[-1]["device_name"] == "Audited lamp"


def test_adopting_twice_is_not_an_error_and_not_a_duplicate(client):
    srv, c = client
    body = {"adapter": "wiz", "device_id": "wiz-test-twice", "ip": "1.2.3.4",
            "device_name": "Once"}
    assert c.post("/v1/discovery/adopt", json=body).json()["adopted"] is True
    second = c.post("/v1/discovery/adopt", json=body).json()
    assert second["adopted"] is False and "already" in second["reason"]


def test_an_adapter_that_cannot_build_a_manifest_says_so(client):
    """Discovery is an adapter capability, not a protocol promise — a drone does
    not answer a UDP broadcast and a clinical device sits on a proprietary bus.
    The honest response is to name manual registration as the normal path, not
    to fail obscurely."""
    srv, c = client
    r = c.post("/v1/discovery/adopt", json={"adapter": "mqtt", "device_id": "x"})
    assert r.status_code == 422
    assert "/v1/devices/register" in r.json()["detail"], \
        "and must point at the path that does work"


def test_automatic_adoption_is_recorded_too(client):
    """`run` registering without a human choosing is defensible — invoking it IS
    the approval — but it must not be invisible."""
    import inspect

    import dosync.server as srv
    src = inspect.getsource(srv.run_discovery)
    assert "devices_auto_adopted" in src, \
        "auto-registration must append to the chain"
    assert "approved_by_operator" in src, \
        "and distinguish itself from an operator's deliberate choice"


def test_dashboard_offers_the_scan(client):
    """The machinery existed and nothing in the UI called it — the same defect
    as the dashboard itself sitting outside the package."""
    srv, c = client
    page = c.get("/").text
    assert "scanDevices()" in page
    assert "/v1/discovery/scan" in page and "/v1/discovery/adopt" in page
