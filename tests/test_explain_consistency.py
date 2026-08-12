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


def _setup(device_tags, resolution, urgency=Urgency.ALERT):
    # ALERT by default: isolates the hard filter from the emergency machinery
    # (force-inclusion + full-capability fallback), which has its own tests.
    reg = CapabilityRegistry()
    reg.register(_device(device_tags))
    r = CapabilityMatchingResolver(reg)
    r._get_resolution = lambda intent: resolution
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=urgency, context={})
    return r, intent


def _agree(r, intent):
    in_plan = any(a.device_id == "dev-x" for a in r.resolve(intent).actions)
    exp = r.explain(intent)
    in_explain = any(d["device_id"] == "dev-x" for d in exp["included"])
    return in_plan, in_explain, exp


def test_tagless_device_is_excluded_in_both():
    # Specific tags demanded, device has none. Both paths must agree it is out.
    #
    # The exclusion REASON changed with the shared candidate set: this device is
    # no longer scored-then-hard-filtered, it is never a candidate, and the
    # reason says so. Asserting "hard filter" here would now be asserting a
    # sentence rather than the behaviour — the behaviour is that resolve and
    # explain agree, which is what this file exists to guard.
    r, intent = _setup(["light", "living-room"], {"tags": ["security", "lock"], "actuators": ["lock"]})
    in_plan, in_explain, exp = _agree(r, intent)
    assert in_plan is False
    assert in_explain is False, "explain contradicted resolve on the hard filter"
    reason = exp["excluded"][0]["reason"]
    assert "not evaluated" in reason, reason


def test_hard_filter_still_applies_to_emergency_forced_candidates():
    """The one path where a device IS a candidate without any tag overlap.

    An emergency_capable device is force-included as a candidate regardless of
    tags, so it reaches scoring and the hard filter can still reject it. Without
    this case the shared candidate set would quietly retire hard-filter coverage
    — the filter would look dead while remaining live exactly where the stakes
    are highest.
    """
    reg = CapabilityRegistry()
    dev = _device(["light", "living-room"])
    dev.emergency_capable = True
    reg.register(dev)
    r = CapabilityMatchingResolver(reg)
    r._get_resolution = lambda intent: {"tags": ["security", "lock"],
                                        "actuators": ["lock"]}
    intent = Intent(intent_id="i1", intent=IntentClass("control_access"),
                    urgency=Urgency.EMERGENCY, context={})
    candidates = {d.device_id for d in r._candidates(intent, r._get_resolution(intent))}
    assert "dev-x" in candidates, "emergency force-inclusion stopped working"
    bd = r._score_breakdown(reg.get("dev-x"), intent, r._get_resolution(intent))
    assert bd.hard_filtered is True
    assert "hard filter" in bd.exclusion_reason()


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


def test_emergency_forces_inclusion_in_both():
    """F2b/F5: at EMERGENCY, an emergency_capable device that fails the hard
    filter is force-included by resolve (full capability set) and explain must
    report the same — consistency in the inclusion direction too."""
    r, intent = _setup(["light", "living-room"],
                       {"tags": ["security", "lock"], "actuators": ["lock"]},
                       urgency=Urgency.EMERGENCY)
    in_plan, in_explain, exp = _agree(r, intent)
    assert in_plan is True, "resolve must force-include emergency_capable devices"
    assert in_explain is True, "explain must mirror the emergency force-inclusion"
    entry = next(d for d in exp["included"] if d["device_id"] == "dev-x")
    assert entry["score_breakdown"].get("forced_emergency") is True
