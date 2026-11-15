"""An emergency is corroborated, not claimed (spec §6.8 rule 5a, G5).

EMERGENCY urgency relaxes rules, so the caller's word is not enough. The second
adversarial round showed why: an agent misled into a false emergency obtained a
cell's declared emergency action, unlock, which no hold stopped because declared
emergency actions flow without confirmation. Now an EMERGENCY nothing
corroborates runs as an ALERT, and what only the emergency would add is held for
a person; the operator credential, an emergency event authenticated with the
device's own token, or a person's release confirm it.
"""
import asyncio
import time

import pytest

from dosync.executor import SimulatedExecutor
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, DeviceEvent,
                           Intent, IntentClass, Severity, Urgency)
from dosync.policies import PolicyEngine


def _dev(device_id, acts, location, emergency=False, emergency_actions=None):
    d = CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts], emergency_capable=emergency,
        emergency_actions=emergency_actions or [])
    d.location = location
    return d


def _plant():
    hub = DoSyncHub(db_path=":memory:")
    hub.policy_engine = PolicyEngine()
    hub.default_executor = SimulatedExecutor()
    # The cell of the adversarial round: emergency-capable, its declared emergency
    # action is unlock (open for evacuation).
    hub.register_device(_dev("cell", ["lock", "unlock"], "plant-1/line-3", emergency=True,
                             emergency_actions=[{"action": "unlock", "params": {}}]))
    hub.register_device(_dev("detector", ["self_test"], "plant-1/line-3"))
    hub.register_device(_dev("detector-2", ["self_test"], "plant-2"))
    for d in ("cell", "detector", "detector-2"):
        hub.registry.get(d).location = hub.registry.get(d).location  # placed by the operator
    return hub


def _fire(hub, basis=None, location="plant-1/line-3", iid="e1"):
    intent = Intent(intent=IntentClass("ensure_safety"), urgency=Urgency.EMERGENCY,
                    context={"location": location}, emergency_basis=basis)
    intent.intent_id = iid
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def _ran(result):
    return {(r.device_id, r.action) for r in result.results}


def _entries(hub, kind):
    return [e for e in hub.audit_log.entries() if e.get("type") == kind]


def _event(hub, device_id, authenticated=True, age=0.0, severity=Severity.EMERGENCY):
    ev = DeviceEvent(device_id=device_id, event_id="smoke", severity=severity, data={"smoke": True},
                     authenticated=authenticated)
    ev.timestamp = time.time() - age
    asyncio.run(hub.receive_event(ev))


def test_a_false_emergency_no_longer_opens_the_cell():
    hub = _plant()
    r = _fire(hub)
    assert ("cell", "unlock") not in _ran(r), "an agent's word alone opened a lock"
    pend = hub.pending_confirmation("e1")
    assert pend and {"device_id": "cell", "action": "unlock", "reason": "unconfirmed_emergency"} in pend["actions"]
    unc = _entries(hub, "emergency_unconfirmed")
    assert unc and unc[0]["held"] == [{"device_id": "cell", "action": "unlock"}]


def test_a_person_releasing_it_confirms_the_emergency():
    hub = _plant()
    _fire(hub, iid="e2")
    out = asyncio.run(hub.confirm_intent("e2", SimulatedExecutor()))
    assert out["status"] == "confirmed"
    assert {"device_id": "cell", "action": "unlock"} in [
        {"device_id": x["device_id"], "action": x["action"]} for x in out["results"]]


def test_the_operator_credential_confirms_it():
    hub = _plant()
    r = _fire(hub, basis="operator", iid="e3")
    assert ("cell", "unlock") in _ran(r)
    assert not _entries(hub, "emergency_unconfirmed")


def test_an_authenticated_emergency_event_in_the_place_corroborates_it():
    hub = _plant()
    _event(hub, "detector")
    r = _fire(hub, iid="e4")
    assert ("cell", "unlock") in _ran(r)
    executed = [e for e in hub.audit_log.entries() if e.get("intent_id") == "e4"]
    assert not _entries(hub, "emergency_unconfirmed"), executed


@pytest.mark.parametrize("device_id, authenticated, age, why", [
    ("detector", False, 0.0, "an event sent with an agent key"),
    ("detector-2", True, 0.0, "an event from another place"),
    ("detector", True, 3600.0, "an event older than the window"),
])
def test_what_does_not_corroborate(device_id, authenticated, age, why):
    hub = _plant()
    _event(hub, device_id, authenticated=authenticated, age=age)
    r = _fire(hub, iid="e5")
    assert ("cell", "unlock") not in _ran(r), f"{why} corroborated an emergency"


def test_a_warning_event_does_not_corroborate():
    hub = _plant()
    _event(hub, "detector", severity=Severity.WARNING)
    assert ("cell", "unlock") not in _ran(_fire(hub, iid="e6"))


def test_switching_corroboration_off_restores_the_old_trust(monkeypatch):
    monkeypatch.setenv("DOSYNC_EMERGENCY_CORROBORATION", "off")
    hub = _plant()
    assert ("cell", "unlock") in _ran(_fire(hub, iid="e7"))


# ── Over HTTP: the device token and the operator credential ──────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "the-operator")
    from fastapi.testclient import TestClient
    import dosync.server as srv
    return TestClient(srv.app), srv


def test_only_the_device_token_authenticates_an_event(client):
    c, srv = client
    from dosync.auth import DeviceAuthManager, get_device_auth_manager, set_device_auth_manager
    if get_device_auth_manager() is None:
        set_device_auth_manager(DeviceAuthManager(srv.hub.db))
    r = c.post("/v1/devices/register", json={"device_id": "smoke-x", "device_name": "smoke", "manufacturer": "t",
                                         "model": "t", "firmware": "1", "category": "sensor", "tags": ["smoke"]})
    assert r.status_code == 200, r.text
    token = get_device_auth_manager().provision("smoke-x")
    body = {"device_id": "smoke-x", "event_id": "smoke", "severity": "emergency", "data": {}}
    assert c.post("/v1/event", json=body).json()["authenticated"] is False
    assert c.post("/v1/event", json=body, headers={"X-Device-Token": "wrong"}).status_code == 401
    assert c.post("/v1/event", json=body, headers={"X-Device-Token": token}).json()["authenticated"] is True
    srv.hub._trusted_emergencies.clear()   # the shared hub: do not corroborate other tests' emergencies


def test_an_unconfirmed_emergency_still_sounds_the_alarm():
    hub = _plant()
    hub.register_device(_dev("siren", ["alarm"], "plant-1/line-3", emergency=True,
                             emergency_actions=[{"action": "alarm", "params": {}}]))
    r = _fire(hub, iid="e8")
    assert ("siren", "alarm") in _ran(r), "a warning waited for a confirmation"
    assert ("cell", "unlock") not in _ran(r)


def test_an_unconfirmed_emergency_keeps_the_emergency_restrictions():
    # The second round's V2: a false emergency asking an emergency-capable light for
    # something other than its declared emergency action. A real emergency refuses
    # it (rule 5); an unconfirmed one must not grant it by turning into an alert.
    hub = _plant()
    hub.register_device(_dev("lamp", ["turn_on", "set_brightness"], "plant-1/line-3", emergency=True,
                             emergency_actions=[{"action": "turn_on", "params": {}}]))
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.EMERGENCY,
                    context={"proposed_actions": [{"device_id": "lamp", "action": "set_brightness"}]})
    intent.intent_id = "e9"
    r = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert ("lamp", "set_brightness") not in _ran(r)
    assert {"device_id": "lamp", "action": "set_brightness", "reason": "not_its_emergency_action"} in [
        {k: x.get(k) for k in ("device_id", "action", "reason")} for x in r.refused_proposals]
