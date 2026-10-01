"""The protocol's JSON schemas accept what the reference hub produces.

spec/schemas/ is the machine-checkable half of the contract: what a third party
validates its implementation against. Nothing compared it with the hub, and it
drifted without anyone seeing: the manifest schema forbids unknown properties
and did not know `location` -- the central field of protocol 0.5 -- nor the
actuator execution fields or the sensor `kind` that 0.4 added. A manifest the
reference hub returned was invalid against the protocol's own schema.

Every document below is produced by the real code, most of it through the API,
and validated against the schema that describes it.
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

SCHEMAS = Path(__file__).resolve().parent.parent / "spec" / "schemas"


def _validator(name):
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _errors(validator, doc):
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message[:140]}"
            for e in validator.iter_errors(doc)]


def _full_manifest(device_id):
    """A registration using every field a device may declare."""
    return {
        "device_id": device_id, "device_name": "Schema probe", "manufacturer": "t",
        "model": "t", "firmware": "1", "category": "actuator", "tags": ["machinery"],
        "emergency_capable": True,
        "emergency_actions": [{"action": "stop", "params": {}}],
        "actuators": [
            {"id": "stop", "type": "stop", "description": "Stop the line"},
            {"id": "move", "type": "move_to", "execution_model": "long_running",
             "supports_progress": True, "supports_cancel": True, "emits_telemetry": True,
             "verify_with": {"sensor_id": "running", "expected_reading": False,
                             "deadline_s": 3.0}}],
        "sensors": [
            {"id": "temp", "type": "temperature", "unit": "C", "range": [0, 90]},
            {"id": "running", "type": "boolean", "kind": "device_state"}],
    }


def test_every_schema_is_itself_valid():
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_the_schema_examples_validate_against_their_own_schema():
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        v = Draft202012Validator(schema)
        for example in schema.get("examples", []):
            assert not _errors(v, example), f"{path.name}: its own example is invalid: {_errors(v, example)}"


def test_a_manifest_the_hub_returns_is_valid():
    import dosync.server as srv
    client = TestClient(srv.app)
    assert client.post("/v1/devices/register", json=_full_manifest("schema-probe-1")).status_code == 200
    assert client.patch("/v1/devices/schema-probe-1", json={"location": "plant-1/line-3"}).status_code == 200
    manifest = srv.hub.registry.get("schema-probe-1").to_dict()
    assert manifest.get("location") == "plant-1/line-3"
    errs = _errors(_validator("capability-manifest.schema.json"), manifest)
    assert not errs, "the hub's own manifest is invalid against the protocol schema:\n  " + "\n  ".join(errs)


def test_every_shipped_example_manifest_is_valid():
    from dosync.declarative import bundled_examples_dir, load_directory
    v = _validator("capability-manifest.schema.json")
    for manifest, _ in load_directory(str(bundled_examples_dir())):
        errs = _errors(v, manifest.to_dict())
        assert not errs, f"{manifest.device_id}: {errs}"


def test_the_location_pattern_accepts_exactly_what_the_hub_accepts():
    """The schema restates normalize_location() as a regex. A reader of the spec
    must not be told a place is valid that the hub refuses, or the reverse."""
    from dosync.models import normalize_location
    schema = json.loads((SCHEMAS / "capability-manifest.schema.json").read_text(encoding="utf-8"))
    loc = schema["properties"]["location"]
    pattern = re.compile(loc["pattern"])
    cases = ["kitchen", "plant-1/line-3/cell-2", "iss/us-lab/rack-4", "Sala de máquinas/línea 3",
             "a b/c", "/plant", "plant/", "plant//line", "plant/ line", "plant /line",
             "line\t3", "x" * (loc["maxLength"] + 1)]
    for case in cases:
        try:
            hub_ok = normalize_location(case) == case
        except ValueError:
            hub_ok = False
        schema_ok = bool(pattern.match(case)) and len(case) <= loc["maxLength"]
        assert schema_ok == hub_ok, f"{case!r}: hub {'accepts' if hub_ok else 'refuses'}, schema {'accepts' if schema_ok else 'refuses'}"


def _probe_scene(client):
    client.post("/v1/devices/register", json=_full_manifest("schema-probe-2"))
    client.post("/v1/intent-classes", json={
        "name": "schema_probe_stop", "urgency": "info", "resolution_tags": ["machinery"],
        "resolution_actuators": ["stop"], "location_role": "restricts",
        "description": "Schema test class"})


def test_an_intent_the_hub_accepts_is_valid():
    """Every field the hub's request model accepts, including the idempotency key
    protocol 0.2 added, must be allowed by the schema that describes the request."""
    import dosync.server as srv
    body = {"intent": "schema_probe_stop", "urgency": "info", "subject": "line 3",
            "source": "api", "context": {"location": "plant-1"},
            "idempotency_key": "6f1d6c5e-0b7a-4c53-9a53-1f2a3b4c5d6e"}
    client = TestClient(srv.app)
    _probe_scene(client)
    assert client.post("/v1/intent/async", json=body).status_code == 200
    errs = _errors(_validator("intent.schema.json"), body)
    assert not errs, "a request the hub accepts is invalid against the schema:\n  " + "\n  ".join(errs)


def test_an_action_plan_the_resolver_produces_is_valid():
    import dosync.server as srv
    from dosync.models import Intent, IntentClass, Urgency
    client = TestClient(srv.app)
    _probe_scene(client)
    plan = srv.hub.resolver.resolve(Intent(intent=IntentClass("schema_probe_stop"),
                                           urgency=Urgency.INFO, context={}))
    assert plan.actions, "the probe scene resolved to nothing"
    # A plan crosses the wire in one place: an external resolver's answer to
    # POST /resolve (RESOLVER-SPEC §5), which ExternalResolver parses back into
    # an ActionPlan. This is that document, built from a plan the reference
    # resolver produced.
    wire = {"intent_id": plan.intent_id, "urgency": plan.urgency.value,
            "actions": [{"device_id": a.device_id, "action": a.action,
                         "params": a.params, "relevance_score": a.relevance_score}
                        for a in plan.actions]}
    errs = _errors(_validator("action-plan.schema.json"), wire)
    assert not errs, "a plan the resolver produced is invalid:\n  " + "\n  ".join(errs)


def test_an_intent_result_the_hub_returns_is_valid():
    import time
    import dosync.server as srv
    with TestClient(srv.app) as client:
      _probe_scene(client)
      started = client.post("/v1/intent/async", json={"intent": "schema_probe_stop", "urgency": "info", "context": {}})
      intent_id = started.json()["intent_id"]
      result = {}
      for _ in range(60):
        result = client.get(f"/v1/intent/{intent_id}").json()
        if result.get("status") != "pending":
            break
        time.sleep(0.05)
    assert result.get("status") != "pending", "execution never finished; the test did not reach a result"
    errs = _errors(_validator("intent-result.schema.json"), result)
    assert not errs, "a result the hub returned is invalid:\n  " + "\n  ".join(errs)
