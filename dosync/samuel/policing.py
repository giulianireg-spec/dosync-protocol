"""The operator's policies, combined (SAMUEL G7, rule R11).

What a policy IS -- a curfew, a geofence, a device under manual control -- is
the deployment's business and lives outside the kernel (dosync/policies.py,
policy files). What happens when several of them speak about one plan is the
standard's, and lives here:

  - Policies are asked in priority order (lowest number first).
  - The first BLOCK, or the first CONFIRM (a legacy whole-plan hold), decides.
  - MODIFY accumulates: each policy sees the plan the previous ones left.
  - ALLOW when no policy objects.
  - In an EMERGENCY only policies that do not bypass emergencies are asked
    (an operator's absolute restriction), and their MODIFY is honoured too.

And one rule that was missing until 2026-10-12 (H7): a policy that FAILS -- it
raises instead of answering -- used to be skipped, so an operator's restriction
with a bug let the plan through. A restriction that could not be evaluated is
not a restriction that passed. Outside an emergency the plan is BLOCKED with
reason ``policy_error``; in an emergency the failure is reported and does not
block, because evacuation and life safety are never blocked by the hub.

Moved here from PolicyEngine.evaluate (dosync/policies.py) unchanged apart from
H7; ``dosync.policies`` re-exports these names.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from ..models import ActionPlan, Urgency

log = logging.getLogger("dosync.samuel.policing")

POLICY_ERROR = "policy_error"


class PolicyDecision(str, Enum):
    ALLOW   = "allow"    # proceed normally
    BLOCK   = "block"    # reject the intent
    CONFIRM = "confirm"  # require explicit confirmation (legacy whole-plan hold)
    MODIFY  = "modify"   # adjust the plan before execution


@dataclass
class PolicyResult:
    decision:    PolicyDecision
    policy_name: str
    reason:      str = ""
    modified_actions: list = field(default_factory=list)
    # Policies that raised while being asked (H7), as "name: error". Present on
    # whatever result the combination produced, so the hub can record them.
    errors:      list = field(default_factory=list)

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


class BasePolicy(ABC):
    """What the kernel needs of a policy: a name, a priority, whether an
    emergency bypasses it, and an answer (or None to abstain) for a plan."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    def priority(self) -> int:
        """Lower number = asked first. Default 100."""
        return 100

    @property
    def bypass_on_emergency(self) -> bool:
        """Whether an EMERGENCY intent skips this policy. Default True; an
        operator's absolute restriction sets False and is asked even then."""
        return True

    @abstractmethod
    def evaluate(self, intent, plan) -> PolicyResult | None:
        ...


def _with_plan(plan: ActionPlan, actions: list) -> ActionPlan:
    return ActionPlan(intent_id=plan.intent_id, actions=actions, urgency=plan.urgency)


def evaluate_policies(policies: Iterable, intent, plan: ActionPlan) -> PolicyResult:
    """Combine the operator's policies over one plan (R11). ``policies`` must
    already be in priority order (PolicyEngine keeps them sorted)."""
    policies = list(policies)
    errors: list[str] = []

    def ask(policy, current):
        try:
            return policy.evaluate(intent, current), None
        except Exception as e:                      # H7
            log.error("Policy '%s' raised an exception: %s", policy.name, e)
            errors.append(f"{policy.name}: {type(e).__name__}: {e}")
            return None, e

    if intent.urgency == Urgency.EMERGENCY:
        non_bypassable = [p for p in policies if not p.bypass_on_emergency]
        current = plan
        modified: list[str] = []
        reasons: list[str] = []
        for policy in non_bypassable:
            result, failed = ask(policy, current)
            if failed is not None or result is None:
                # A failure in an emergency is reported, never a block: the hub
                # does not stop an evacuation over a policy's bug.
                continue
            if result.decision == PolicyDecision.BLOCK:
                log.info("EMERGENCY intent blocked by non-bypassable policy '%s': %s",
                         policy.name, result.reason)
                result.errors = errors
                return result
            if result.decision == PolicyDecision.MODIFY:
                # A non-bypassable policy's decision is honoured WHATEVER it is,
                # not only BLOCK (fixed 2026-07-14: MODIFY used to be discarded).
                current = _with_plan(plan, result.modified_actions)
                modified.append(policy.name)
                reasons.append(f"{policy.name}: {result.reason}")
        log.info("EMERGENCY intent — %d/%d policies bypassed",
                 len(policies) - len(non_bypassable), len(policies))
        if modified:
            return PolicyResult(
                decision=PolicyDecision.MODIFY, policy_name=", ".join(modified),
                # each policy's OWN declared reason, for audit provenance
                reason="; ".join(reasons) or "Plan modified by non-bypassable policies",
                modified_actions=current.actions, errors=errors)
        result = PolicyResult.allow("emergency_bypass")
        result.errors = errors
        return result

    current = plan
    modified = []
    reasons = []
    for policy in policies:
        result, failed = ask(policy, current)
        if failed is not None:
            return PolicyResult(
                decision=PolicyDecision.BLOCK, policy_name=policy.name,
                reason=f"{POLICY_ERROR}: the operator's policy '{policy.name}' could not be "
                       f"evaluated ({type(failed).__name__}: {failed}); a restriction that "
                       "could not be checked is not one that passed",
                errors=errors)
        if result is None:
            continue
        if result.decision in (PolicyDecision.BLOCK, PolicyDecision.CONFIRM):
            result.errors = errors
            return result
        if result.decision == PolicyDecision.MODIFY:
            current = _with_plan(plan, result.modified_actions)
            modified.append(policy.name)
            reasons.append(f"{policy.name}: {result.reason}")
    if modified:
        log.info("MODIFY applied by %s", modified)
        return PolicyResult(
            decision=PolicyDecision.MODIFY, policy_name=", ".join(modified),
            reason="; ".join(reasons) or "Plan modified by policies",
            modified_actions=current.actions, errors=errors)
    result = PolicyResult.allow("no_policy_matched")
    result.errors = errors
    return result
