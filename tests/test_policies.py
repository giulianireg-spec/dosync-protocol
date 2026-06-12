"""
DoSync Policy Engine Validation

Verifies the PolicyEngine and each built-in policy produce correct decisions:
ALLOW / BLOCK / CONFIRM / MODIFY, emergency bypass behaviour, priority ordering,
and the absolute-block guarantee for operator constraints.

Run: python3 -m pytest tests/test_policies.py -v
  or: python3 tests/test_policies.py
"""

import sys, os
from unittest import mock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.models import (
    Intent, IntentClass, Urgency, ActionPlan, DeviceAction,
)
from dosync.policies import (
    PolicyEngine, PolicyDecision, PolicyResult,
    NeverAfterHoursPolicy, RequireConfirmationPolicy, BlockIntentPolicy,
    DeviceExclusionPolicy, ConflictResolutionPolicy, ContextualWeightingPolicy,
    DeviceActuatorRateLimitPolicy, IntentRateLimitPolicy,
    get_intent_priority, INTENT_PRIORITY,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_intent(intent_name="notify", urgency=Urgency.INFO, context=None):
    return Intent(
        intent=IntentClass(intent_name),
        context=context or {},
        urgency=urgency,
    )

def make_plan(actions, urgency=Urgency.INFO):
    """actions: list of (device_id, action) tuples."""
    return ActionPlan(
        intent_id="test-plan",
        actions=[DeviceAction(device_id=d, action=a) for d, a in actions],
        urgency=urgency,
    )


# ── NeverAfterHoursPolicy ─────────────────────────────────────────────────────

def test_never_after_hours_blocks_inside_window():
    """unlock at 03:00 must be blocked when window is 00:00-06:00."""
    policy = NeverAfterHoursPolicy(["unlock"], 0, 6)
    intent = make_intent("control_access")
    plan = make_plan([("lock-front", "unlock")])

    fake_now = datetime(2026, 6, 11, 3, 0)  # 03:00
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is not None, "policy must fire inside blocked window"
    assert result.decision == PolicyDecision.BLOCK, "must BLOCK an unlock at 03:00"


def test_never_after_hours_allows_outside_window():
    """unlock at 15:00 must be allowed (policy abstains) when window is 00:00-06:00."""
    policy = NeverAfterHoursPolicy(["unlock"], 0, 6)
    intent = make_intent("control_access")
    plan = make_plan([("lock-front", "unlock")])

    fake_now = datetime(2026, 6, 11, 15, 0)  # 15:00
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is None, "policy must abstain outside the blocked window"


def test_never_after_hours_ignores_unrelated_actuators():
    """Inside the window, a turn_on action (not unlock) must not be blocked."""
    policy = NeverAfterHoursPolicy(["unlock"], 0, 6)
    intent = make_intent("set_environment")
    plan = make_plan([("light-1", "turn_on")])

    fake_now = datetime(2026, 6, 11, 3, 0)
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is None, "non-matching actuators must not trigger the block"


def test_never_after_hours_emergency_bypasses():
    """EMERGENCY urgency must bypass the time restriction even at 03:00."""
    policy = NeverAfterHoursPolicy(["unlock"], 0, 6)
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    plan = make_plan([("lock-front", "unlock")], urgency=Urgency.EMERGENCY)

    fake_now = datetime(2026, 6, 11, 3, 0)
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is None, "emergency must bypass time restriction"


# ── RequireConfirmationPolicy ─────────────────────────────────────────────────

def test_require_confirmation_fires_on_matching_actuator():
    policy = RequireConfirmationPolicy(["alarm"])
    intent = make_intent("alert_anomaly")
    plan = make_plan([("alarm-1", "alarm")])

    result = policy.evaluate(intent, plan)
    assert result is not None
    assert result.decision == PolicyDecision.CONFIRM, "alarm action must require confirmation"


def test_require_confirmation_abstains_without_match():
    policy = RequireConfirmationPolicy(["alarm"])
    intent = make_intent("set_environment")
    plan = make_plan([("light-1", "turn_on")])

    result = policy.evaluate(intent, plan)
    assert result is None, "no matching actuator → abstain"


def test_require_confirmation_emergency_bypasses():
    policy = RequireConfirmationPolicy(["alarm"])
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    plan = make_plan([("alarm-1", "alarm")], urgency=Urgency.EMERGENCY)

    result = policy.evaluate(intent, plan)
    assert result is None, "emergency must bypass confirmation requirement"


# ── BlockIntentPolicy ─────────────────────────────────────────────────────────

def test_block_intent_blocks_matching_intent():
    policy = BlockIntentPolicy(["away_mode"])
    intent = make_intent("away_mode")
    plan = make_plan([("lock-front", "lock")])

    result = policy.evaluate(intent, plan)
    assert result is not None
    assert result.decision == PolicyDecision.BLOCK


def test_block_intent_abstains_on_other_intent():
    policy = BlockIntentPolicy(["away_mode"])
    intent = make_intent("notify")
    plan = make_plan([("sms-1", "notify")])

    result = policy.evaluate(intent, plan)
    assert result is None, "non-matching intent → abstain"


def test_block_intent_actor_tag_filtering():
    """Block only when the actor matches the configured actor_tags."""
    policy = BlockIntentPolicy(["away_mode"], actor_tags=["child"])

    # Child actor → blocked
    intent_child = make_intent("away_mode", context={"actor_tags": ["child"]})
    assert policy.evaluate(intent_child, make_plan([("x", "lock")])).decision == PolicyDecision.BLOCK

    # Adult actor → abstain
    intent_adult = make_intent("away_mode", context={"actor_tags": ["adult"]})
    assert policy.evaluate(intent_adult, make_plan([("x", "lock")])) is None


def test_block_intent_is_absolute_even_in_emergency():
    """bypass_on_emergency must be False — operator blocks are absolute."""
    policy = BlockIntentPolicy(["away_mode"])
    assert policy.bypass_on_emergency is False, \
        "BlockIntentPolicy must NOT be bypassed by emergency"


# ── DeviceExclusionPolicy ─────────────────────────────────────────────────────

def test_device_exclusion_removes_excluded_device():
    policy = DeviceExclusionPolicy(["save_energy"], ["hallway-light"])
    intent = make_intent("save_energy")
    plan = make_plan([("hallway-light", "turn_off"), ("living-light", "turn_off")])

    result = policy.evaluate(intent, plan)
    assert result is not None
    assert result.decision == PolicyDecision.MODIFY
    device_ids = [a.device_id for a in result.modified_actions]
    assert "hallway-light" not in device_ids, "excluded device must be removed"
    assert "living-light" in device_ids, "non-excluded device must remain"


def test_device_exclusion_abstains_when_no_excluded_device_present():
    policy = DeviceExclusionPolicy(["save_energy"], ["hallway-light"])
    intent = make_intent("save_energy")
    plan = make_plan([("living-light", "turn_off")])

    result = policy.evaluate(intent, plan)
    assert result is None, "no excluded device in plan → abstain"


# ── PolicyEngine orchestration ────────────────────────────────────────────────

def test_engine_allow_when_no_policy_matches():
    engine = PolicyEngine()
    engine.add(BlockIntentPolicy(["away_mode"]))
    intent = make_intent("notify")
    plan = make_plan([("sms-1", "notify")])

    result = engine.evaluate(intent, plan)
    assert result.decision == PolicyDecision.ALLOW


def test_engine_first_block_wins():
    engine = PolicyEngine()
    engine.add(BlockIntentPolicy(["away_mode"]))
    engine.add(RequireConfirmationPolicy(["lock"]))
    intent = make_intent("away_mode")
    plan = make_plan([("lock-front", "lock")])

    result = engine.evaluate(intent, plan)
    assert result.decision == PolicyDecision.BLOCK, \
        "BlockIntentPolicy (priority 5) must win over confirmation (priority 20)"


def test_engine_emergency_bypasses_bypassable_policies():
    """Emergency bypasses RequireConfirmation but NOT BlockIntent."""
    engine = PolicyEngine()
    engine.add(RequireConfirmationPolicy(["alarm"]))
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    plan = make_plan([("alarm-1", "alarm")], urgency=Urgency.EMERGENCY)

    result = engine.evaluate(intent, plan)
    assert result.decision == PolicyDecision.ALLOW, \
        "emergency must bypass confirmation policy"


def test_engine_emergency_does_not_bypass_absolute_block():
    """A non-bypassable BlockIntentPolicy must still block an emergency."""
    engine = PolicyEngine()
    engine.add(BlockIntentPolicy(["ensure_safety"], reason="test absolute block"))
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    plan = make_plan([("alarm-1", "alarm")], urgency=Urgency.EMERGENCY)

    result = engine.evaluate(intent, plan)
    assert result.decision == PolicyDecision.BLOCK, \
        "absolute operator block must survive emergency"


def test_engine_modify_is_cumulative():
    """Two exclusion policies should both apply to the final plan."""
    engine = PolicyEngine()
    engine.add(DeviceExclusionPolicy(["save_energy"], ["device-a"]))
    engine.add(DeviceExclusionPolicy(["save_energy"], ["device-b"]))
    intent = make_intent("save_energy")
    plan = make_plan([("device-a", "turn_off"), ("device-b", "turn_off"), ("device-c", "turn_off")])

    result = engine.evaluate(intent, plan)
    assert result.decision == PolicyDecision.MODIFY
    device_ids = [a.device_id for a in result.modified_actions]
    assert device_ids == ["device-c"], \
        f"both exclusions must apply, leaving only device-c, got {device_ids}"


# ── Intent priority map ───────────────────────────────────────────────────────

def test_intent_priority_known_intents():
    assert get_intent_priority("ensure_safety") == 1
    assert get_intent_priority("control_access") == 2
    assert get_intent_priority("notify") == 3
    assert get_intent_priority("save_energy") == 5


def test_intent_priority_unknown_defaults_to_99():
    assert get_intent_priority("some_custom_intent") == 99, \
        "unknown/custom intents must default to priority 99"


def test_emergency_intents_outrank_efficiency():
    assert get_intent_priority("ensure_safety") < get_intent_priority("save_energy"), \
        "emergency must outrank efficiency (lower number = higher priority)"


# ── ConflictResolutionPolicy ──────────────────────────────────────────────────

class _FakeHub:
    """Minimal hub stub exposing the attributes ConflictResolutionPolicy reads."""
    def __init__(self, active_intents=None, active_devices=None):
        self._active_intents = active_intents or {}
        self._active_intent_devices = active_devices or {}


def test_conflict_abstains_when_no_active_intents():
    policy = ConflictResolutionPolicy(_FakeHub())
    intent = make_intent("save_energy")
    plan = make_plan([("light-1", "turn_off")])
    assert policy.evaluate(intent, plan) is None, "no active intents → abstain"


def test_conflict_abstains_when_no_shared_devices():
    hub = _FakeHub(
        active_intents={"ensure_safety": 1},
        active_devices={"ensure_safety": {"alarm-1"}},
    )
    policy = ConflictResolutionPolicy(hub)
    intent = make_intent("save_energy")
    plan = make_plan([("light-1", "turn_off")])  # no overlap with alarm-1
    assert policy.evaluate(intent, plan) is None, "no shared devices → no conflict"


def test_conflict_lower_priority_loses_shared_device():
    """save_energy (5) conflicts with ensure_safety (1) on a shared device → MODIFY."""
    hub = _FakeHub(
        active_intents={"ensure_safety": 1},
        active_devices={"ensure_safety": {"light-shared"}},
    )
    policy = ConflictResolutionPolicy(hub)
    intent = make_intent("save_energy")
    plan = make_plan([("light-shared", "turn_off"), ("light-other", "turn_off")])

    result = policy.evaluate(intent, plan)
    assert result is not None and result.decision == PolicyDecision.MODIFY
    ids = [a.device_id for a in result.modified_actions]
    assert "light-shared" not in ids, "shared device must be removed from loser"
    assert "light-other" in ids, "non-conflicting device must remain"


def test_conflict_lower_priority_fully_blocked():
    """If every device conflicts, the lower-priority intent is BLOCKED entirely."""
    hub = _FakeHub(
        active_intents={"ensure_safety": 1},
        active_devices={"ensure_safety": {"light-shared"}},
    )
    policy = ConflictResolutionPolicy(hub)
    intent = make_intent("save_energy")
    plan = make_plan([("light-shared", "turn_off")])  # only conflicting device

    result = policy.evaluate(intent, plan)
    assert result is not None and result.decision == PolicyDecision.BLOCK


def test_conflict_higher_priority_wins():
    """ensure_safety (1) vs active save_energy (5) on shared device → current wins, abstain."""
    hub = _FakeHub(
        active_intents={"save_energy": 5},
        active_devices={"save_energy": {"light-shared"}},
    )
    policy = ConflictResolutionPolicy(hub)
    intent = make_intent("ensure_safety")
    plan = make_plan([("light-shared", "turn_on")])

    result = policy.evaluate(intent, plan)
    assert result is None, "higher-priority intent wins and executes fully (abstain)"


# ── ContextualWeightingPolicy ─────────────────────────────────────────────────

def test_contextual_motion_at_night_amplifies():
    """motion at 02:00 → weight 1.8 → escalated flag set in context."""
    policy = ContextualWeightingPolicy()
    intent = make_intent("alert_anomaly", context={"trigger": "motion_detected"})
    plan = make_plan([("alarm-1", "alarm")])

    fake_now = datetime(2026, 6, 11, 2, 0)  # 02:00, a Thursday
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        policy.evaluate(intent, plan)

    assert intent.context.get("context_weight") == 1.8, "night motion must weight 1.8"
    assert intent.context.get("escalated") is True, "weight > 1.5 must set escalated flag"


def test_contextual_emergency_no_adjustment():
    """Emergency urgency must skip contextual weighting entirely."""
    policy = ContextualWeightingPolicy()
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY,
                         context={"trigger": "motion_detected"})
    plan = make_plan([("alarm-1", "alarm")], urgency=Urgency.EMERGENCY)

    fake_now = datetime(2026, 6, 11, 2, 0)
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is None
    assert "context_weight" not in intent.context, "emergency must not inject weight"


def test_contextual_work_hours_reduces_scope():
    """motion at 11:00 on a weekday → weight 0.6 → MODIFY reducing scope."""
    policy = ContextualWeightingPolicy()
    intent = make_intent("alert_anomaly", context={"trigger": "motion_detected"})
    plan = make_plan([("d1", "alarm"), ("d2", "alarm"), ("d3", "alarm"), ("d4", "alarm")])

    fake_now = datetime(2026, 6, 11, 11, 0)  # Thursday 11:00 = work hours
    with mock.patch("dosync.policies.datetime") as m:
        m.now.return_value = fake_now
        result = policy.evaluate(intent, plan)

    assert result is not None and result.decision == PolicyDecision.MODIFY, \
        "low weight (0.6) must reduce scope via MODIFY"
    assert len(result.modified_actions) < 4, "scope must be reduced"


# ── DeviceActuatorRateLimitPolicy ─────────────────────────────────────────────

def test_device_rate_limit_allows_within_limit():
    policy = DeviceActuatorRateLimitPolicy(limit_per_minute=3)
    intent = make_intent("notify")
    # Fire 3 times — all within limit
    for _ in range(3):
        result = policy.evaluate(intent, make_plan([("dev-1", "turn_on")]))
        assert result is None, "actions within limit must be allowed"


def test_device_rate_limit_blocks_when_all_throttled():
    policy = DeviceActuatorRateLimitPolicy(limit_per_minute=2)
    intent = make_intent("notify")
    # Exhaust the limit for dev-1
    policy.evaluate(intent, make_plan([("dev-1", "turn_on")]))
    policy.evaluate(intent, make_plan([("dev-1", "turn_on")]))
    # 3rd call — only dev-1 in plan, all throttled → BLOCK
    result = policy.evaluate(intent, make_plan([("dev-1", "turn_on")]))
    assert result is not None and result.decision == PolicyDecision.BLOCK, \
        "fully throttled plan must be blocked"


def test_device_rate_limit_partial_throttle_modifies():
    policy = DeviceActuatorRateLimitPolicy(limit_per_minute=1)
    intent = make_intent("notify")
    # Exhaust dev-1 only
    policy.evaluate(intent, make_plan([("dev-1", "turn_on")]))
    # Plan with throttled dev-1 + fresh dev-2 → MODIFY keeping dev-2
    result = policy.evaluate(intent, make_plan([("dev-1", "turn_on"), ("dev-2", "turn_on")]))
    assert result is not None and result.decision == PolicyDecision.MODIFY
    ids = [a.device_id for a in result.modified_actions]
    assert ids == ["dev-2"], f"only non-throttled device must remain, got {ids}"


def test_device_rate_limit_emergency_never_limited():
    policy = DeviceActuatorRateLimitPolicy(limit_per_minute=1)
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    # Fire well over the limit — emergency must never be throttled
    for _ in range(5):
        result = policy.evaluate(intent, make_plan([("dev-1", "alarm")], urgency=Urgency.EMERGENCY))
        assert result is None, "emergency must never be rate limited (protocol guarantee)"


# ── IntentRateLimitPolicy ─────────────────────────────────────────────────────

def test_intent_rate_limit_allows_within_limit():
    policy = IntentRateLimitPolicy(limits_per_minute={"info": 3})
    intent = make_intent("notify", urgency=Urgency.INFO)
    for _ in range(3):
        result = policy.evaluate(intent, make_plan([("d", "notify")]))
        assert result is None, "intents within limit must be allowed"


def test_intent_rate_limit_blocks_over_limit():
    policy = IntentRateLimitPolicy(limits_per_minute={"info": 2})
    intent = make_intent("notify", urgency=Urgency.INFO)
    policy.evaluate(intent, make_plan([("d", "notify")]))
    policy.evaluate(intent, make_plan([("d", "notify")]))
    # 3rd intent exceeds the limit → BLOCK
    result = policy.evaluate(intent, make_plan([("d", "notify")]))
    assert result is not None and result.decision == PolicyDecision.BLOCK, \
        "intent over limit must be blocked"


def test_intent_rate_limit_emergency_never_limited():
    policy = IntentRateLimitPolicy(limits_per_minute={"info": 1})
    intent = make_intent("ensure_safety", urgency=Urgency.EMERGENCY)
    for _ in range(5):
        result = policy.evaluate(intent, make_plan([("d", "alarm")], urgency=Urgency.EMERGENCY))
        assert result is None, "emergency intents must never be rate limited"


def test_intent_rate_limit_separate_windows_per_source():
    """Two different sources have independent rate limit windows."""
    policy = IntentRateLimitPolicy(limits_per_minute={"info": 1})

    intent_a = Intent(intent=IntentClass("notify"), context={}, urgency=Urgency.INFO, source="mcp")
    intent_b = Intent(intent=IntentClass("notify"), context={}, urgency=Urgency.INFO, source="api")

    # Exhaust source "mcp"
    assert policy.evaluate(intent_a, make_plan([("d", "notify")])) is None
    blocked = policy.evaluate(intent_a, make_plan([("d", "notify")]))
    assert blocked is not None and blocked.decision == PolicyDecision.BLOCK, "mcp over limit blocked"

    # Source "api" still has its own fresh window
    assert policy.evaluate(intent_b, make_plan([("d", "notify")])) is None, \
        "different source must have independent window"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} policy tests passed.")
    sys.exit(1 if failed else 0)
