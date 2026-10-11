"""Human confirmation (SAMUEL G9; DoSync protocol §6.8 rule 9).

What the operator marks consequential does not run on an agent's say-so: only
the marked actions are withheld, the rest of the plan runs. In an emergency a
device's DECLARED emergency action flows without confirmation, unless the
marking rule set ``even_in_emergency``.

A marking rule is anything with ``matches(device_id, action) -> bool``, an
``even_in_emergency`` flag and a ``reason`` -- DoSync's RequireConfirmationPolicy
is one; the kernel does not import it. Moved here unchanged from
DoSyncHub._split_plan_by_confirmation (2026-10-11); holding, confirming, denying
and expiring stay with the hub, which owns the state and the operator credential.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..models import Urgency


def split_by_confirmation(actions: Iterable, rules: list, urgency: Urgency,
                          get_device: Callable[[str], object]) -> tuple[list, list]:
    """Return (runnable actions, held) where ``held`` is [(action, reason)]. With
    no marking rules nothing is held and the actions are returned unchanged."""
    actions = list(actions)
    if not rules:
        return actions, []
    emergency = urgency == Urgency.EMERGENCY
    runnable, held = [], []
    for a in actions:
        device = get_device(a.device_id)
        declared = {e["action"] for e in (getattr(device, "emergency_actions", None) or [])}
        is_declared_emergency = a.action in declared
        marking = next(
            (r for r in rules if r.matches(a.device_id, a.action)
             and not (emergency and is_declared_emergency and not r.even_in_emergency)),
            None)
        if marking is None:
            runnable.append(a)
        else:
            held.append((a, marking.reason))
    return runnable, held
