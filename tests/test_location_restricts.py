"""A location in the context restricts where an intent acts.

Since the capability gate (2026-09-04) a device takes part if it declares what
the intent needs, and context.location only added points. Every capable device
acted wherever it was: an intent for the main bedroom light lit every light in
the house, and control_access with `unlock` would have opened every lock that
could open. The tag vocabulary said "location is what makes a targeted intent
targeted" and the MCP described `location` as the place to act; neither was true.

An intent class now declares location_role. "restricts" (the default): with a
location in the context, only devices tagged with it act. "informs": the
location only says where the situation is -- alert_anomaly and notify, whose
notifier is not in that room, and ensure_safety, which protects people
everywhere. An emergency follows its class: a restricting emergency stays in its
zone (see test_device_location.py for the hierarchy and the unknown-location
rule).
"""
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

from dosync.hub import CapabilityMatchingResolver, CapabilityRegistry
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, SensorSpec, Urgency)


def _device(did, tags, actuators=(), sensors=(), emergency=False):
    return CapabilityManifest(
        device_id=did, device_name=did, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=list(tags), emergency_capable=emergency,
        actuators=[ActuatorSpec(id=a, type=a, description="") for a in actuators],
        sensors=list(sensors))


def _resolver(devices, resolution):
    registry = CapabilityRegistry()
    for d in devices:
        registry.register(d)
    resolver = CapabilityMatchingResolver(registry)
    resolver._get_resolution = lambda intent: dict(resolution)
    return resolver


def _acting(resolver, context, urgency=Urgency.INFO, name="light_room"):
    plan = resolver.resolve(Intent(intent=IntentClass(name), urgency=urgency, context=context))
    return {a.device_id for a in plan.actions}


LAMPS = [_device("lamp-main", ["light", "main-bedroom"], ["turn_on"], emergency=True),
         _device("lamp-kids", ["light", "bedroom"], ["turn_on"], emergency=True),
         _device("lamp-kitchen", ["light", "kitchen"], ["turn_on"], emergency=True)]
LIGHT = {"tags": ["light"], "actuators": ["turn_on"], "location_role": "restricts"}


def test_a_location_restricts_which_devices_act():
    assert _acting(_resolver(LAMPS, LIGHT), {"location": "main-bedroom"}) == {"lamp-main"}


def test_without_a_location_every_capable_device_acts_as_before():
    assert _acting(_resolver(LAMPS, LIGHT), {}) == {"lamp-main", "lamp-kids", "lamp-kitchen"}


def test_explain_reports_the_devices_outside_the_location_and_why():
    resolver = _resolver(LAMPS, LIGHT)
    report = resolver.explain(Intent(intent=IntentClass("light_room"), urgency=Urgency.INFO,
                                     context={"location": "main-bedroom"}))
    included = {d["device_id"] for d in report["included"]}
    reasons = {d["device_id"]: d["reason"] for d in report["excluded"]}
    assert included == {"lamp-main"}, "explain and resolve disagree about who acts"
    assert "main-bedroom" in reasons["lamp-kids"] and "main-bedroom" in reasons["lamp-kitchen"]


def test_an_emergency_follows_its_class_role():
    # A class that restricts keeps its zone in an emergency too -- an emergency
    # stop on one line is not a plant-wide stop. The lamps ARE emergency_capable,
    # so this also shows the force-inclusion no longer pulls in the rest.
    assert _acting(_resolver(LAMPS, LIGHT), {"location": "main-bedroom"},
                   urgency=Urgency.EMERGENCY) == {"lamp-main"}
    # A class whose location informs (ensure_safety: protect people everywhere)
    # acts on every capable device, wherever the emergency is.
    informs = {**LIGHT, "location_role": "informs"}
    assert _acting(_resolver(LAMPS, informs), {"location": "main-bedroom"},
                   urgency=Urgency.EMERGENCY) == {"lamp-main", "lamp-kids", "lamp-kitchen"}


def test_an_informs_class_keeps_a_device_that_is_in_no_place():
    notifier = _device("notifier-sms", ["notification"], ["notify"])
    resolver = _resolver([notifier], {"tags": ["notification"], "actuators": ["notify"],
                                      "location_role": "informs"})
    assert _acting(resolver, {"location": "kitchen"}, urgency=Urgency.ALERT,
                   name="alert_anomaly") == {"notifier-sms"}, \
        "an alert about the kitchen was not sent because the notifier is not in the kitchen"


def test_a_status_query_reads_only_where_its_context_says():
    temp = SensorSpec(id="temperature", type="temperature")
    sensors = [_device("dht-kitchen", ["sensor", "kitchen"], sensors=[temp]),
               _device("dht-bedroom", ["sensor", "bedroom"], sensors=[temp])]
    resolver = _resolver(sensors, {"tags": [], "actuators": [], "location_role": "restricts"})
    intent = Intent(intent=IntentClass("report_status"), urgency=Urgency.INFO,
                    context={"location": "kitchen"})
    assert {a.device_id for a in resolver.resolve(intent).actions} == {"dht-kitchen"}
    assert {d["device_id"] for d in resolver.explain(intent)["included"]} == {"dht-kitchen"}


def test_status_explain_and_resolve_agree_on_the_environment_scope():
    own_state = SensorSpec(id="power", type="boolean", kind="device_state")
    temp = SensorSpec(id="temperature", type="temperature")
    sensors = [_device("plug-01", ["sensor"], sensors=[own_state]),
               _device("dht-01", ["sensor"], sensors=[temp])]
    resolver = _resolver(sensors, {"tags": [], "actuators": []})
    intent = Intent(intent=IntentClass("report_status"), urgency=Urgency.INFO,
                    context={"scope": "environment"})
    read = {a.device_id for a in resolver.resolve(intent).actions}
    explained = {d["device_id"] for d in resolver.explain(intent)["included"]}
    assert read == explained == {"dht-01"}, "explain reported a device the status query does not read"


def test_the_universal_classes_declare_their_location_role():
    from dosync.db import DoSyncDB
    db = DoSyncDB(":memory:")
    db.init()
    roles = {c["name"]: c["location_role"] for c in db.list_intent_classes()}
    assert roles == {"ensure_safety": "informs", "alert_anomaly": "informs",
                     "control_access": "restricts", "report_status": "restricts",
                     "notify": "informs"}


def test_the_api_takes_validates_and_keeps_location_role():
    import dosync.server as srv
    client = TestClient(srv.app)
    body = {"name": "tell_family_loc", "urgency": "info", "resolution_tags": ["notification"],
            "resolution_actuators": ["notify"], "location_role": "informs"}
    assert client.post("/v1/intent-classes", json=body).json()["location_role"] == "informs"
    body.pop("location_role")
    assert client.post("/v1/intent-classes", json=body).json()["location_role"] == "informs", \
        "re-registering without the field reset the class to restrict"
    listed = {c["name"]: c["location_role"]
              for c in client.get("/v1/intent-classes").json()["intent_classes"]}
    assert listed["tell_family_loc"] == "informs"
    bad = {**body, "name": "bad_loc_role", "location_role": "sometimes"}
    assert client.post("/v1/intent-classes", json=bad).status_code == 422


def test_an_existing_database_is_migrated():
    from dosync.db import DoSyncDB
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE intent_classes (
        name TEXT PRIMARY KEY, urgency TEXT NOT NULL DEFAULT 'info',
        resolution_tags TEXT NOT NULL DEFAULT '[]', resolution_actuators TEXT NOT NULL DEFAULT '[]',
        resolution_sensors TEXT NOT NULL DEFAULT '[]', description TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT 'general', is_universal INTEGER NOT NULL DEFAULT 0,
        composition_kind TEXT DEFAULT NULL, created_at REAL NOT NULL)""")
    con.execute("INSERT INTO intent_classes (name, resolution_tags, created_at) "
                "VALUES ('my_custom', '[\"light\"]', 0)")
    con.commit(); con.close()

    db = DoSyncDB(path)
    db.init()
    roles = {c["name"]: c["location_role"] for c in db.list_intent_classes()}
    assert roles["my_custom"] == "restricts", "a class from before the column did not default to restrict"
    assert roles["alert_anomaly"] == "informs" and roles["notify"] == "informs"
