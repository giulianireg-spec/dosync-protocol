"""Less confirmation fatigue, same bound (panel 2026-10-17).

- A mark can hold only from N devices on (`min_devices`), counting what it let
  act within its window, so one large request split into small ones still meets
  the threshold.
- An operator can release some of an intent's held actions and deny the rest in
  one decision.
- A WoT consumer follows a held invocation at its status resource (queryaction):
  pending -> completed, or failed (denied / expired).
"""
import asyncio

import pytest

from dosync.executor import SimulatedExecutor
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)
from dosync.policies import RequireConfirmationPolicy

LIGHTS = [f"light-{i}" for i in range(5)]
OP = {"X-DoSync-Operator-Token": "the-operator-xyz"}


def _hub():
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = SimulatedExecutor()
    for d in LIGHTS:
        hub.register_device(CapabilityManifest(
            device_id=d, device_name=d, manufacturer="t", model="t", firmware="1",
            category=DeviceCategory.ACTUATOR, tags=["light"], sensors=[], events=[],
            actuators=[ActuatorSpec("turn_on", "turn_on", ""), ActuatorSpec("turn_off", "turn_off", "")]))
    return hub


def _off(hub, devices, iid):
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": d, "action": "turn_off"} for d in devices]})
    intent.intent_id = iid
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def _held(r):
    return sorted(h["device_id"] for h in r.held_for_confirmation)


def test_below_the_threshold_it_runs_at_or_above_it_waits():
    hub = _hub()
    hub.set_confirmation_marks([{"actions": ["turn_off"], "min_devices": 3}])
    r = _off(hub, LIGHTS[:2], "two")
    assert _held(r) == [] and len(r.results) == 2, "two lights: under the threshold"
    hub2 = _hub()
    hub2.set_confirmation_marks([{"actions": ["turn_off"], "min_devices": 3}])
    r = _off(hub2, LIGHTS[:3], "three")
    assert _held(r) == LIGHTS[:3] and not r.results, "three lights: the whole marked set waits"


def test_splitting_a_request_does_not_slip_under_the_threshold():
    hub = _hub()
    hub.set_confirmation_marks([{"actions": ["turn_off"], "min_devices": 3, "window_seconds": 300}])
    assert _held(_off(hub, [LIGHTS[0]], "a")) == []
    assert _held(_off(hub, [LIGHTS[1]], "b")) == []
    assert _held(_off(hub, [LIGHTS[2]], "c")) == [LIGHTS[2]], "the third within the window waits"


def test_the_window_forgets(monkeypatch):
    hub = _hub()
    hub.set_confirmation_marks([{"actions": ["turn_off"], "min_devices": 2, "window_seconds": 60}])
    assert _held(_off(hub, [LIGHTS[0]], "a")) == []
    hub._recent_runs[0] = (hub._recent_runs[0][0] - 120, *hub._recent_runs[0][1:])
    assert _held(_off(hub, [LIGHTS[1]], "b")) == [], "the earlier one is outside the window"


def test_a_threshold_in_the_policy_file_and_bad_values():
    p = RequireConfirmationPolicy(["turn_off"], min_devices=4, window_seconds=30)
    assert (p.min_devices, p.window_seconds) == (4, 30)
    for bad in ({"min_devices": 0}, {"min_devices": True}, {"window_seconds": 0}):
        with pytest.raises(ValueError):
            DoSyncHub.normalize_confirmation_mark(0, {"actions": ["turn_off"], **bad})


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "the-operator-xyz")
    hub = _hub()
    monkeypatch.setattr(srv, "hub", hub)
    monkeypatch.setattr(srv, "executor", SimulatedExecutor(failure_rate=0.0))
    with TestClient(srv.app) as c:
        srv.hub = hub
        yield c, hub


def test_release_some_deny_the_rest(client):
    c, hub = client
    hub.set_confirmation_marks([{"actions": ["turn_off"]}])
    _off(hub, LIGHTS[:3], "all-off")
    r = c.post("/v1/intents/all-off/confirm", headers=OP,
               json={"actions": [{"device_id": LIGHTS[0], "action": "turn_off"},
                                 {"device_id": LIGHTS[2], "action": "turn_off"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(x["device_id"] for x in body["results"]) == [LIGHTS[0], LIGHTS[2]]
    assert body["denied"] == [{"device_id": LIGHTS[1], "action": "turn_off"}]
    types = [(e["type"], e.get("partial")) for e in hub.audit_log.entries()
             if e.get("intent_id") == "all-off" and e["type"].startswith("confirmation_")]
    assert ("confirmation_denied", True) in types and ("confirmation_confirmed", True) in types
    assert hub.pending_confirmation("all-off") is None


def test_releasing_something_not_held_changes_nothing(client):
    c, hub = client
    hub.set_confirmation_marks([{"actions": ["turn_off"]}])
    _off(hub, LIGHTS[:2], "x")
    r = c.post("/v1/intents/x/confirm", headers=OP,
               json={"actions": [{"device_id": LIGHTS[4], "action": "turn_off"}]})
    assert r.status_code == 422
    assert hub.pending_confirmation("x") is not None, "the hold is intact"


def _invoke_and_status(c, device):
    r = c.post(f"/v1/things/{device}/actions/turn_off")
    assert r.status_code == 202, r.text
    href = r.json()["href"]
    assert r.headers["Location"] == href
    return r.json()["intent_id"], href


def test_queryaction_follows_a_hold_to_completed(client):
    c, hub = client
    hub.set_confirmation_marks([{"actions": ["turn_off"]}])
    iid, href = _invoke_and_status(c, LIGHTS[0])
    assert c.get(href).json()["status"] == "pending"
    assert c.post(f"/v1/intents/{iid}/confirm", headers=OP).status_code == 200
    s = c.get(href).json()
    assert s["status"] == "completed" and s["output"]["status"] == "done"


def test_queryaction_reports_a_denial(client):
    c, hub = client
    hub.set_confirmation_marks([{"actions": ["turn_off"]}])
    iid, href = _invoke_and_status(c, LIGHTS[1])
    assert c.post(f"/v1/intents/{iid}/deny", headers=OP).status_code == 200
    assert c.get(href).json() == {"status": "failed", "intent_id": iid, "error": "denied"}


def test_the_td_declares_queryaction(client):
    c, hub = client
    td = c.get(f"/v1/things/{LIGHTS[0]}").json()
    ops = {f["op"] for f in td["actions"]["turn_off"]["forms"]}
    assert ops == {"invokeaction", "queryaction"}
    assert "actionID" in td["actions"]["turn_off"]["uriVariables"]
