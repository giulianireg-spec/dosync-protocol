"""
DoSync — Policy Engine
======================
Evaluates policies before intent execution.

A policy is a rule that can:
- ALLOW   — intent executes normally
- BLOCK   — intent is rejected with a reason
- CONFIRM — intent requires explicit confirmation before executing
- MODIFY  — intent parameters are adjusted before execution

Policies are evaluated in priority order. First matching policy wins.

Example policies:
    "never unlock doors after midnight"
    "critical actions require confirmation"
    "save_energy never turns off hallway lights"
    "children cannot trigger away_mode"

Usage:
    engine = PolicyEngine()
    engine.add(NeverAfterHoursPolicy("unlock", hour_start=0, hour_end=6))
    engine.add(RequireConfirmationPolicy(["lock", "unlock", "alarm"]))

    result = engine.evaluate(intent, action_plan)
    if result.decision == PolicyDecision.BLOCK:
        # reject the intent
    elif result.decision == PolicyDecision.CONFIRM:
        # wait for confirmation before executing
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Intent, ActionPlan, DeviceAction

log = logging.getLogger("dosync.policies")


# ── Policy decision ───────────────────────────────────────────────────────────

class PolicyDecision(str, Enum):
    ALLOW   = "allow"    # proceed normally
    BLOCK   = "block"    # reject the intent
    CONFIRM = "confirm"  # require explicit confirmation
    MODIFY  = "modify"   # adjust parameters before execution


@dataclass
class PolicyResult:
    decision:    PolicyDecision
    policy_name: str
    reason:      str = ""
    modified_actions: list = field(default_factory=list)

    @staticmethod
    def allow(policy_name: str) -> "PolicyResult":
        return PolicyResult(PolicyDecision.ALLOW, policy_name)

    @staticmethod
    def block(policy_name: str, reason: str) -> "PolicyResult":
        log.warning("Policy BLOCK [%s]: %s", policy_name, reason)
        return PolicyResult(PolicyDecision.BLOCK, policy_name, reason)

    @staticmethod
    def confirm(policy_name: str, reason: str) -> "PolicyResult":
        log.info("Policy CONFIRM [%s]: %s", policy_name, reason)
        return PolicyResult(PolicyDecision.CONFIRM, policy_name, reason)


# ── Base policy ───────────────────────────────────────────────────────────────

class BasePolicy(ABC):
    """
    Base class for all DoSync policies.

    To implement a custom policy:
        class MyPolicy(BasePolicy):
            def evaluate(self, intent, plan) -> PolicyResult:
                ...
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this policy."""
        ...

    @property
    def priority(self) -> int:
        """Lower number = evaluated first. Default 100."""
        return 100

    @abstractmethod
    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        """
        Evaluate this policy against the intent and action plan.
        Return None to abstain (policy does not apply to this intent).
        Return a PolicyResult to make a decision.
        """
        ...


# ── Built-in policies ─────────────────────────────────────────────────────────

class NeverAfterHoursPolicy(BasePolicy):
    """
    Blocks specific actuator types outside allowed hours.

    Example: never unlock doors between midnight and 6am.

    NeverAfterHoursPolicy(
        actuator_types=["unlock"],
        blocked_hours_start=0,
        blocked_hours_end=6,
        reason="Security policy: no remote unlocking between 00:00 and 06:00"
    )
    """

    def __init__(
        self,
        actuator_types: list[str],
        blocked_hours_start: int,
        blocked_hours_end: int,
        reason: str = "",
    ):
        self._actuator_types = set(actuator_types)
        self._start = blocked_hours_start
        self._end   = blocked_hours_end
        self._reason = reason or (
            f"Policy: {actuator_types} blocked between "
            f"{blocked_hours_start:02d}:00 and {blocked_hours_end:02d}:00"
        )

    @property
    def name(self) -> str:
        return "never_after_hours"

    @property
    def priority(self) -> int:
        return 10  # high priority

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .models import Urgency
        # Emergency always bypasses time restrictions
        if intent.urgency == Urgency.EMERGENCY:
            return None

        now = datetime.now()
        in_blocked_hours = self._start <= now.hour < self._end

        if not in_blocked_hours:
            return None  # outside blocked window — policy does not apply

        relevant = [a for a in plan.actions if a.action in self._actuator_types]
        if not relevant:
            return None  # no relevant actions

        return PolicyResult.block(
            self.name,
            f"{self._reason} (current time: {now.strftime('%H:%M')})"
        )


class RequireConfirmationPolicy(BasePolicy):
    """
    Requires explicit confirmation for specific actuator types.

    Example: always confirm before locking/unlocking doors.

    RequireConfirmationPolicy(
        actuator_types=["lock", "unlock", "alarm"],
        reason="Critical action requires confirmation"
    )
    """

    def __init__(self, actuator_types: list[str], reason: str = ""):
        self._actuator_types = set(actuator_types)
        self._reason = reason or f"Confirmation required for: {actuator_types}"

    @property
    def name(self) -> str:
        return "require_confirmation"

    @property
    def priority(self) -> int:
        return 20

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .models import Urgency
        # Emergency bypasses confirmation
        if intent.urgency == Urgency.EMERGENCY:
            return None

        relevant = [a for a in plan.actions if a.action in self._actuator_types]
        if not relevant:
            return None

        devices = [a.device_id for a in relevant]
        return PolicyResult.confirm(
            self.name,
            f"{self._reason} — affects: {', '.join(devices)}"
        )


class BlockIntentPolicy(BasePolicy):
    """
    Unconditionally blocks specific intents.

    Example: children cannot trigger away_mode.

    BlockIntentPolicy(
        intent_classes=["away_mode"],
        actor_tags=["child"],
        reason="Children cannot arm away mode"
    )
    """

    def __init__(
        self,
        intent_classes: list[str],
        reason: str = "",
        actor_tags: list[str] | None = None,
    ):
        self._intents = set(intent_classes)
        self._reason = reason or f"Intent blocked by policy: {intent_classes}"
        self._actor_tags = set(actor_tags) if actor_tags else None

    @property
    def name(self) -> str:
        return "block_intent"

    @property
    def priority(self) -> int:
        return 5  # highest priority

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        if intent.intent.value not in self._intents:
            return None

        if self._actor_tags:
            actor = intent.context.get("actor_tags", [])
            if not (self._actor_tags & set(actor)):
                return None  # actor doesn't match — policy doesn't apply

        return PolicyResult.block(self.name, self._reason)


class DeviceExclusionPolicy(BasePolicy):
    """
    Excludes specific devices from specific intents.

    Example: save_energy never turns off hallway lights.

    DeviceExclusionPolicy(
        intent_classes=["save_energy"],
        excluded_device_ids=["wiz-hallway-01"],
        reason="Hallway light stays on for safety"
    )
    """

    def __init__(
        self,
        intent_classes: list[str],
        excluded_device_ids: list[str],
        reason: str = "",
    ):
        self._intents  = set(intent_classes)
        self._excluded = set(excluded_device_ids)
        self._reason   = reason or f"Devices excluded by policy"

    @property
    def name(self) -> str:
        return "device_exclusion"

    @property
    def priority(self) -> int:
        return 30

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        if intent.intent.value not in self._intents:
            return None

        filtered = [a for a in plan.actions if a.device_id not in self._excluded]
        excluded = [a for a in plan.actions if a.device_id in self._excluded]

        if not excluded:
            return None  # no excluded devices in this plan

        log.info("DeviceExclusionPolicy: removed %d action(s) for %s",
                 len(excluded), [a.device_id for a in excluded])

        # MODIFY: return filtered plan
        result = PolicyResult(
            decision=PolicyDecision.MODIFY,
            policy_name=self.name,
            reason=self._reason,
            modified_actions=filtered,
        )
        return result


# ── Policy Engine ─────────────────────────────────────────────────────────────

class PolicyEngine:
    """
    Evaluates all registered policies against an intent and action plan.

    Policies are evaluated in priority order (lowest number first).
    First BLOCK or CONFIRM result wins.
    MODIFY policies are accumulated — all matching MODIFY policies apply.
    ALLOW is the default if no policy matches.

    Usage:
        engine = PolicyEngine()
        engine.add(NeverAfterHoursPolicy(["unlock"], 0, 6))
        engine.add(RequireConfirmationPolicy(["alarm"]))

        result = engine.evaluate(intent, plan)
    """

    def __init__(self):
        self._policies: list[BasePolicy] = []

    def add(self, policy: BasePolicy) -> None:
        """Register a policy."""
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.priority)
        log.info("Policy registered: %s (priority %d)", policy.name, policy.priority)

    def remove(self, policy_name: str) -> None:
        """Remove a policy by name."""
        self._policies = [p for p in self._policies if p.name != policy_name]

    def list_policies(self) -> list[dict]:
        """List all registered policies."""
        return [{"name": p.name, "priority": p.priority} for p in self._policies]

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult:
        """
        Evaluate all policies. Returns the first blocking/confirming result,
        or ALLOW if all policies pass. MODIFY policies are applied cumulatively.
        """
        from .models import Urgency

        # Emergency intents bypass all non-emergency policies
        if intent.urgency == Urgency.EMERGENCY:
            log.info("PolicyEngine: EMERGENCY intent — all policies bypassed")
            return PolicyResult.allow("emergency_bypass")

        current_plan = plan
        modify_applied = []

        for policy in self._policies:
            try:
                result = policy.evaluate(intent, current_plan)
            except Exception as e:
                log.error("Policy '%s' raised an exception: %s", policy.name, e)
                continue

            if result is None:
                continue  # policy abstained

            if result.decision == PolicyDecision.BLOCK:
                return result  # stop immediately

            if result.decision == PolicyDecision.CONFIRM:
                return result  # stop and request confirmation

            if result.decision == PolicyDecision.MODIFY:
                # Apply modification and continue evaluating
                from .models import ActionPlan
                current_plan = ActionPlan(
                    intent_id=plan.intent_id,
                    actions=result.modified_actions,
                    urgency=plan.urgency,
                )
                modify_applied.append(policy.name)

        if modify_applied:
            log.info("PolicyEngine: MODIFY applied by %s", modify_applied)
            return PolicyResult(
                decision=PolicyDecision.MODIFY,
                policy_name=", ".join(modify_applied),
                reason="Plan modified by policies",
                modified_actions=current_plan.actions,
            )

        return PolicyResult.allow("no_policy_matched")


# ── Intent priority map ───────────────────────────────────────────────────────

INTENT_PRIORITY: dict[str, int] = {
    # Priority 1 — Emergency (highest)
    "ensure_safety":         1,
    "alert_anomaly":         1,
    # Priority 2 — Security
    "control_access":        2,
    # Priority 3 — Presence
    "children_arrived_home": 3,
    "notify_family":         3,
    # Priority 4 — Comfort
    "set_environment":       4,
    "morning_routine":       4,
    "bedtime_routine":       4,
    "remind_chore":          4,
    "report_status":         4,
    # Priority 5 — Efficiency (lowest)
    "save_energy":           5,
    "away_mode":             5,
}

def get_intent_priority(intent_value: str) -> int:
    """Returns priority for an intent. Lower = higher priority. Default 99."""
    return INTENT_PRIORITY.get(intent_value, 99)


# ── Conflict resolution policy ────────────────────────────────────────────────

class ConflictResolutionPolicy(BasePolicy):
    """
    Detects and resolves conflicts between simultaneous intents.

    When two intents affect the same devices simultaneously, the one
    with higher priority (lower number) wins. The lower priority intent
    is blocked or has conflicting actions removed.

    Priority scale (lower = higher priority):
        1 = Emergency
        2 = Security
        3 = Presence
        4 = Comfort
        5 = Efficiency

    Example:
        children_arrived_home (priority 3) fires at the same time as
        save_energy (priority 5) — save_energy loses on shared devices.
    """

    def __init__(self, hub):
        self._hub = hub

    @property
    def name(self) -> str:
        return "conflict_resolution"

    @property
    def priority(self) -> int:
        return 1  # evaluated first

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        active = getattr(self._hub, "_active_intents", {})
        if not active:
            return None

        current_priority = get_intent_priority(intent.intent.value)
        current_devices = {a.device_id for a in plan.actions}

        for active_intent_value, active_priority in active.items():
            if active_intent_value == intent.intent.value:
                continue

            # Check if there are shared devices (conflict)
            active_devices = getattr(self._hub, "_active_intent_devices", {}).get(
                active_intent_value, set()
            )
            conflict_devices = current_devices & active_devices

            if not conflict_devices:
                continue

            # Conflict detected
            if current_priority > active_priority:
                # Current intent has LOWER priority — block conflicting actions
                filtered = [a for a in plan.actions if a.device_id not in conflict_devices]
                if not filtered:
                    return PolicyResult.block(
                        self.name,
                        f"Intent '{intent.intent.value}' (priority {current_priority}) blocked "
                        f"by '{active_intent_value}' (priority {active_priority}) "
                        f"on devices: {conflict_devices}"
                    )
                log.info(
                    "ConflictResolution: '%s' loses to '%s' on %d device(s)",
                    intent.intent.value, active_intent_value, len(conflict_devices)
                )
                result = PolicyResult(
                    decision=PolicyDecision.MODIFY,
                    policy_name=self.name,
                    reason=f"Conflict with higher-priority intent '{active_intent_value}'",
                    modified_actions=filtered,
                )
                return result

            elif current_priority < active_priority:
                # Current intent has HIGHER priority — it wins, log the override
                log.info(
                    "ConflictResolution: '%s' (priority %d) overrides '%s' (priority %d) "
                    "on devices: %s",
                    intent.intent.value, current_priority,
                    active_intent_value, active_priority,
                    conflict_devices,
                )
                # No modification needed — current intent executes fully
                return None

        return None
