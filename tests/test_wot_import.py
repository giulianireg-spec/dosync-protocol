"""Importing W3C WoT Thing Descriptions, and governing the Things they describe.

Measured beyond these tests (tools/wot_import_report.py): all 701 distinct TDs of
the W3C plugfest repository import; and two of Eclipse Thingweb node-wot's own
example Things, served over HTTP, were governed end to end
(tools/wot_nodewot_e2e.py). These tests pin the rules and run a stand-in for
node-wot's counter, so CI needs no Node.js.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from dosync.adapters import AdapterExecutor
from dosync.adapters.wot import WotHttpAdapter, expand
from dosync.hub import DoSyncHub
from dosync.models import Intent, IntentClass, Urgency
from dosync.wot_import import import_td, map_action

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name,affordance,expected", [
    ("power", {"dosync:capability": "turn_on"}, ("turn_on", "dosync")),
    ("doIt", {"@type": "iot:TurnOnAction"}, ("turn_on", "@type")),
    ("lightOff", {"@type": ["saref:SwitchOffCommand"]}, ("turn_off", "@type")),
    ("turnOn", {}, ("turn_on", "name-verb")),
    ("switch_off", {}, ("turn_off", "name-verb")),
    ("toggle", {}, ("toggle", "name-verb")),
    ("openValve", {}, ("open", "name-verb")),
    ("stopPump", {}, ("stop", "name-verb")),
    ("setBrightness", {}, ("set_brightness", "name-verb")),
    ("makeDrink", {}, ("make_drink", "own-name")),
    ("gotoPosTurntable", {}, ("goto_pos_turntable", "own-name")),
])
def test_actions_map_in_layers_and_say_how(name, affordance, expected):
    assert map_action(name, affordance) == expected


def _valve_td(**extra):
    return {"@context": "https://www.w3.org/2022/wot/td/v1.1", "title": "Valve", "id": "urn:x:valve-1",
            "base": "http://valve.local/",
            "actions": {"openValve": {"forms": [{"href": "actions/open"}]},
                        "closeValve": {"forms": [{"href": "actions/close"}]},
                        "startPump": {"forms": [{"href": "actions/a"}]},
                        "startHeater": {"forms": [{"href": "actions/b"}]}},
            "properties": {"level": {"type": "number", "unit": "%", "forms": [{"href": "properties/level"}]},
                           "temperature": {"type": "number", "readOnly": True, "forms": [{"href": "properties/t"}]}},
            **extra}


def test_a_third_party_thing_imports_with_its_mapping_reported():
    m, report = import_td(_valve_td())
    types = {a.type for a in m.actuators}
    assert {"open", "close", "set_level"} <= types, "open/close: an opposite pair the hub can enforce"
    assert {"start_pump", "start_heater"} <= types and "start" not in types, \
        "two affordances that would both be 'start' keep their own names"
    assert {s.type for s in m.sensors} >= {"temperature", "level"}
    assert m.adapter_config["affordances"]["open"]["forms"][0]["href"] == "http://valve.local/actions/open"
    assert report["provenance"]["open"] == "name-verb" and report["executable"]["open"]


def test_a_third_party_td_never_sets_place_or_emergency_actions():
    m, _ = import_td(_valve_td(location="plant/line-1", emergencyActions=["close"]))
    assert m.location == "" and m.emergency_actions == [] and not m.emergency_capable


def test_a_device_dosync_exported_comes_back_with_everything_governance_reads():
    from tools.export_thing_descriptions import manifest_to_td
    from tools.recall_benchmark import load_registry
    same = n = 0
    for reg in ("benchmarks/fixtures/prod_registry_anonymized.json", "benchmarks/corpus/industrial_registry.json"):
        hub = DoSyncHub(db_path=":memory:")
        load_registry(REPO / reg, hub)
        for m in hub.registry.active():
            back, _ = import_td(manifest_to_td(m))
            key = lambda x: (x.device_id, sorted(a.type for a in x.actuators),
                             sorted((s.type, getattr(s, "kind", None)) for s in x.sensors), x.location or "",
                             x.emergency_capable, sorted(e["action"] for e in x.emergency_actions),
                             x.category.value, sorted(x.tags))
            n += 1
            same += key(m) == key(back)
    assert (same, n) == (39, 39)


def test_uri_templates_expand():
    assert expand("http://h/c/actions/increment{?step}", {}) == ("http://h/c/actions/increment", {})
    assert expand("http://h/c/actions/increment{?step}", {"step": 3}) == ("http://h/c/actions/increment?step=3", {})
    assert expand("http://h/m/actions/makeDrink{?drinkId,size}", {"drinkId": "latte", "sugar": 1}) == \
        ("http://h/m/actions/makeDrink?drinkId=latte", {"sugar": 1})


class _Counter(BaseHTTPRequestHandler):
    """A stand-in for node-wot's example counter, with its URI-template form."""
    count = 0

    def do_POST(self):
        path, _, query = self.path.partition("?")
        if path == "/counter/actions/increment":
            _Counter.count += int(dict(p.split("=") for p in query.split("&") if p).get("step", 1))
        elif path == "/counter/actions/reset":
            _Counter.count = 0
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(204); self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def counter_thing():
    server = HTTPServer(("127.0.0.1", 0), _Counter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/counter/"
    td = {"@context": "https://www.w3.org/2022/wot/td/v1.1", "title": "Counter", "id": "urn:uuid:counter",
          "actions": {"increment": {"uriVariables": {"step": {"type": "integer"}},
                                    "forms": [{"href": base + "actions/increment{?step}", "op": ["invokeaction"],
                                               "htv:methodName": "POST"}]},
                      "reset": {"forms": [{"href": base + "actions/reset", "op": ["invokeaction"]}]}}}
    _Counter.count = 0
    yield td
    server.shutdown()


def test_an_imported_thing_is_governed_end_to_end(counter_thing):
    hub = DoSyncHub(db_path=":memory:")
    m, _ = import_td(counter_thing)
    m.location = "lab/bench"
    hub.register_device(m)
    # A known second place: a place no device is at is unknown, and the hub
    # refuses it before proposals are validated (spec §6.8 rule 3).
    other, _ = import_td({"title": "Kettle", "id": "urn:x:kettle", "actions": {"boil": {"forms": [{"href": "http://127.0.0.1:9/boil"}]}}})
    other.location = "lab/kitchen"
    hub.register_device(other)
    ex = AdapterExecutor(hub)
    ex.register(WotHttpAdapter(hub=hub))

    def operate(proposals, **ctx):
        return asyncio.run(hub.execute_intent(Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                                                     context={**ctx, "proposed_actions": proposals}), ex))
    r = operate([{"device_id": m.device_id, "action": "increment", "params": {"step": 2}}])
    assert r.results[0].success and _Counter.count == 2
    r = operate([{"device_id": m.device_id, "action": "explode"}])
    assert r.refused_proposals[0]["reason"] == "not_declared" and _Counter.count == 2
    r = operate([{"device_id": m.device_id, "action": "increment"}], location="lab/kitchen")
    assert r.refused_proposals[0]["reason"] == "outside_place" and _Counter.count == 2
    r = operate([{"device_id": m.device_id, "action": "reset"}])
    assert r.results[0].success and _Counter.count == 0


def test_the_hub_imports_a_td_and_the_operator_places_it():
    from fastapi.testclient import TestClient
    import dosync.server as srv
    c = TestClient(srv.app)
    r = c.post("/v1/things", json={"td": _valve_td(id="urn:x:valve-api"), "location": "plant/line-1",
                                   "emergency_actions": ["close"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location"] == "plant/line-1" and body["emergency_actions"] == ["close"]
    assert body["provenance"]["open"] == "name-verb"
    assert c.post("/v1/things", json={"td": _valve_td(id="urn:x:v2"), "emergency_actions": ["fly"]}).status_code == 422
    assert c.post("/v1/things", json={"td": {"no": "title"}}).status_code == 422
