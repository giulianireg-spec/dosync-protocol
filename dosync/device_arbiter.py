"""
DoSync — Device Arbiter: emergency preemption at the device level.
==================================================================

The PolicyEngine resolves conflicts PRE-DISPATCH. It cannot touch a plan that is
already in flight, and its high-priority branch is a no-op against an active
lower-priority plan: when an emergency arrives while a routine is executing, the
policy logs "overrides" and returns None — it does not retract the routine's
in-flight actions. The dispatch (`asyncio.gather` per plan) has no per-device
serialization, so both commands race the same device and last-write-wins can leave
it in the routine's (lower-urgency) state. See spec/CONSISTENCY-MODEL.md §3.

This component closes that gap at the EXECUTION layer. It guarantees that an
`emergency`-urgency write is the *device-final* write with respect to any
lower-urgency action it OVERLAPS WITH — without aborting an in-flight action
mid-send.

Scope: INSTANT actions only. Preempting a long-running operation (a drone
mid-maneuver) is a separate concern handled by the operations supervisor
(dosync/operations.py, the INTERRUPTED state). Aborting an instant light command
leaves no undefined intermediate state; aborting a maneuver does — different
contracts.

Claim lifetime — OVERLAP-SCOPED, not wall-clock
-----------------------------------------------
A claim is NOT a fixed-duration lock on a device (an earlier design used a 30 s TTL;
it was wrong — it broke certification and would block a legitimate routine fired
minutes after an emergency had already resolved). A claim lasts only as long as the
contention it exists to win:

  * SET when an action whose urgency rank >= the claim threshold (default: emergency)
    is executed. The claim is HELD (open-ended) while the emergency intent is active.
  * RELEASED by the hub via `release_claim(device_ids)` when the emergency intent
    completes. Release does not drop the claim instantly — it starts a short `grace`
    countdown that covers the dispatch skew of a concurrently-dispatched lower-urgency
    plan whose straggler command may still be arriving. `grace` must be > 0 (0 reopens
    the race) and is sized to the adapter's execution latency, not to minutes.
  * SAFETY CAP `max_hold`: if `release_claim` is never called (a wiring bug, a crash),
    the claim expires anyway after `max_hold`, so a device is never locked forever.

A lower-urgency action targeting a device with an active, strictly-higher claim is
dropped (never sent), returned as success=False with a superseded marker, and
reported to the audit hook. A dropped action never reaches the adapter, so it never
calls `resolver.update_state()` — cache coherence is preserved as a consequence of
the drop, not as a separate step.

Per-device serialization: at most one action is mid-send per device (one lock per
device_id); different devices stay fully parallel. The claim is re-checked after
acquiring the lock, because a higher claim may have arrived while waiting.

Urgency is the arbitration axis — not the intent-class priority map — because it is
the signal available at the executor boundary and exactly what distinguishes the
canonical case (emergency vs routine). Same-urgency conflicts remain the
PolicyEngine's job, pre-dispatch.

Transparent wrapper: callers keep using `await executor.execute(action, urgency)`;
any other attribute is delegated to the inner executor.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Iterable, Optional

from .models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.arbiter")

# Urgency → numeric rank. Higher = more urgent = wins the device.
_URGENCY_RANK: dict[Urgency, int] = {
    Urgency.INFO: 0,
    Urgency.WARNING: 1,
    Urgency.ALERT: 2,
    Urgency.EMERGENCY: 3,
}


def _default_claim_min_rank() -> int:
    """Minimum urgency rank that asserts a device claim. Default: emergency only.
    Configurable via DOSYNC_CLAIM_MIN_URGENCY (info|warning|alert|emergency)."""
    name = os.environ.get("DOSYNC_CLAIM_MIN_URGENCY", "emergency").lower()
    try:
        return _URGENCY_RANK[Urgency(name)]
    except (KeyError, ValueError):
        return _URGENCY_RANK[Urgency.EMERGENCY]


def _default_grace() -> float:
    """Seconds a claim lingers AFTER the emergency intent is released, to cover the
    dispatch skew of a concurrently-dispatched lower-urgency straggler. Sized to
    adapter latency, not minutes. Must be > 0. Default 3s."""
    try:
        return max(0.0, float(os.environ.get("DOSYNC_EMERGENCY_CLAIM_GRACE", "3")))
    except ValueError:
        return 3.0


def _default_max_hold() -> float:
    """Safety cap: max seconds a claim is held if release_claim() is never called
    (wiring bug / crash), so a device is never locked forever. Default 60s."""
    try:
        return max(1.0, float(os.environ.get("DOSYNC_EMERGENCY_CLAIM_MAX_HOLD", "60")))
    except ValueError:
        return 60.0


class _Claim:
    """A device claim. HELD (open-ended, capped by max_hold) until released; once
    released, active only for `grace` more seconds."""
    __slots__ = ("rank", "urgency", "set_at", "released_at", "grace", "max_hold")

    def __init__(self, rank: int, urgency: str, set_at: float, grace: float, max_hold: float):
        self.rank = rank
        self.urgency = urgency
        self.set_at = set_at
        self.released_at: Optional[float] = None
        self.grace = grace
        self.max_hold = max_hold

    def is_active(self, now: float) -> bool:
        if self.released_at is None:
            return now < self.set_at + self.max_hold      # held (until safety cap)
        return now < self.released_at + self.grace         # releasing (grace countdown)

    def release(self, now: float) -> None:
        if self.released_at is None:
            self.released_at = now


class DeviceArbiter:
    """Wraps any executor exposing `async execute(action, urgency) -> ActionResult`,
    adding per-device serialization and overlap-scoped emergency claims."""

    def __init__(
        self,
        inner,
        audit_hook: Optional[Callable[[dict], None]] = None,
        grace: Optional[float] = None,
        max_hold: Optional[float] = None,
        claim_min_rank: Optional[int] = None,
        now_fn: Callable[[], float] = time.time,
    ):
        self._inner = inner
        self._audit_hook = audit_hook
        self._grace = grace if grace is not None else _default_grace()
        self._max_hold = max_hold if max_hold is not None else _default_max_hold()
        self._claim_min_rank = (
            claim_min_rank if claim_min_rank is not None else _default_claim_min_rank()
        )
        self._now = now_fn
        self._locks: dict[str, asyncio.Lock] = {}
        self._claims: dict[str, _Claim] = {}

    # Delegate everything we don't override to the wrapped executor (register,
    # get_state, adapters, etc.), so the arbiter is a drop-in for AdapterExecutor.
    def __getattr__(self, name):
        if name == "_inner":  # guard against recursion before __init__ completes
            raise AttributeError(name)
        return getattr(self._inner, name)

    # ── internals ────────────────────────────────────────────────────────────
    def _rank(self, urgency: Urgency) -> int:
        return _URGENCY_RANK.get(urgency, 0)

    def _lock_for(self, device_id: str) -> asyncio.Lock:
        lock = self._locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[device_id] = lock
        return lock

    def _active_claim(self, device_id: str, now: float) -> Optional[_Claim]:
        c = self._claims.get(device_id)
        if c is None:
            return None
        if not c.is_active(now):
            self._claims.pop(device_id, None)
            return None
        return c

    def _set_claim(self, device_id: str, rank: int, urgency: Urgency, now: float) -> None:
        existing = self._active_claim(device_id, now)
        if existing is not None and existing.rank > rank:
            return  # a strictly-higher claim already owns the device; don't downgrade
        self._claims[device_id] = _Claim(
            rank=rank,
            urgency=urgency.value if hasattr(urgency, "value") else str(urgency),
            set_at=now,
            grace=self._grace,
            max_hold=self._max_hold,
        )

    def _supersede(self, action: DeviceAction, claim: _Claim) -> ActionResult:
        reason = (
            f"superseded: device '{action.device_id}' is claimed by a higher-urgency "
            f"action ({claim.urgency}); '{action.action}' not applied"
        )
        if self._audit_hook is not None:
            try:
                self._audit_hook({
                    "type": "action_superseded_by_priority",
                    "device_id": action.device_id,
                    "action": action.action,
                    "claimed_by_urgency": claim.urgency,
                    "ts": self._now(),
                })
            except Exception:  # audit must never break execution
                log.warning("arbiter audit hook failed", exc_info=True)
        log.info("ARBITER supersede: %s.%s dropped (device claimed by %s)",
                 action.device_id, action.action, claim.urgency)
        return ActionResult(
            device_id=action.device_id,
            action=action.action,
            success=False,
            error=reason,
            response={"superseded": True, "claimed_by_urgency": claim.urgency},
        )

    # ── claim lifecycle (called by the hub) ───────────────────────────────────
    def release_claim(self, device_ids: Iterable[str]) -> None:
        """Called by the hub when an emergency intent completes. Starts the grace
        countdown on each device's claim (does not drop it instantly — a
        concurrently-dispatched straggler may still be arriving)."""
        now = self._now()
        for device_id in device_ids:
            c = self._claims.get(device_id)
            if c is not None:
                c.release(now)

    def clear_claims(self) -> None:
        """Drop all claims immediately. For tests and for deterministic resets."""
        self._claims.clear()

    def active_claims(self) -> dict[str, str]:
        """Introspection: device_id -> claiming urgency, for active claims only."""
        now = self._now()
        return {d: c.urgency for d, c in list(self._claims.items()) if c.is_active(now)}

    # ── public ─────────────────────────────────────────────────────────────────
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        device_id = action.device_id
        rank = self._rank(urgency)
        now = self._now()

        # 1. Claim-first: a qualifying high-urgency action claims the device before
        #    contending for the lock, so lower-urgency actions queued on the lock
        #    self-drop the moment they acquire it (the emergency only ever waits for
        #    the single action currently mid-send, never the whole lower plan).
        if rank >= self._claim_min_rank:
            self._set_claim(device_id, rank, urgency, now)

        # 2. Respect a strictly-higher existing claim before queuing on the lock.
        claim = self._active_claim(device_id, now)
        if claim is not None and claim.rank > rank:
            return self._supersede(action, claim)

        # 3. Per-device serialization (different devices stay parallel).
        async with self._lock_for(device_id):
            claim = self._active_claim(device_id, self._now())
            if claim is not None and claim.rank > rank:
                return self._supersede(action, claim)
            return await self._inner.execute(action, urgency)
