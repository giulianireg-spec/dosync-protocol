"""Resolver scoring validation (radar v9, 2026-07-21).

The scoring logic used to be duplicated: _relevance_score computed the score the
resolver DECIDED with, and explain() recomputed the same arithmetic to tell the
story — with a comment promising the two "must mirror exactly", a promise the
language did not enforce. If one drifted, the explanation would lie about why the
resolver chose what it chose.

Now there is ONE computation (_score_breakdown); resolve uses its .total, explain
reads its parts. These tests pin the property that matters: the explanation's
score always equals the decision's score, for any registry.
"""
import pytest

from dosync.hub import (CapabilityMatchingResolver, DoSyncHub, ScoreBreakdown)
from dosync.models import (ActuatorSpec, CapabilityManifest, CertTier,
                           DeviceCategory, Intent, IntentClass, SensorSpec,
                           Urgency)


def _reg(hub, did, tags, actuators=("turn_on",), emergency=False, sensors=False):
    hub.registry.register(CapabilityManifest(
        device_id=did, device_name=did, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=list(tags),
        sensors=[SensorSpec("s", "boolean", "s")] if sensors else [],
        events=[],
        actuators=[ActuatorSpec(id=a, type=a, description="") for a in actuators],
        emergency_capable=emergency, cert_tier=CertTier.STANDARD))


def _hub():
    hub = DoSyncHub(db_path=":memory:")
    _reg(hub, "lamp-living", ["light", "living"], ["turn_on"])
    _reg(hub, "siren", ["alarm", "emergency"], ["alarm"], emergency=True)
    _reg(hub, "tv", ["communication", "display", "emergency"], ["notify"], emergency=True)
    _reg(hub, "thermostat", ["climate"], ["set_temp"], sensors=True)
    _reg(hub, "lock-front", ["lock", "security"], ["lock", "unlock"])
    return hub


# ── The property: explanation score == decision score, always ────────────────

@pytest.mark.parametrize("intent_class,urgency,context", [
    ("ensure_safety", Urgency.EMERGENCY, {}),
    ("alert_anomaly", Urgency.ALERT, {}),
    ("control_access", Urgency.ALERT, {}),
    ("notify", Urgency.INFO, {}),
    ("ensure_safety", Urgency.EMERGENCY, {"location": "living"}),
])
def test_explanation_score_equals_decision_score(intent_class, urgency, context):
    """THE v9 guarantee: for every device, the score explain() reports is exactly
    the score _relevance_score (the decision path) computes. One source, so they
    cannot diverge."""
    hub = _hub()
    resolver = hub.resolver
    intent = Intent(intent_id="t", intent=IntentClass(intent_class),
                    urgency=urgency, context=context)
    resolution = resolver._get_resolution(intent)

    exp = resolver.explain(intent)
    explained = {d["device_id"]: d["score"] for d in exp["included"]}

    # The universe is the CANDIDATE SET, not the whole registry. This loop used
    # to iterate registry.all() and assert that every device scoring above zero
    # appeared as included — which encoded the divergence rather than the
    # guarantee: a device matching only on ACTUATOR scores 12 and is not a
    # candidate, so resolve() never acts on it. explain() listing it was the bug
    # (see tests/test_explain_resolve_parity.py). The v9 property itself is
    # unchanged and still asserted below: for every device actually evaluated,
    # the explained score IS the decided score.
    for device in resolver._candidates(intent, resolution):
        decided = resolver._relevance_score(device, intent, resolution)
        forced = (decided == 0.0 and urgency == Urgency.EMERGENCY
                  and device.emergency_capable)
        expected = resolver._FORCED_SCORE if forced else decided
        if expected > 0:
            assert device.device_id in explained, \
                f"{device.device_id} scored {expected} but explain omitted it"
            assert explained[device.device_id] == expected, \
                f"{device.device_id}: explain={explained[device.device_id]} decision={expected}"
        else:
            assert device.device_id not in explained

    # And the other direction, which is what actually broke: nothing may be
    # explained that was not evaluated.
    candidate_ids = {d.device_id for d in resolver._candidates(intent, resolution)}
    assert set(explained) <= candidate_ids, \
        f"explain reports {sorted(set(explained) - candidate_ids)}, never evaluated"


def test_breakdown_total_is_sum_of_components():
    """The .total is exactly its parts — no hidden arithmetic."""
    hub = _hub()
    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    resolution = hub.resolver._get_resolution(intent)
    for device in hub.registry.all():
        bd = hub.resolver._score_breakdown(device, intent, resolution)
        if not bd.hard_filtered:
            assert bd.total == (bd.tag_component + bd.location_component
                                + bd.emergency_component + bd.actuator_component)


def test_hard_filter_zeroes_regardless_of_bonuses():
    """An all-specific resolution with no tag overlap is OUT even if it would
    otherwise earn an emergency bonus (F3b)."""
    hub = DoSyncHub(db_path=":memory:")
    _reg(hub, "wrong-tag", ["basement"], ["turn_on"], emergency=True)
    # a resolution of only specific tags the device lacks
    intent = Intent(intent_id="t", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT, context={})
    resolution = {"tags": ["lock"], "actuators": ["unlock"]}
    bd = hub.resolver._score_breakdown(
        hub.registry.get("wrong-tag"), intent, resolution)
    assert bd.hard_filtered is True
    assert bd.total == 0.0


def test_weights_are_named_constants_not_magic_numbers():
    """v9 hygiene: the five weights live in one place, referenced by both paths."""
    r = CapabilityMatchingResolver
    assert r._W_TAG == 10.0
    assert r._W_LOCATION == 15.0
    assert r._W_EMERGENCY == 30.0
    assert r._W_ACTUATOR == 12.0
    assert r._FORCED_SCORE == 50.0


def test_scores_have_known_absolute_values():
    """Anchor the behavior to concrete numbers, not to self-consistency. If a
    weight changes, THIS fails — which is the regression guard the tautological
    'explain==decision' check cannot provide once both read one source.

    siren: tags {alarm,emergency} vs resolution — emergency intent, emergency_capable.
    """
    hub = DoSyncHub(db_path=":memory:")
    _reg(hub, "siren", ["alarm", "emergency"], ["alarm"], emergency=True)
    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    # resolution that overlaps one tag (alarm) and one actuator (alarm)
    resolution = {"tags": ["alarm"], "actuators": ["alarm"]}
    bd = hub.resolver._score_breakdown(hub.registry.get("siren"), intent, resolution)
    # 1 tag*10 + emergency 30 + 1 actuator*12 = 52
    assert bd.tag_component == 10.0
    assert bd.emergency_component == 30.0
    assert bd.actuator_component == 12.0
    assert bd.total == 52.0


def test_location_bonus_absolute():
    hub = DoSyncHub(db_path=":memory:")
    _reg(hub, "lamp-living", ["light", "living"], ["turn_on"])
    intent = Intent(intent_id="t", intent=IntentClass("notify"),
                    urgency=Urgency.INFO, context={"location": "living"})
    resolution = {"tags": ["light"], "actuators": []}
    bd = hub.resolver._score_breakdown(hub.registry.get("lamp-living"), intent, resolution)
    # 1 tag*10 + location 15 = 25
    assert bd.location_component == 15.0
    assert bd.total == 25.0


def test_exclusion_reason_matches_why_score_is_zero():
    hub = DoSyncHub(db_path=":memory:")
    _reg(hub, "no-overlap", ["basement"], ["turn_on"])
    intent = Intent(intent_id="t", intent=IntentClass("notify"),
                    urgency=Urgency.INFO, context={})
    resolution = {"tags": ["communication"], "actuators": ["notify"]}
    bd = hub.resolver._score_breakdown(
        hub.registry.get("no-overlap"), intent, resolution)
    assert bd.total == 0.0
    assert "declares none of the capabilities" in bd.exclusion_reason()


# ── Capability decides participation (2026-09-04) ─────────────────────────────

def test_a_device_declaring_the_needed_actuator_is_never_gated_out_by_tags():
    """The failure that started the redesign.

    A lock declaring `lock` and `unlock` was excluded from `control_access`
    because it lacked the tag `lock` — a tag the capability already implies.
    Its tags were `access` and `security`, which is what anyone would write for
    a lock.
    """
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                               Intent, IntentClass, Urgency)
    from dosync.resolvers import CapabilityMatchingResolver
    from dosync.hub import CapabilityRegistry

    reg = CapabilityRegistry()
    reg.register(CapabilityManifest(
        device_id="lock-01", device_name="Door", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["access", "security"],
        actuators=[ActuatorSpec(id="l", type="lock", description=""),
                   ActuatorSpec(id="u", type="unlock", description="")]))

    r = CapabilityMatchingResolver(reg)
    bd = r._score_breakdown(reg.get("lock-01"),
                            Intent(intent_id="t", intent=IntentClass("control_access"),
                                   urgency=Urgency.ALERT, context={}),
                            {"tags": ["lock"], "actuators": ["lock", "unlock"]})

    assert not bd.hard_filtered, (
        "a lock declaring lock and unlock was gated out of control_access for "
        "want of a tag: " + bd.exclusion_reason())


def test_a_device_declaring_nothing_the_intent_needs_is_gated_out():
    """The gate still gates. Capability deciding participation is not the same
    as everything participating."""
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                               Intent, IntentClass, Urgency)
    from dosync.resolvers import CapabilityMatchingResolver
    from dosync.hub import CapabilityRegistry

    reg = CapabilityRegistry()
    reg.register(CapabilityManifest(
        device_id="lamp-01", device_name="Lamp", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["lock", "security"],          # tags say lock; capability does not
        actuators=[ActuatorSpec(id="on", type="turn_on", description="")]))

    r = CapabilityMatchingResolver(reg)
    bd = r._score_breakdown(reg.get("lamp-01"),
                            Intent(intent_id="t", intent=IntentClass("control_access"),
                                   urgency=Urgency.ALERT, context={}),
                            {"tags": ["lock"], "actuators": ["lock", "unlock"]})

    assert bd.hard_filtered, (
        "a lamp tagged `lock` but declaring only turn_on took part in "
        "control_access — the tag decided, which is what this change removes")


def test_the_exclusion_reason_names_a_capability_not_a_tag():
    """An operator told "no tag overlap" would edit a field that no longer
    gates anything. The message must name what the device cannot do."""
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                               Intent, IntentClass, Urgency)
    from dosync.resolvers import CapabilityMatchingResolver
    from dosync.hub import CapabilityRegistry

    reg = CapabilityRegistry()
    reg.register(CapabilityManifest(
        device_id="lamp-02", device_name="Lamp", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["lock"],
        actuators=[ActuatorSpec(id="on", type="turn_on", description="")]))

    r = CapabilityMatchingResolver(reg)
    reason = r._score_breakdown(reg.get("lamp-02"),
                                Intent(intent_id="t", intent=IntentClass("control_access"),
                                       urgency=Urgency.ALERT, context={}),
                                {"tags": ["lock"], "actuators": ["lock"]}).exclusion_reason()

    assert "capabilities" in reason and "turn_on" in reason, (
        f"the exclusion reason does not say what the device declares: {reason}")
    assert "tag" not in reason.lower(), (
        f"the exclusion reason still blames tags: {reason}")
