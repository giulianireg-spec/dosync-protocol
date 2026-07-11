"""Regression: explain() must report the same inclusion decision as resolve().

Found 2026-07-10: explain() summed bonuses without applying the specific-tags
hard filter that _relevance_score applies, so a device failing the hard filter
(score 0.0 in resolve) could appear as 'included' with a positive score in the
transparency endpoint. The transparency endpoint must never diverge from the
real resolver.
"""
from dosync.hub import CapabilityRegistry, CapabilityMatchingResolver
from dosync.models import (CapabilityManifest, ActuatorSpec, Intent,
                           IntentClass, Urgency, DeviceCategory)


def _device(tags, emergency=True):
    return CapabilityManifest(
        device_id="dev-x", device_name="X", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=tags,
        actuators=[ActuatorSpec(id="p", type="turn_on", description="on")],
        sensors=[], events=[], emergency_capable=emergency, cert_tier="emergency",
    )


def _setup(device_tags, resolution):
    reg = CapabilityRegistry()
    reg.register(_device(device_tags))
    r = CapabilityMatchingResolver(reg)
    r._get_resolution = lambda intent: resolution
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=Urgency.EMERGENCY, context={})
    return r, intent


def _agree(r, intent):
    in_plan = any(a.device_id == "dev-x" for a in r.resolve(intent).actions)
    exp = r.explain(intent)
    in_explain = any(d["device_id"] == "dev-x" for d in exp["included"])
    return in_plan, in_explain, exp


def test_hard_filtered_device_is_excluded_in_both():
    # specific tags demanded, device has none -> resolve gives 0.0; explain must agree
    r, intent = _setup(["light", "living-room"], {"tags": ["security", "lock"], "actuators": ["lock"]})
    in_plan, in_explain, exp = _agree(r, intent)
    assert in_plan is False
    assert in_explain is False, "explain contradicted resolve on the hard filter"
    reason = exp["excluded"][0]["reason"]
    assert "hard filter" in reason


def test_matching_device_is_included_in_both():
    r, intent = _setup(["security", "lock"], {"tags": ["security", "lock"], "actuators": ["turn_on"]})
    in_plan, in_explain, _ = _agree(r, intent)
    assert in_plan is True
    assert in_explain is True


def test_generic_tags_only_no_hard_filter():
    # resolution uses only generic tags -> no hard filter; emergency bonus applies
    r, intent = _setup(["light"], {"tags": ["light"], "actuators": ["turn_on"]})
    in_plan, in_explain, _ = _agree(r, intent)
    assert in_plan is True and in_explain is True
