"""An independent W3C WoT consumer uses DoSync's devices, governed (W1-W5, 2026-10-12).

Eclipse Thingweb node-wot -- which knows nothing of DoSync -- discovers the hub
at /.well-known/wot, reads a production cell's Thing Description, invokes its
actions and reads a property using nothing but the TD. Every invocation crosses
SAMUEL: an action the operator marked is held for a person (202), the rest runs,
and an action the device does not declare is not in the TD at all.

Also, without Node: every TD the hub serves is valid against the official TD 1.1
JSON Schema, the directory TD too, and an exported TD imports back to the same
governance (round trip).

node-wot runs where `tools/wot_interop/node_modules` exists (`npm ci` there; CI
job `wot-interop` does it); elsewhere that test is skipped.
"""
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from dosync.models import ActuatorSpec, CapabilityManifest, DeviceCategory, SensorSpec

REPO = Path(__file__).resolve().parent.parent
INTEROP = REPO / "tools" / "wot_interop"
SCHEMA = REPO / "tools" / "vendor" / "w3c-wot-td-1.1-validation.schema.json"
OPERATOR = "wot-interop-operator"

CELL = CapabilityManifest(
    device_id="plant1-cell2-robot", device_name="Cell 2 robot arm", manufacturer="t", model="t",
    firmware="1", category=DeviceCategory.ACTUATOR, tags=["machinery"],
    sensors=[SensorSpec(id="spindle_torque", type="torque", unit="N.m", range=[0, 80])], events=[],
    actuators=[ActuatorSpec("start_cycle", "start", "Start the cycle"),
               ActuatorSpec("stop_cycle", "stop", "Stop the cycle"),
               ActuatorSpec("release_clamp", "unlock", "Release the part clamp")],
    location="plant-1/line-3/cell-2")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hub_url(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    import dosync.server as srv
    from dosync.auth import AuthManager, set_auth_manager, get_auth_manager
    from dosync.executor import SimulatedExecutor
    from dosync.hub import DoSyncHub
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", OPERATOR)
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = SimulatedExecutor()
    hub.register_device(CELL)
    # The operator marks one action: releasing a clamp waits for a person.
    hub.set_confirmation_marks([{"actions": ["unlock"], "reason": "a person releases a clamp"}])
    monkeypatch.setattr(srv, "hub", hub)
    monkeypatch.setattr(srv, "executor", SimulatedExecutor(failure_rate=0.0))
    previous = get_auth_manager()
    manager = AuthManager(hub.db, enabled=True)
    set_auth_manager(manager)
    token = manager.generate_key("wot-consumer", token="wot-interop-agent-token")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", token, hub
    server.should_exit = True
    t.join(timeout=5)
    set_auth_manager(previous)


def _get(url, token=None):
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.headers.get("Content-Type"), json.loads(r.read())


def test_served_tds_are_valid_td_11_and_round_trip(hub_url):
    from jsonschema import Draft7Validator
    from dosync.wot_export import td_to_manifest
    url, token, hub = hub_url
    validator = Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    st, ctype, directory = _get(f"{url}/.well-known/wot")          # public: no token
    assert st == 200 and ctype.startswith("application/td+json")
    assert directory["@type"] == "ThingDirectory"
    assert not list(validator.iter_errors(directory)), list(validator.iter_errors(directory))[:1]
    st, ctype, things = _get(f"{url}/v1/things", token)
    assert st == 200 and len(things) == 1
    st, ctype, td = _get(f"{url}/v1/things/{CELL.device_id}", token)
    assert ctype.startswith("application/td+json")
    errors = list(validator.iter_errors(td))
    assert not errors, errors[0].message
    # forms address the hub's governed binding, never the device
    hrefs = [f["href"] for a in td["actions"].values() for f in a["forms"]]
    assert all(h.startswith(f"v1/things/{CELL.device_id}/actions/") for h in hrefs)
    back = td_to_manifest(td)
    assert {(a.id, a.type) for a in back.actuators} == {(a.id, a.type) for a in CELL.actuators}
    assert back.location == CELL.location


def test_the_device_list_and_tds_need_the_hub_credential(hub_url):
    import urllib.error
    url, token, hub = hub_url
    for path in ("/v1/things", f"/v1/things/{CELL.device_id}"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(url + path)
        assert e.value.code == 401


def test_node_wot_uses_the_devices_and_samuel_governs_it(hub_url):
    import os
    if shutil.which("node") is None or not (INTEROP / "node_modules").is_dir():
        # CI's wot-interop job sets this so a missing install fails, not skips.
        if os.environ.get("DOSYNC_REQUIRE_NODE_WOT"):
            pytest.fail("node-wot is required here and not installed")
        pytest.skip("node-wot not installed (npm ci in tools/wot_interop)")
    url, token, hub = hub_url
    import urllib.request
    reading = json.dumps({"device_id": CELL.device_id, "event_id": "reading", "severity": "info",
                          "data": {"spindle_torque": 41.5}}).encode()
    urllib.request.urlopen(urllib.request.Request(
        f"{url}/v1/event", data=reading, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}), timeout=10)
    run = subprocess.run(["node", "consume.mjs", url, token, CELL.device_id,
                          "start_cycle", "release_clamp", "--read", "spindle_torque"],
                         cwd=INTEROP, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["directory"] == "ThingDirectory"
    assert out["td_title"] == "Cell 2 robot arm"
    assert set(out["td_actions"]) == {"start_cycle", "stop_cycle", "release_clamp"}
    assert out["actions"]["start_cycle"]["ok"] and out["actions"]["start_cycle"]["value"]["status"] == "done"
    held = out["actions"]["release_clamp"]
    assert held["ok"] and held["value"]["status"] == "held_for_confirmation", held
    assert out["property"] == {"ok": True, "value": 41.5}
    # what ran and what waits is on the hub, governed and recorded
    pending = hub.pending_confirmations()
    assert [a["action"] for p in pending for a in p["actions"]] == ["unlock"]
    sources = {e.get("source") for e in hub.audit_log.entries() if e.get("type") == "intent_executed"}
    assert "wot" in sources


def test_a_policy_refusal_reaches_the_wot_consumer_as_403(hub_url):
    import urllib.error
    import urllib.request
    url, token, hub = hub_url
    from dosync.policies import DeviceExclusionPolicy, PolicyEngine
    engine = PolicyEngine()
    engine.add(DeviceExclusionPolicy(intent_classes=["operate_device"], excluded_device_ids=[CELL.device_id],
                                     reason="cell 2 is locked out"))
    hub.policy_engine = engine
    req = urllib.request.Request(f"{url}/v1/things/{CELL.device_id}/actions/start_cycle", data=b"",
                                 method="POST", headers={"Authorization": f"Bearer {token}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=30)
    body = json.loads(e.value.read())
    assert e.value.code == 403 and body["status"] == "not_run", body
    # an action the device does not declare does not exist for the consumer
    req = urllib.request.Request(f"{url}/v1/things/{CELL.device_id}/actions/fly", data=b"",
                                 method="POST", headers={"Authorization": f"Bearer {token}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=30)
    assert e.value.code == 404
