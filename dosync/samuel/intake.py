"""Admission of an intent as a whole, before anything is proposed or resolved.

These are the refusals that apply to the intent, not to one action of it
(SAMUEL §5.1; DoSync protocol §6.8 rules 2, 3, 6 and 7). Until 2026-10-11 they
lived only in the REST handler of POST /v1/intent/async, so an intent that
reached the hub any other way -- the hub's own capability-anomaly alert, a
library caller, a future bridge -- skipped them. They live here now, and both
the REST layer and DoSyncHub.execute_intent call admit(): one rule, every door.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Urgency, device_at_location, opposite_pairs

#: Why an intent as a whole is refused (HTTP 422 on the REST surface).
INTENT_REFUSALS = ("not_registered", "unknown_location", "invalid_actions",
                   "invalid_proposals", "proposals_required", "ambiguous_actions")

#: At most this many proposals in one intent.
MAX_PROPOSALS = 200


@dataclass(frozen=True)
class Admission:
    """The kernel's answer about an intent as a whole.

    ``refused`` is None when the intent may go on, else one of INTENT_REFUSALS
    with a human-readable ``detail``. ``location_not_found`` names a restricting
    place no device is at, for an EMERGENCY (never refused: it acts as an
    emergency without a place would, and the hub records that). ``context`` is
    the intent's context as the kernel reads it: an empty ``proposed_actions``
    list means "no proposals" and is removed.
    """
    refused: Optional[str] = None
    detail: str = ""
    location_not_found: Optional[str] = None
    context: dict = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return self.refused is None


def admit(intent_name: str, class_row: Optional[dict], urgency: Urgency,
          context: Optional[dict], active_devices: Iterable) -> Admission:
    """Decide whether an intent may proceed at all.

    ``class_row`` is the registered intent class (None if the class is not
    registered); ``active_devices`` are the registry's active devices, read only
    to know whether a place exists. Pure: reads its arguments, writes nothing.
    """
    ctx = dict(context or {})
    if class_row is None:
        return Admission("not_registered", f"Intent '{intent_name}' is not registered.", context=ctx)

    # A location that restricts where the intent acts must name a place some
    # device is actually at. Otherwise a typo ("main-bedrom") resolves to no
    # device at all and the intent "completes" with zero actions -- silent. Only
    # when the location restricts: an alert's location just says where something
    # happened. An EMERGENCY is never refused: with no device at its location it
    # acts as an emergency without one would, and that is recorded -- never
    # narrowed to nothing, never silently widened.
    location = ctx.get("location")
    location_not_found = None
    if isinstance(location, str) and location:
        if class_row.get("location_role", "restricts") == "restricts":
            if not any(device_at_location(d, location) for d in active_devices):
                if urgency == Urgency.EMERGENCY:
                    location_not_found = location
                else:
                    return Admission(
                        "unknown_location",
                        f"No device is at location '{location}'. '{intent_name}' acts only "
                        "where context.location says, and no registered device is there. "
                        "Check the spelling, or omit location to act on every capable device.",
                        context=ctx)

    # An intent may narrow its class's actions (context.action_types), and must
    # when the class asks for actions that undo each other: control_access asks
    # for lock and unlock, and "secure the plant" sent a lock both.
    class_actions = list(class_row.get("resolution_actuators") or [])
    any_declared = "*" in class_actions
    wanted = ctx.get("action_types")
    if wanted is not None:
        if (not isinstance(wanted, list) or not wanted
                or not all(isinstance(a, str) for a in wanted)
                or not (any_declared or set(wanted) <= set(class_actions))):
            return Admission(
                "invalid_actions",
                "context.action_types must be a non-empty subset of the actions "
                f"'{intent_name}' asks for: {sorted(class_actions)}",
                context=ctx)

    # Governed direct mode: the agent proposes the actions itself. An empty list
    # means the agent proposes nothing and the hub resolves as usual (refusing it
    # cost the second agent comparison every sensor read asked for that way).
    proposals = ctx.get("proposed_actions")
    if isinstance(proposals, list) and not proposals:
        ctx.pop("proposed_actions", None)
        proposals = None
    if proposals is not None:
        if (not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS
                or not all(isinstance(p, dict) and isinstance(p.get("device_id"), str)
                           and isinstance(p.get("action"), str)
                           and isinstance(p.get("params", {}), dict) for p in proposals)):
            return Admission(
                "invalid_proposals",
                f"context.proposed_actions must be a list (at most {MAX_PROPOSALS}) of "
                "{device_id, action, params?} objects; an empty list means none",
                context=ctx)
    if any_declared and proposals is None:
        # A class that grants any declared action (operate_device) acts only on
        # what the agent names: there is nothing to resolve.
        return Admission(
            "proposals_required",
            f"'{intent_name}' acts only on the actions you propose: put the device "
            "and action in context.proposed_actions, e.g. "
            '[{"device_id": "lamp-hall", "action": "turn_off"}]',
            context=ctx)

    # Each proposal names its direction, so a class's ambiguity applies only when
    # the hub is to resolve; in an emergency the resolver never sends both.
    effective = set(wanted) if wanted else {a for a in class_actions if a != "*"}
    pairs = opposite_pairs(effective)
    if pairs and urgency != Urgency.EMERGENCY and proposals is None:
        return Admission(
            "ambiguous_actions",
            f"'{intent_name}' asks for actions that undo each other: "
            + ", ".join(f"{a}/{b}" for a, b in pairs)
            + ". Say which you mean with context.action_types, e.g. "
            f"{{\"action_types\": [\"{pairs[0][0]}\"]}}.",
            context=ctx)

    return Admission(None, "", location_not_found, ctx)
