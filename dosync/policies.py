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
import threading
from collections import deque
import time
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

    @property
    def bypass_on_emergency(self) -> bool:
        """Whether EMERGENCY urgency bypasses this policy.

        Default: True — most safety policies should be bypassed for emergencies
        (time restrictions, confirmation requirements, device exclusions).

        Set to False for policies that represent absolute operator constraints
        that must be honored even in emergencies (e.g. BlockIntentPolicy when
        an operator has explicitly prohibited an intent class).

        Note: IntentRateLimitPolicy and DeviceActuatorRateLimitPolicy handle
        emergency bypass internally and do not rely on this flag.
        """
        return True

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

    bypass_on_emergency=False: operator blocks are absolute — not bypassed
    even by EMERGENCY urgency. If an operator has explicitly prohibited
    an intent class, that prohibition is honored regardless of urgency.

    Example: children cannot trigger away_mode.

    BlockIntentPolicy(
        intent_classes=["away_mode"],
        actor_tags=["child"],
        reason="Children cannot arm away mode"
    )
    """

    @property
    def bypass_on_emergency(self) -> bool:
        return False  # Operator blocks are absolute

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
        bypass_on_emergency: bool = True,
    ):
        """
        Args:
            bypass_on_emergency: whether EMERGENCY urgency ignores this exclusion.

                True (default, and the historical behavior) suits an exclusion
                that expresses convenience: in a fire, use everything that helps.

                False makes the exclusion ABSOLUTE. That is a real deployment
                need, not an edge case: an operator may exclude a device because
                involving it is USELESS or HARMFUL, and an emergency does not
                change that — screens that nobody will read while evacuating, or
                a ward where lights must never come on because of a
                photosensitive patient. Whether an exclusion is advisory or
                absolute is the DEPLOYER's judgement about their building, which
                is precisely the kind of decision the protocol must not make for
                them (2026-07-12 panel). Added 2026-07-14, when the first
                operator ground truth ("screens must not act in an emergency")
                turned out to be inexpressible.
        """
        self._intents  = set(intent_classes)
        self._excluded = set(excluded_device_ids)
        self._reason   = reason or f"Devices excluded by policy"
        self._bypass_on_emergency = bypass_on_emergency

    @property
    def name(self) -> str:
        return "device_exclusion"

    @property
    def priority(self) -> int:
        return 30

    @property
    def bypass_on_emergency(self) -> bool:
        return self._bypass_on_emergency

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



# ── Device actuator rate limit policy ─────────────────────────────────────────

class DeviceActuatorRateLimitPolicy(BasePolicy):
    """
    Limits how many times a specific device can be targeted per minute.

    Complements IntentRateLimitPolicy (which limits by intent source).
    This policy limits by target device, preventing a single device
    from being flooded with commands regardless of how many agents
    or intents trigger it.

    When the limit is exceeded for a device, that device's action is
    removed from the ActionPlan (MODIFY). Other devices in the plan
    are not affected.

    Emergency intents are NEVER rate limited — protocol guarantee.

    Default limit: 20 actions per minute per device.

    Usage:
        policy_engine.add(DeviceActuatorRateLimitPolicy())

        # Custom limit
        policy_engine.add(DeviceActuatorRateLimitPolicy(limit_per_minute=10))
    """

    DEFAULT_LIMIT = 20  # actions per minute per device

    def __init__(
        self,
        limit_per_minute: int | None = None,
        window_seconds: int = 60,
        db=None,
    ):
        self._limit  = limit_per_minute if limit_per_minute is not None else self.DEFAULT_LIMIT
        self._window = window_seconds
        self._db     = db          # DoSyncDB instance — if set, events are persisted
        # Sliding window: {device_id: deque of timestamps}
        self._windows: dict[str, deque] = {}
        self._lock = threading.Lock()
        # Restore windows from DB on startup if DB is available
        if self._db is not None:
            self._restore_from_db()

    def set_db(self, db) -> None:
        """Wire DB after construction (called from hub startup)."""
        self._db = db
        self._restore_from_db()

    def _restore_from_db(self) -> None:
        """Load rate limit events from DB on startup. Only loads events within current window."""
        try:
            events = self._db.load_rate_limit_events(self._window)
            with self._lock:
                for device_id, timestamps in events.items():
                    self._windows[device_id] = deque(sorted(timestamps))
            log.info(
                "DeviceActuatorRateLimitPolicy: restored %d device windows from DB",
                len(events),
            )
        except Exception as exc:
            log.warning("DeviceActuatorRateLimitPolicy: could not restore from DB: %s", exc)

    @property
    def name(self) -> str:
        return "device_actuator_rate_limit"

    @property
    def priority(self) -> int:
        return 5  # runs after IntentRateLimitPolicy (0) but before other policies

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .models import Urgency

        # Emergency intents are NEVER rate limited — protocol guarantee
        if intent.urgency == Urgency.EMERGENCY:
            return None

        if not plan.actions:
            return None

        now    = time.time()
        cutoff = now - self._window
        throttled: list[str] = []
        allowed_actions = []

        with self._lock:
            for action in plan.actions:
                device_id = action.device_id

                if device_id not in self._windows:
                    self._windows[device_id] = deque()
                window = self._windows[device_id]

                # Evict expired timestamps
                while window and window[0] < cutoff:
                    window.popleft()

                if len(window) >= self._limit:
                    throttled.append(device_id)
                else:
                    window.append(now)
                    allowed_actions.append(action)
                    # Persist to DB (best-effort — never block execution)
                    if self._db is not None:
                        try:
                            self._db.append_rate_limit_event(device_id, now)
                        except Exception:
                            pass

        if not throttled:
            return None  # all devices within limit

        log.info(
            "DeviceActuatorRateLimitPolicy: throttled %d device(s): %s",
            len(throttled), throttled,
        )

        if not allowed_actions:
            # Every device in the plan is throttled — block the entire intent
            return PolicyResult.block(
                self.name,
                f"All {len(throttled)} device(s) in plan are rate limited "
                f"({self._limit} actions/{self._window}s).",
            )

        # Partial throttle — remove over-limit devices, execute the rest
        return PolicyResult(
            decision=PolicyDecision.MODIFY,
            policy_name=self.name,
            reason=f"Throttled {len(throttled)} device(s): {throttled}",
            modified_actions=allowed_actions,
        )

    def get_stats(self) -> dict:
        """Return current action counts per device for monitoring."""
        now    = time.time()
        cutoff = now - self._window
        stats  = {}
        with self._lock:
            for device_id, window in self._windows.items():
                active = sum(1 for t in window if t >= cutoff)
                stats[device_id] = {
                    "count":     active,
                    "limit":     self._limit,
                    "remaining": max(0, self._limit - active),
                }
        return stats


# ── Policy Engine ─────────────────────────────────────────────────────────────

class IntentRateLimitPolicy(BasePolicy):
    """
    Limits intent execution frequency per source using a sliding window counter.

    This policy is a REQUIRED component of any DoSync-compliant deployment.
    It protects the hub against runaway AI agents, malfunctioning automations,
    and denial-of-service conditions.

    Emergency intents are NEVER rate limited — this is a protocol-level guarantee.
    All other urgency levels are limited independently per source.

    Default limits (configurable):
        info:    60 intents / minute per source
        warning: 60 intents / minute per source
        alert:   20 intents / minute per source

    Usage:
        policy_engine.add(IntentRateLimitPolicy())

        # Custom limits
        policy_engine.add(IntentRateLimitPolicy(
            limits_per_minute={"info": 30, "warning": 30, "alert": 10}
        ))

    The response follows HTTP 429 semantics: BLOCK with reason including
    the current count, the limit, and the seconds until the window resets.
    Every blocked intent is logged in the tamper-evident audit trail.
    """

    # Protocol-defined minimum default limits.
    # A compliant hub MUST enforce at least these limits.
    DEFAULT_LIMITS: dict[str, int] = {
        "info":    60,
        "warning": 60,
        "alert":   20,
        # "emergency" is intentionally absent — always bypassed
    }

    def __init__(
        self,
        limits_per_minute: dict[str, int] | None = None,
        window_seconds: int = 60,
    ):
        self._limits = limits_per_minute if limits_per_minute is not None else self.DEFAULT_LIMITS
        self._window = window_seconds
        # Sliding window: {source: {urgency_value: deque of timestamps}}
        self._windows: dict[str, dict[str, deque]] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "intent_rate_limit"

    @property
    def priority(self) -> int:
        return 0  # FIRST line of defense — runs before all other policies

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .models import Urgency

        # Emergency intents are NEVER rate limited — protocol guarantee
        if intent.urgency == Urgency.EMERGENCY:
            return None

        urgency_value = str(intent.urgency.value)
        limit = self._limits.get(urgency_value)
        if limit is None:
            return None  # no limit configured for this urgency level

        source = getattr(intent, "source", None) or "unknown"
        now = time.time()
        cutoff = now - self._window

        with self._lock:
            # Initialize per-source, per-urgency sliding window
            if source not in self._windows:
                self._windows[source] = {}
            if urgency_value not in self._windows[source]:
                self._windows[source][urgency_value] = deque()

            window = self._windows[source][urgency_value]

            # Evict timestamps outside the sliding window
            while window and window[0] < cutoff:
                window.popleft()

            current_count = len(window)

            if current_count >= limit:
                # Calculate retry-after: seconds until oldest entry leaves the window
                retry_after = max(1, int(self._window - (now - window[0])) + 1)
                return PolicyResult.block(
                    self.name,
                    f"Rate limit exceeded for source '{source}': "
                    f"{current_count}/{limit} {urgency_value} intents "
                    f"in the last {self._window}s. "
                    f"Retry after {retry_after}s."
                )

            # Record this intent execution
            window.append(now)
            return None  # within limit — allow

    def get_stats(self) -> dict:
        """Return current rate limit counters for all sources. Useful for monitoring."""
        now = time.time()
        cutoff = now - self._window
        stats = {}
        with self._lock:
            for source, urgency_windows in self._windows.items():
                stats[source] = {}
                for urgency, window in urgency_windows.items():
                    # Count active entries
                    active = sum(1 for t in window if t >= cutoff)
                    limit = self._limits.get(urgency, 0)
                    stats[source][urgency] = {
                        "count": active,
                        "limit": limit,
                        "remaining": max(0, limit - active),
                    }
        return stats


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

        # Emergency intents bypass policies that declare bypass_on_emergency=True.
        # Policies with bypass_on_emergency=False are still evaluated
        # (e.g. BlockIntentPolicy — operator blocks are absolute).
        if intent.urgency == Urgency.EMERGENCY:
            non_bypassable = [p for p in self._policies if not p.bypass_on_emergency]
            emergency_plan = plan
            emergency_modified: list[str] = []
            emergency_reasons: list[str] = []
            if non_bypassable:
                for policy in non_bypassable:
                    try:
                        result = policy.evaluate(intent, emergency_plan)
                    except Exception as e:
                        log.error("Policy '%s' raised an exception: %s", policy.name, e)
                        continue
                    if result is None:
                        continue
                    if result.decision == PolicyDecision.BLOCK:
                        log.info(
                            "PolicyEngine: EMERGENCY intent blocked by non-bypassable policy '%s': %s",
                            policy.name, result.reason,
                        )
                        return result
                    if result.decision == PolicyDecision.MODIFY:
                        # A non-bypassable policy's decision is honored WHATEVER it
                        # is — not only BLOCK. Until 2026-07-14 this branch
                        # evaluated the policy, received its MODIFY, and silently
                        # discarded it on the way to allow("emergency_bypass"): the
                        # deployer declared an absolute restriction, the engine
                        # asked the policy, got the answer, and threw it away.
                        # Nobody noticed because every MODIFY policy in the tree was
                        # bypassable, so the case never arose until an operator
                        # needed "these devices must not act, not even in an
                        # emergency" (a ward where lights must never come on, a
                        # screen nobody will read while evacuating).
                        from .models import ActionPlan
                        emergency_plan = ActionPlan(
                            intent_id=plan.intent_id,
                            actions=result.modified_actions,
                            urgency=plan.urgency,
                        )
                        emergency_modified.append(policy.name)
                        emergency_reasons.append(f"{policy.name}: {result.reason}")
            log.info(
                "PolicyEngine: EMERGENCY intent — %d/%d policies bypassed",
                len(self._policies) - len(non_bypassable), len(self._policies),
            )
            if emergency_modified:
                log.info("PolicyEngine: EMERGENCY plan modified by non-bypassable %s",
                         emergency_modified)
                return PolicyResult(
                    decision=PolicyDecision.MODIFY,
                    policy_name=", ".join(emergency_modified),
                    # Carry each policy's OWN declared reason: for audit
                    # provenance, "why was this removed" is the operator's
                    # words, not this engine's generic phrasing.
                    reason="; ".join(emergency_reasons)
                           or "Plan modified by non-bypassable policies",
                    modified_actions=emergency_plan.actions,
                )
            return PolicyResult.allow("emergency_bypass")

        current_plan = plan
        modify_applied = []
        modify_reasons: list[str] = []

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
                modify_reasons.append(f"{policy.name}: {result.reason}")

        if modify_applied:
            log.info("PolicyEngine: MODIFY applied by %s", modify_applied)
            return PolicyResult(
                decision=PolicyDecision.MODIFY,
                policy_name=", ".join(modify_applied),
                reason="; ".join(modify_reasons) or "Plan modified by policies",
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
    # Priority 3 — Notification
    "notify":                3,
    # Domain-specific intents (e.g. children_arrived_home) fall through to default 99
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
        notify (priority 3) fires at the same time as
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


# ── Contextual weighting policy ───────────────────────────────────────────────

class ContextualWeightingPolicy(BasePolicy):
    """
    Adjusts intent context based on temporal and environmental factors.

    The same physical event carries different weight depending on context:
    - A motion sensor at 3am is more likely an intrusion than at 3pm
    - A temperature anomaly in winter has different implications than in summer
    - Monday morning routines differ from weekend patterns

    This policy injects a 'context_weight' into the intent context,
    which the resolver can use to adjust scoring.

    Weight scale:
        1.0 = normal weight (default)
        > 1.0 = amplify response (e.g. motion at night = higher urgency)
        < 1.0 = reduce response (e.g. motion during typical home hours = routine)

    Built-in rules:
        - motion_detected at night (22:00-06:00) → weight 1.8 (possible intrusion)
        - motion_detected during work hours (09:00-17:00) weekday → weight 0.6 (likely routine)
        - any intent on weekend → weight 0.9 (relaxed mode)
        - temperature anomaly in extreme weather hours → weight 1.5
    """

    def __init__(self, custom_rules: list[dict] | None = None):
        self._custom_rules = custom_rules or []

    @property
    def name(self) -> str:
        return "contextual_weighting"

    @property
    def priority(self) -> int:
        return 2  # evaluated very early, before conflict resolution

    def _compute_weight(self, intent: "Intent") -> float:
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()  # 0=Monday, 6=Sunday
        is_night = hour >= 22 or hour < 6
        is_work_hours = 9 <= hour < 17 and weekday < 5
        is_weekend = weekday >= 5
        intent_value = intent.intent.value
        trigger = intent.context.get("trigger", "")

        weight = 1.0

        # Motion at night — possible intrusion
        if trigger == "motion_detected" and is_night:
            weight = 1.8

        # Motion during typical work hours on weekday — likely routine
        elif trigger == "motion_detected" and is_work_hours:
            weight = 0.6

        # Weekend — relaxed mode
        if is_weekend and intent_value not in ("ensure_safety", "alert_anomaly"):
            weight *= 0.9

        # Temperature anomaly at extreme hours
        if trigger == "temperature_anomaly" and is_night:
            weight = max(weight, 1.5)

        # Apply custom rules
        for rule in self._custom_rules:
            if rule.get("intent") == intent_value:
                if rule.get("hour_start") is not None and rule.get("hour_end") is not None:
                    if rule["hour_start"] <= hour < rule["hour_end"]:
                        weight = rule.get("weight", weight)

        return round(weight, 2)

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .models import Urgency
        # Emergency always full weight
        if intent.urgency == Urgency.EMERGENCY:
            return None

        weight = self._compute_weight(intent)

        if weight == 1.0:
            return None  # no adjustment needed

        # Inject weight into intent context for resolver awareness
        intent.context["context_weight"] = weight

        if weight < 0.7:
            # Very low weight — reduce scope by keeping only high-scoring devices
            log.info(
                "ContextualWeighting: low weight %.2f for '%s' — reducing scope",
                weight, intent.intent.value
            )
            high_priority_actions = plan.actions[:max(1, len(plan.actions) // 2)]
            return PolicyResult(
                decision=PolicyDecision.MODIFY,
                policy_name=self.name,
                reason=f"Low contextual weight ({weight}) — reduced scope",
                modified_actions=high_priority_actions,
            )

        if weight > 1.5:
            # High weight — escalate urgency in context
            log.info(
                "ContextualWeighting: high weight %.2f for '%s' — escalating context",
                weight, intent.intent.value
            )
            intent.context["escalated"] = True
            intent.context["original_urgency"] = intent.urgency.value

        log.info(
            "ContextualWeighting: weight=%.2f applied to '%s' (hour=%d, trigger=%s)",
            weight, intent.intent.value,
            __import__("datetime").datetime.now().hour,
            intent.context.get("trigger", "none")
        )
        return None  # let intent proceed with modified context


# ── Aerial domain policies (drone safety) ─────────────────────────────────────
# These two policies are the ones the expert panel identified as the essential
# barriers for a vehicle that moves through physical space. They are deliberately
# ABSOLUTE: both set bypass_on_emergency = False. A geofence protects something
# real (an airport, a populated area, restricted airspace); a DoSync "emergency"
# is never a reason to fly the vehicle there. And once a human has taken manual
# control, an emergency must NOT wrestle it back — the takeover is very likely the
# response to that emergency.
#
# CRITICAL BOUNDARY: these policies filter INTENT before DoSync dispatches a MAVLink
# command. They are NOT the flight safety system. The failsafe (lost link, low
# battery, GPS loss) lives in the vehicle firmware and acts on its own, even if
# DoSync does not exist. DoSync is a policy layer above the firmware, never the
# firmware's replacement. The firmware also has its own geofence; this complements
# it, it does not substitute for it.


class GeofencePolicy(BasePolicy):
    """Blocks any go_to whose target lies outside the permitted perimeter.

    The perimeter is a horizontal circle (center + max radius) plus a maximum
    altitude ceiling — the standard, easy-to-reason-about geofence that covers the
    overwhelming majority of cases. Each deployment configures its own perimeter
    via the constructor, exactly like NeverAfterHoursPolicy configures its hours.
    The protocol stays generic; the perimeter is deployment-specific.

    Only go_to actions carry a destination, so only they are checked. take_off,
    land, return_home and loiter do not move the vehicle to an arbitrary point and
    are not constrained here (return_home in particular must always be allowed —
    it is how the vehicle comes back).

    This is an ABSOLUTE limit: bypass_on_emergency is False. The perimeter exists
    because crossing it is dangerous or illegal, and no urgency changes that.

    DESIGN NOTE — agnosticism and the deliberate coupling to `go_to`:
    The PolicyEngine itself stays fully domain-agnostic; this is just one more
    pluggable BasePolicy, registered only by deployments that fly something, like
    NeverAfterHoursPolicy is registered only where time restrictions matter. It is
    agnostic to the drone make/model (it reasons over lat/lon/alt, which are
    universal), but it is inherently SPATIAL — an oven or a light has no position,
    so a geofence is meaningless for them. That is correct and expected: generic
    mechanism, domain-specific policy.
    However, this policy is currently coupled to the MAVLink adapter's vocabulary:
    it checks the literal action name "go_to" and the params "lat"/"lon"/"alt".
    That coupling is DELIBERATE for now. DoSync has exactly one spatial adapter
    (the drone). Abstracting "an action with a spatial destination" into a protocol-
    level concept that any spatial adapter (a rover, a boat, a 3D robotic arm) would
    declare is the right move ONLY once a SECOND spatial device exists to inform the
    abstraction — designing it from a single example risks the wrong abstraction
    (the same "don't abstract on one example" discipline applied to the
    execution_model vocabulary). When a second spatial adapter arrives, extract a
    declared "spatial destination" capability and have this policy operate on that
    instead of the hardcoded "go_to". Until then, the explicit coupling is the
    honest, simplest correct choice.
    """

    def __init__(self, center_lat: float, center_lon: float,
                 max_radius_m: float, max_altitude_m: float = None,
                 applies_to_devices: list = None):
        """
        Args:
            center_lat, center_lon: center of the permitted circle (degrees).
            max_radius_m: maximum allowed distance from center, in meters.
            max_altitude_m: optional altitude ceiling, in meters. None = no ceiling.
            applies_to_devices: optional list of device_ids this geofence governs.
                None = applies to every go_to in the plan (single-perimeter deploy).
        """
        self._center_lat = center_lat
        self._center_lon = center_lon
        self._max_radius_m = max_radius_m
        self._max_altitude_m = max_altitude_m
        self._devices = set(applies_to_devices) if applies_to_devices else None

    @property
    def name(self) -> str:
        return "geofence"

    @property
    def priority(self) -> int:
        # Evaluate early — a geofence breach should be caught before lower-priority
        # conveniences even look at the plan.
        return 10

    @property
    def bypass_on_emergency(self) -> bool:
        # ABSOLUTE. An emergency never licenses crossing the perimeter.
        return False

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance, delegated to the shared geo module so the formula
        lives in exactly one place (see dosync/geo.py). Kept as a thin wrapper for
        backward compatibility with any code/tests referencing it."""
        from .geo import haversine_m
        return haversine_m(lat1, lon1, lat2, lon2)

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        from .geo import is_within_perimeter
        for action in plan.actions:
            if action.action != "go_to":
                continue
            if self._devices is not None and action.device_id not in self._devices:
                continue

            params = action.params or {}
            lat = params.get("lat")
            lon = params.get("lon")
            if lat is None or lon is None:
                # A go_to without coordinates is malformed; let the adapter reject
                # it. The geofence only judges destinations it can locate.
                continue

            # Same shared rule the in-flight guard uses — admission vs monitoring.
            ok, reason = is_within_perimeter(
                lat, lon, self._center_lat, self._center_lon,
                self._max_radius_m,
                alt=params.get("alt"), max_altitude_m=self._max_altitude_m,
            )
            if not ok:
                return PolicyResult.block(self.name, f"go_to target {reason}")

        return None  # every go_to in the plan is within the perimeter


class ManualControlActivePolicy(BasePolicy):
    """Blocks commands to a vehicle whose active operation is `interrupted` — i.e.
    a human has taken manual control.

    This is the policy-level counterpart to the MANUAL_CONTROL_TAKEN telemetry
    event. Once the pilot has the sticks, DoSync must not try to re-dispatch the
    original (or any) command to that vehicle, or it would fight the human for
    control. The pilot took over for a reason DoSync cannot see — most likely to
    avoid something. DoSync goes quiet until a human explicitly returns control
    (by clearing/finishing the interrupted operation out of band).

    Consults the hub for the vehicle's active operations. An operation in the
    INTERRUPTED state means a human is flying; any command in the plan targeting
    that device is blocked.

    ABSOLUTE: bypass_on_emergency is False. An emergency is very likely the reason
    the human took control — it must never be used to wrestle control back.
    """

    INTERRUPTED_STATE = "interrupted"

    def __init__(self, hub):
        self._hub = hub

    @property
    def name(self) -> str:
        return "manual_control_active"

    @property
    def priority(self) -> int:
        # Evaluate very early — if a human is flying, nothing else about the plan
        # for that device matters.
        return 5

    @property
    def bypass_on_emergency(self) -> bool:
        # ABSOLUTE. An emergency never wrestles control back from a human pilot.
        return False

    def _devices_under_manual_control(self) -> set:
        """Return the set of device_ids whose active operation is interrupted."""
        try:
            active = self._hub.db.get_active_operations()
        except Exception:
            return set()
        return {
            o.get("device_id") for o in active
            if o.get("state") == self.INTERRUPTED_STATE and o.get("device_id")
        }

    def evaluate(self, intent: "Intent", plan: "ActionPlan") -> PolicyResult | None:
        manual = self._devices_under_manual_control()
        if not manual:
            return None

        blocked = [a for a in plan.actions if a.device_id in manual]
        if not blocked:
            return None

        devices = sorted({a.device_id for a in blocked})
        return PolicyResult.block(
            self.name,
            f"Device(s) {devices} are under manual human control "
            f"(operation interrupted). DoSync will not dispatch commands until a "
            f"human returns control.",
        )
