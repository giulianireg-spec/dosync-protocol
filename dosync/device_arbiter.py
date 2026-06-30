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

This component closes that gap at the EXECUTION layer. It guarantees that the
highest-urgency writer is the *device-final* writer within a bounded window, WITHOUT
aborting in-flight actions mid-send.

Scope: INSTANT actions only. Preempting a long-running operation (a drone
mid-maneuver) is a separate concern handled by the operations supervisor
(dosync/operations.py, the INTERRUPTED state). The reason they are different:
aborting an instant light command leaves no undefined intermediate state, while
aborting a maneuver does — so the two cases need different contracts.

Mechanism — claim-first, then per-device lock:

  1. CLAIM-FIRST. An action whose urgency rank is >= the claim threshold claims its
     device *before* contending for the per-device lock, at its urgency rank, for
     `DOSYNC_EMERGENCY_CLAIM_TTL` seconds. Setting the claim first means lower-rank
     actions already queued on the lock self-drop the moment they acquire it — so an
     emergency waits at most for the single action currently mid-send, never for the
     whole lower-priority plan.

  2. RESPECT HIGHER CLAIMS. Any action targeting a device with an active claim of
     STRICTLY HIGHER rank is dropped (never sent), returned as success=False with a
     superseded marker, and reported to the audit hook. A dropped action never
     reaches the adapter, so it never calls `resolver.update_state()` — the state
     cache is never poisoned by the superseded routine. Cache coherence is preserved
     as a consequence of the drop, not as a separate step.

  3. PER-DEVICE SERIALIZATION. At most one action is mid-send per device, so two
     plans never race the same physical device. Different devices stay fully parallel
     (one lock per device_id). The claim is re-checked after acquiring the lock,
     because a higher claim may have arrived while waiting.

Urgency is the arbitration axis — not the intent-class priority map — because it is
the signal available at the executor boundary, it is exactly what distinguishes the
canonical case (emergency vs routine), and it keeps the executor decoupled from
intent semantics. Same-urgency conflicts remain the PolicyEngine's job, pre-dispatch.

Transparent wrapper: callers keep using `await executor.execute(action, urgency)`.
Any other attribute (`.register`, `.get_state`, ...) is delegated to the inner
executor, so an AdapterExecutor wrapped in a DeviceArbiter behaves like the
AdapterExecutor for everything except the added arbitration.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Optional

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


def _default_claim_ttl() -> float:
    """Seconds a device stays claimed after a high-urgency write. Bounded by design:
    the device returns to normal control after the window. Default 30s."""
    try:
        return float(os.environ.get("DOSYNC_EMERGENCY_CLAIM_TTL", "30"))
    except ValueError:
        return 30.0


class _Claim:
    __slots__ = ("rank", "urgency", "expires_at")

    def __init__(self, rank: int, urgency: str, expires_at: float):
        self.rank = rank
        self.urgency = urgency
        self.expires_at = expires_at


class DeviceArbiter:
    """Wraps any executor exposing `async execute(action, urgency) -> ActionResult`,
    adding per-device serialization and bounded emergency claims."""

    def __init__(
        self,
        inner,
        audit_hook: Optional[Callable[[dict], None]] = None,
        claim_ttl: Optional[float] = None,
        claim_min_rank: Optional[int] = None,
        now_fn: Callable[[], float] = time.time,
    ):
        self._inner = inner
        self._audit_hook = audit_hook
        self._claim_ttl = claim_ttl if claim_ttl is not None else _default_claim_ttl()
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
        if c.expires_at <= now:
            self._claims.pop(device_id, None)
            return None
        return c

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

    # ── public ───────────────────────────────────────────────────────────────
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        device_id = action.device_id
        rank = self._rank(urgency)
        now = self._now()

        # 1. Claim-first: claim the device before contending for the lock, so
        #    lower-urgency actions queued on the lock self-drop when they acquire it.
        if rank >= self._claim_min_rank:
            self._claims[device_id] = _Claim(
                rank=rank,
                urgency=urgency.value if hasattr(urgency, "value") else str(urgency),
                expires_at=now + self._claim_ttl,
            )

        # 2. Respect a strictly-higher existing claim before queuing on the lock.
        claim = self._active_claim(device_id, now)
        if claim is not None and claim.rank > rank:
            return self._supersede(action, claim)

        # 3. Per-device serialization (different devices stay parallel).
        async with self._lock_for(device_id):
            # Re-check: a higher claim may have arrived while we waited for the lock.
            claim = self._active_claim(device_id, self._now())
            if claim is not None and claim.rank > rank:
                return self._supersede(action, claim)
            return await self._inner.execute(action, urgency)
