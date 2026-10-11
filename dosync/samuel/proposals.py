"""Validation of proposed actions: the governed direct mode (SAMUEL §5.2; DoSync
protocol §6.8 rule 7).

The agent -- or any other proposer -- has chosen devices and actions; the kernel
decides which of them may run. Moved here unchanged from
StateAwareResolver.validate_proposals (2026-10-11), which now delegates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from ..models import ActionPlan, DeviceAction, Intent, Urgency, device_at_location, opposite_pairs

log = logging.getLogger("dosync.samuel")

#: Why a proposed action is refused (in the result and the audit log, never 422).
PROPOSAL_REFUSALS = ("unknown_device", "not_declared", "outside_class", "outside_place",
                     "not_its_emergency_action", "opposite_actions")


@dataclass(frozen=True)
class ClassGrant:
    """What an intent class grants, as the kernel reads it.

    ``actions`` is the set of action types the class allows ("*" = any action a
    device declares), already narrowed by the intent's ``action_types``;
    ``sensors`` the sensor types it reads; ``location_role`` either
    ``restricts`` or ``informs``.
    """
    actions: frozenset
    sensors: tuple
    location_role: str = "restricts"

    @classmethod
    def from_row(cls, row: Optional[dict], context: Optional[dict] = None) -> "ClassGrant":
        """The grant of a registered class row (as DoSync stores it), narrowed by
        the intent's ``action_types``. An unregistered class grants nothing."""
        if not row:
            return cls(frozenset(), ())
        actions = list(row.get("resolution_actuators") or [])
        wanted = (context or {}).get("action_types")
        if isinstance(wanted, list) and wanted and "*" not in actions:
            actions = [a for a in actions if a in set(wanted)]
        return cls(frozenset(actions), tuple(row.get("resolution_sensors") or []),
                   row.get("location_role", "restricts"))


def validate_proposals(intent: Intent, grant: ClassGrant,
                       get_device: Callable[[str], object],
                       active_devices: Iterable) -> ActionPlan:
    """Check each proposal against the guarantees and refuse, with its reason,
    the ones that fail: the device must exist and declare the action (G1); the
    action must be one the intent's class allows -- an intent bounds the agent's
    authority, no unlocking inside a `notify` (G2); a restricting place must
    contain the device (G3); no device receives two opposite actions (G4); in an
    emergency a device that declared its emergency actions may only do those
    (G5). What passes goes on through the operator's policies, execution and the
    audit log, exactly like a resolved plan.

    ``get_device(id)`` returns a device or None; ``active_devices`` are read only
    to decide whether a restricting place exists.
    """
    ctx = intent.context or {}
    proposals = ctx.get("proposed_actions") or []
    class_actions = set(grant.actions)
    # "*" (operate_device): any action the device declares, unless the intent
    # narrows it with action_types.
    any_declared = "*" in class_actions
    wanted = ctx.get("action_types")
    if any_declared and isinstance(wanted, list) and wanted and "*" not in wanted:
        class_actions, any_declared = set(wanted), False
    reads_allowed = any_declared or bool(grant.sensors) or not class_actions
    location = ctx.get("location")
    restrict_to = None
    if (isinstance(location, str) and location and grant.location_role == "restricts"
            and any(device_at_location(d, location) for d in active_devices)):
        restrict_to = location
    emergency = intent.urgency == Urgency.EMERGENCY
    refused, accepted, seen = [], {}, set()
    for p in proposals:
        device_id, action = p.get("device_id"), p.get("action")
        if (device_id, action) in seen:
            continue
        seen.add((device_id, action))
        device = get_device(device_id) if isinstance(device_id, str) else None
        reason = None
        # At emergency urgency, an emergency-capable device that declared its
        # emergency actions is governed by rule 5 (declared first) on BOTH paths:
        # the authorization check is its declared emergency actions, not the
        # class's grants. Until 2026-10-09 this refused a declared emergency
        # action as `outside_class` when the class did not grant it -- G5 applied
        # inconsistently between resolution and proposals.
        _emergency_declared = (emergency and device is not None
                               and device.emergency_capable and bool(device.emergency_actions))
        if device is None:
            reason = "unknown_device"
        elif action == "read_sensors":
            if not device.sensors:
                reason = "not_declared"
            elif not reads_allowed:
                reason = "outside_class"
        elif action not in {a.type for a in device.actuators}:
            reason = "not_declared"
        elif _emergency_declared:
            pass  # authorization decided by the declared-emergency check below
        elif not any_declared and action not in class_actions:
            reason = "outside_class"
        if reason is None and restrict_to and not device_at_location(device, restrict_to):
            reason = "outside_place"
        if (reason is None and _emergency_declared
                and action not in {e["action"] for e in device.emergency_actions}):
            reason = "not_its_emergency_action"
        if reason:
            refused.append({"device_id": device_id, "action": action, "reason": reason})
        else:
            accepted.setdefault(device_id, []).append((action, dict(p.get("params") or {})))
    actions: list[DeviceAction] = []
    for device_id, acts in accepted.items():
        clash = {t for pair in opposite_pairs(a for a, _ in acts) for t in pair}
        for action, params in acts:
            if action in clash:
                refused.append({"device_id": device_id, "action": action, "reason": "opposite_actions"})
            else:
                actions.append(DeviceAction(device_id=device_id, action=action, params=params,
                                            relevance_score=1.0))
    log.info("Proposals for '%s': %d accepted, %d refused",
             intent.intent.value, len(actions), len(refused))
    return ActionPlan(intent_id=intent.intent_id, actions=actions, urgency=intent.urgency,
                      refused_proposals=refused)
