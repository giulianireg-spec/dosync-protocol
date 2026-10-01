"""DoSync devices can be described as valid WoT TD 1.1 Things without losing governance.

tools/export_thing_descriptions.py exports manifests as Thing Descriptions with
a `dosync:` extension vocabulary, validates them against the official TD 1.1
JSON Schema (vendored), and rebuilds the devices from the TDs alone. On the
committed corpora no device carries the `location` field (the reference
deployment's July snapshot writes places as tags), so a corpus-only check could
not notice the location being lost; the device below carries every field
governance reads.
"""
import json

from tools.export_thing_descriptions import (_validator, governance_fields, manifest_to_td,
                                             run, td_to_manifest)
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, EventSpec,
                           SensorSpec, Severity)


def _full_device():
    return CapabilityManifest(
        device_id="td-probe-1", device_name="Line 3 conveyor", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["machinery", "emergency"],
        actuators=[ActuatorSpec("stop", "stop", "Stop"),
                   ActuatorSpec("set_speed", "set_speed", "Speed",
                                params_schema={"type": "object",
                                               "properties": {"speed": {"type": "number"}}},
                                execution_model="long_running")],
        sensors=[SensorSpec(id="temp", type="temperature", unit="C", range=[0, 90]),
                 SensorSpec(id="running", type="boolean", kind="device_state")],
        events=[EventSpec("jam", Severity.ALERT, "Belt jam")],
        emergency_capable=True, emergency_actions=[{"action": "stop", "params": {}}],
        location="plant-1/line-3")


def test_a_device_with_every_governance_field_survives_the_td():
    m = _full_device()
    td = manifest_to_td(m)
    assert not list(_validator().iter_errors(td)), "the exported TD is not valid TD 1.1"
    assert governance_fields(td_to_manifest(td)) == governance_fields(m)


def test_an_action_invoked_through_the_td_goes_through_the_hub():
    td = manifest_to_td(_full_device())
    form = td["actions"]["stop"]["forms"][0]
    assert form["href"] == "v1/device/action" and form["op"] == "invokeaction", \
        "the TD must address the hub's governed action endpoint, not the device"
    fixed = td["actions"]["stop"]["input"]["properties"]
    assert fixed["device_id"] == {"const": "td-probe-1"} and fixed["action"] == {"const": "stop"}


def test_every_corpus_device_exports_valid_and_resolves_identically():
    r = run(None)
    assert r["valid"] == r["devices"] and not r["invalid"], r["invalid"]
    assert r["fields_preserved"] == r["devices"], f"governance fields lost for {r['lost']}"
    assert r["identical_plans"] == r["scenarios"], f"plans differ: {r['differing']}"
