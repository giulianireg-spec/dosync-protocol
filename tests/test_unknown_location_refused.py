"""A location nothing is at is refused, instead of resolving to no device.

Once a location restricts where an intent acts, a typo ("main-bedrom") would
match no device and the intent would "complete" with zero actions -- a silent
failure. When the class restricts by location, it is not an emergency, and no
registered device declares the tag, the hub refuses the intent with 422 and
counts it as unknown_location. An alert's location only says where something
happened, and an emergency never narrows, so neither is refused.
"""
from fastapi.testclient import TestClient


def _setup():
    import dosync.server as srv
    client = TestClient(srv.app)
    assert client.post("/v1/devices/register", json={
        "device_id": "lamp-loc-refusal", "device_name": "lamp", "manufacturer": "t",
        "model": "t", "firmware": "1", "category": "actuator",
        "tags": ["light", "main-bedroom-refusal"],
        "actuators": [{"id": "turn_on", "type": "turn_on", "description": ""}]}).status_code == 200
    assert client.post("/v1/intent-classes", json={
        "name": "light_room_refusal", "urgency": "info", "resolution_tags": ["light"],
        "resolution_actuators": ["turn_on"]}).status_code == 200
    return client


def _fire(client, intent, location, urgency="info"):
    return client.post("/v1/intent/async", json={
        "intent": intent, "urgency": urgency, "context": {"location": location}})


def _unknown_location_count(client):
    return client.get("/v1/status").json()["intents_rejected"]["unknown_location"]


def test_a_location_no_device_is_at_is_refused_and_counted():
    client = _setup()
    before = _unknown_location_count(client)
    r = _fire(client, "light_room_refusal", "main-bedrom")
    assert r.status_code == 422 and "main-bedrom" in r.json()["detail"]
    assert _unknown_location_count(client) == before + 1


def test_a_location_a_device_is_at_is_accepted():
    client = _setup()
    assert _fire(client, "light_room_refusal", "main-bedroom-refusal").status_code == 200


def test_an_informing_location_is_never_refused():
    client = _setup()
    assert _fire(client, "alert_anomaly", "sector-7g", urgency="alert").status_code == 200, \
        "an alert was refused for naming a place no device is at"


def test_an_emergency_location_is_never_refused():
    client = _setup()
    assert _fire(client, "light_room_refusal", "nowhere-at-all", urgency="emergency").status_code == 200
