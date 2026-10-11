"""SAMUEL's intent refusals hold on every door, not only on the REST surface.

Until 2026-10-11 the refusals of an intent as a whole (unknown place, actions
outside the class, ambiguous opposites, malformed or missing proposals) lived
only in the handler of POST /v1/intent/async. An intent reaching
DoSyncHub.execute_intent any other way -- the hub's own capability-anomaly
alert, a library caller, a bridge -- skipped them. Both doors now call the same
dosync.samuel.admit(); this checks each refusal through both, and that they
agree on the reason.
"""
import asyncio

import pytest

from dosync.executor import SimulatedExecutor
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)
from dosync.samuel import INTENT_REFUSALS

# A plant cell, so nothing here reads as a home: a cell lock and a conveyor on a line.
DEVICES = [
    ("cell-2-lock", ["lock", "unlock"], "plant-1/line-3/cell-2"),
    ("line-3-conveyor", ["start", "stop"], "plant-1/line-3"),
]

CASES = [
    # (reason, intent, urgency, context)
    ("unknown_location", "control_access", "alert",
     {"location": "plant-1/line-30", "action_types": ["lock"]}),
    ("invalid_actions", "control_access", "alert", {"action_types": ["start"]}),
    ("ambiguous_actions", "control_access", "alert", {}),
    ("proposals_required", "operate_device", "info", {}),
    ("invalid_proposals", "operate_device", "info",
     {"proposed_actions": [{"device_id": 7, "action": "stop"}]}),
]


def _hub():
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = SimulatedExecutor()
    for did, acts, place in DEVICES:
        hub.register_device(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t", firmware="1",
            category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
            actuators=[ActuatorSpec(a, a, a) for a in acts], location=place))
    return hub


def test_every_intent_refusal_is_covered():
    assert {c[0] for c in CASES} == set(INTENT_REFUSALS) - {"not_registered"}


@pytest.mark.parametrize("reason,cls,urgency,ctx", CASES, ids=[c[0] for c in CASES])
def test_the_object_door_refuses(reason, cls, urgency, ctx):
    hub = _hub()
    intent = Intent(intent=IntentClass(cls), urgency=Urgency(urgency), context=dict(ctx))
    result = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert result.status == "refused" and result.refused_reason == reason
    assert not result.results, "a refused intent runs nothing"
    entries = [e for e in hub.audit_log.entries() if e.get("type") == "intent_refused"]
    assert entries and entries[-1]["reason"] == reason, "and the refusal is recorded (G6)"


@pytest.mark.parametrize("reason,cls,urgency,ctx", CASES, ids=[c[0] for c in CASES])
def test_the_rest_door_refuses_with_the_same_reason(reason, cls, urgency, ctx):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    original = srv.hub
    try:
        with TestClient(srv.app) as client:
            srv.hub = _hub()
            before = client.get("/v1/status").json()["intents_rejected"].get(reason, 0)
            r = client.post("/v1/intent/async", json={"intent": cls, "urgency": urgency,
                                                      "context": dict(ctx)})
            after = client.get("/v1/status").json()["intents_rejected"].get(reason, 0)
    finally:
        srv.hub = original
    assert r.status_code == 422
    assert after == before + 1, f"counted under {reason}"


def test_an_unregistered_class_is_refused_by_the_object_door():
    hub = _hub()
    intent = Intent(intent=IntentClass("line_shutdown"), urgency=Urgency.ALERT, context={})
    result = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert result.status == "refused" and result.refused_reason == "not_registered"


def test_an_emergency_at_an_unknown_place_is_never_refused():
    """Rule 3: an emergency is never narrowed to nothing -- on the object door too."""
    hub = _hub()
    intent = Intent(intent=IntentClass("ensure_safety"), urgency=Urgency.EMERGENCY,
                    context={"location": "plant-9"})
    result = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert result.status != "refused"


def test_an_empty_proposal_list_means_no_proposals():
    """admit() reads [] as 'no proposals' and resolves -- same as the REST door."""
    hub = _hub()
    intent = Intent(intent=IntentClass("control_access"), urgency=Urgency.ALERT,
                    context={"action_types": ["lock"], "proposed_actions": []})
    result = asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))
    assert result.status != "refused"
    assert {(r.device_id, r.action) for r in result.results} == {("cell-2-lock", "lock")}
