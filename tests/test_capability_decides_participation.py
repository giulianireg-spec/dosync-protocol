"""A device that declares what an intent needs takes part, whatever its tags.

Protocol 0.5 (spec §6): capabilities select, tags only rank. The resolver
realises "capable" as a positive term of the score -- 12 per matching actuator
or sensor -- and excludes every device whose score is zero. So the rule holds
only while those weights are positive: a sensitivity run for the 0.7.0 paper
found that setting the actuator weight to zero drops capable devices that share
no tag with the class. No operator can change the weights (they are constants),
but whoever tunes the ranking later could break the protocol's central rule
without noticing. These tests make the assumption a guarantee.
"""
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, SensorSpec, Urgency)
from dosync.resolvers import CapabilityMatchingResolver


def test_capability_weights_are_positive():
    assert CapabilityMatchingResolver._W_ACTUATOR > 0, (
        "a matching actuator must add to the score: a capable device with no tag "
        "overlap would score zero and be excluded, and tags would decide again")
    assert CapabilityMatchingResolver._W_SENSOR > 0, (
        "a matching sensor must add to the score, for the same reason")


def _hub_with(device, cls_name, actuators=(), sensors=()):
    hub = DoSyncHub(db_path=":memory:")
    hub.db.save_intent_class(name=cls_name, urgency="info", resolution_tags=["light"],
                             resolution_actuators=list(actuators),
                             resolution_sensors=list(sensors) or None,
                             description="participation probe", domain="test")
    hub.register_device(device)
    return hub


def _device(**kw):
    fields = dict(device_id="probe-1", device_name="Probe", manufacturer="t", model="t",
                  firmware="1", category=DeviceCategory.ACTUATOR, tags=["plug"],
                  sensors=[], actuators=[], events=[])
    fields.update(kw)
    return CapabilityManifest(**fields)


def test_a_capable_device_with_no_shared_tag_takes_part():
    hub = _hub_with(_device(actuators=[ActuatorSpec("turn_on", "turn_on", "Turn on")]),
                    "participation_by_actuator", actuators=["turn_on"])
    plan = hub.resolver.resolve(Intent(intent=IntentClass("participation_by_actuator"),
                                       urgency=Urgency.INFO, context={}))
    assert {a.device_id for a in plan.actions} == {"probe-1"}, \
        "a plug declaring turn_on, tagged `plug`, was left out of a class tagged `light`"


def test_a_device_with_a_needed_sensor_and_no_shared_tag_takes_part():
    device = _device(sensors=[SensorSpec("smoke", "smoke", "Smoke")])
    device.category = DeviceCategory.SENSOR
    hub = _hub_with(device, "participation_by_sensor", actuators=["notify"], sensors=["smoke"])
    report = hub.resolver.explain(Intent(intent=IntentClass("participation_by_sensor"),
                                         urgency=Urgency.INFO, context={}))
    assert "probe-1" in {d["device_id"] for d in report["included"]}, \
        "a device declaring the smoke sensor the class needs was left out"
