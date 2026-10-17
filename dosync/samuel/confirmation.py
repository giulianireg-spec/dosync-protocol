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
                          get_device: Callable[[str], object],
                          recent: Callable[[object], set] | None = None) -> tuple[list, list]:
    """Return (runnable actions, held) where ``held`` is [(action, reason)]. With
    no marking rules nothing is held and the actions are returned unchanged.

    A rule with ``min_devices`` N > 1 holds only when it would act on at least N
    distinct devices, counting this plan's and the devices ``recent(rule)`` says
    it let act on within its window (so a large request split into small ones
    still reaches the threshold). Without ``recent`` only this plan counts."""
    actions = list(actions)
    if not rules:
        return actions, []
    emergency = urgency == Urgency.EMERGENCY

    def exempt(rule, a, declared):
        return emergency and a.action in declared and not rule.even_in_emergency

    declared_of = {}
    for a in actions:
        if a.device_id not in declared_of:
            device = get_device(a.device_id)
            declared_of[a.device_id] = {e["action"] for e in
                                        (getattr(device, "emergency_actions", None) or [])}
    active = []
    for r in rules:
        threshold = getattr(r, "min_devices", 1) or 1
        if threshold > 1:
            here = {a.device_id for a in actions
                    if r.matches(a.device_id, a.action) and not exempt(r, a, declared_of[a.device_id])}
            before = set(recent(r)) if recent else set()
            if len(here | before) < threshold:
                continue
        active.append(r)
    runnable, held = [], []
    for a in actions:
        marking = next(
            (r for r in active if r.matches(a.device_id, a.action)
             and not exempt(r, a, declared_of[a.device_id])),
            None)
        if marking is None:
            runnable.append(a)
        else:
            held.append((a, marking.reason))
    return runnable, held
