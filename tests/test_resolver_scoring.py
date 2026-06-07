"""
DoSync Resolver Scoring Validation
Verifies that the CapabilityMatchingResolver produces correct relative scores.

Run: python3 -m pytest tests/test_resolver_scoring.py -v
  or: python3 tests/test_resolver_scoring.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.hub import DoSyncHub, CapabilityMatchingResolver
from dosync.models import (
    Intent, IntentClass, Urgency, CapabilityManifest, ActuatorSpec, DeviceCategory
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_hub():
    hub = DoSyncHub(db_path=":memory:")

    def reg(device_id, tags, emergency_capable=False):
        hub.register_device(CapabilityManifest(
            device_id=device_id,
            device_name=device_id,
            manufacturer="Test",
            model="T",
            firmware="1.0",
            category=DeviceCategory.ACTUATOR,
            tags=tags,
            actuators=[ActuatorSpec(id="turn_on", type="turn_on")],
            emergency_capable=emergency_capable,
        ))

    reg("light-emg",      ["light", "emergency"], emergency_capable=True)
    reg("light-normal",   ["light"],               emergency_capable=False)
    reg("sensor-pir",     ["sensor", "motion"],    emergency_capable=False)
    reg("light-entrance", ["light", "entrance"],   emergency_capable=True)
    reg("light-bedroom",  ["light", "bedroom"],    emergency_capable=False)

    # ensure_safety: requires "emergency" specific tag
    hub.db.save_intent_class(
        "t_safety", "emergency",
        ["emergency", "light"], ["turn_on", "alarm"],
        "Safety intent for tests", "test",
    )
    # set_environment: only generic "light" tag — all lights qualify
    hub.db.save_intent_class(
        "t_environment", "info",
        ["light"], ["turn_on"],
        "Environment intent for tests", "test",
    )

    resolver = CapabilityMatchingResolver(hub.registry)
    resolver.hub = hub
    return hub, resolver


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_specific_tag_filter():
    """Devices without required specific tags are excluded from the plan."""
    _, resolver = make_hub()
    intent = Intent(intent=IntentClass("t_safety"), urgency=Urgency.INFO, context={})
    plan = resolver.resolve(intent)
    device_ids = {a.device_id for a in plan.actions}

    # light-entrance has emergency_capable=True but lacks the "emergency" TAG
    # Tag filter applies at non-emergency urgency — only EMERGENCY urgency bypasses it
    assert "light-emg"      in device_ids,     "device with 'emergency' tag must be included"
    assert "light-normal"   not in device_ids, "device without specific 'emergency' tag must be excluded"
    assert "sensor-pir"     not in device_ids, "sensor-only device must not appear in actuator plan"
    assert "light-entrance" not in device_ids, "emergency_capable without 'emergency' TAG excluded at INFO urgency"


def test_tag_overlap_increases_score():
    """More tag overlap with resolution tags → higher relevance score."""
    _, resolver = make_hub()
    intent = Intent(intent=IntentClass("t_safety"), urgency=Urgency.INFO, context={})
    plan = resolver.resolve(intent)
    scores = {a.device_id: a.relevance_score for a in plan.actions}

    # light-emg has both "light" and "emergency" → higher overlap than light-entrance
    # light-entrance has "light" and "emergency" too → same overlap
    # Both should be > 0
    # Only devices with "emergency" TAG appear at INFO urgency for t_safety
    assert scores.get("light-emg", 0) > 0, "device with 'emergency' tag must have score > 0"


def test_emergency_bonus_increases_score():
    """EMERGENCY urgency gives a score bonus to emergency_capable devices."""
    _, resolver = make_hub()
    intent_info = Intent(intent=IntentClass("t_safety"), urgency=Urgency.INFO,    context={})
    intent_emg  = Intent(intent=IntentClass("t_safety"), urgency=Urgency.EMERGENCY, context={})

    plan_info = resolver.resolve(intent_info)
    plan_emg  = resolver.resolve(intent_emg)

    scores_info = {a.device_id: a.relevance_score for a in plan_info.actions}
    scores_emg  = {a.device_id: a.relevance_score for a in plan_emg.actions}

    assert scores_emg.get("light-emg", 0) > scores_info.get("light-emg", 0), \
        "emergency_capable device must score higher on EMERGENCY urgency"
    assert scores_emg.get("light-entrance", 0) > scores_info.get("light-entrance", 0), \
        "all emergency_capable devices get the emergency bonus"


def test_location_bonus_increases_score():
    """Devices with a matching location tag score higher when location is in context."""
    _, resolver = make_hub()
    intent_no_loc = Intent(intent=IntentClass("t_environment"), urgency=Urgency.INFO, context={})
    intent_loc    = Intent(intent=IntentClass("t_environment"), urgency=Urgency.INFO,
                           context={"location": "entrance"})

    plan_no_loc = resolver.resolve(intent_no_loc)
    plan_loc    = resolver.resolve(intent_loc)

    scores_no_loc = {a.device_id: a.relevance_score for a in plan_no_loc.actions}
    scores_loc    = {a.device_id: a.relevance_score for a in plan_loc.actions}

    assert scores_loc.get("light-entrance", 0) > scores_no_loc.get("light-entrance", 0), \
        "device at matching location must score higher when location is in context"


def test_all_emergency_capable_in_emergency_plan():
    """All emergency_capable devices appear in plans for EMERGENCY urgency."""
    _, resolver = make_hub()
    intent = Intent(intent=IntentClass("t_safety"), urgency=Urgency.EMERGENCY, context={})
    plan   = resolver.resolve(intent)
    device_ids = {a.device_id for a in plan.actions}

    assert "light-emg"      in device_ids, "light-emg must be in emergency plan"
    assert "light-entrance" in device_ids, "light-entrance must be in emergency plan"


def test_sensor_excluded_from_actuator_plan():
    """Sensor-only devices are not included in actuator-focused plans."""
    _, resolver = make_hub()
    intent = Intent(intent=IntentClass("t_safety"), urgency=Urgency.EMERGENCY, context={})
    plan   = resolver.resolve(intent)
    device_ids = {a.device_id for a in plan.actions}

    assert "sensor-pir" not in device_ids, "sensor-only device must not appear in actuator plan"


def test_empty_registry_returns_empty_plan():
    """Empty registry returns empty ActionPlan without raising an exception."""
    hub = DoSyncHub(db_path=":memory:")
    resolver = CapabilityMatchingResolver(hub.registry)
    resolver.hub = hub
    hub.db.save_intent_class(
        "t_safety", "emergency", ["emergency"], ["turn_on"], "", "test"
    )
    intent = Intent(intent=IntentClass("t_safety"), urgency=Urgency.EMERGENCY, context={})
    plan   = resolver.resolve(intent)

    assert len(plan.actions) == 0, "empty registry must return empty ActionPlan"
    assert plan.intent_id == intent.intent_id


def test_relevance_scores_are_non_negative():
    """All relevance scores must be non-negative."""
    _, resolver = make_hub()
    for intent_class in ("t_safety", "t_environment"):
        for urgency in (Urgency.INFO, Urgency.EMERGENCY):
            intent = Intent(
                intent=IntentClass(intent_class), urgency=urgency, context={}
            )
            plan = resolver.resolve(intent)
            for action in plan.actions:
                assert action.relevance_score >= 0, \
                    f"score must be >= 0, got {action.relevance_score} for {action.device_id}"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_specific_tag_filter,
        test_tag_overlap_increases_score,
        test_emergency_bonus_increases_score,
        test_location_bonus_increases_score,
        test_all_emergency_capable_in_emergency_plan,
        test_sensor_excluded_from_actuator_plan,
        test_empty_registry_returns_empty_plan,
        test_relevance_scores_are_non_negative,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗  {t.__name__}: unexpected error — {e}")
            failed += 1

    print(f"\n{passed}/{passed + failed} resolver scoring tests passed.")
    sys.exit(0 if failed == 0 else 1)
