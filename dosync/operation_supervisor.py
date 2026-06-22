"""
DoSync — Operation supervisor (the nervous-system trunk of a composite operation).
==================================================================================

CompositeOperation is the structure (the ordered steps). RouteComposer fills it with
geometry. This module is what makes them LIVE: the supervisor drives a composite
forward, step by step, in a CLOSED LOOP — it dispatches the current step, then WAITS
for that step's atomic operation to reach a positive terminal state (confirmed
arrival, never a timer), and only then advances. Between steps it samples continuously
so guards (next piece) can react in real time.

This is the brain coordinating the body: not "fire the plan and walk away" (open
loop), but "watch what the body actually did, decide the next move, repeat" (closed
loop). A waypoint's confirmed arrival is not the end of an instruction — it is
perception that feeds the next decision.

DESIGN (validated by a multi-pass expert panel incl. a drone manufacturer, a pilot,
and two systems professors):

  * POLLING at a fixed, CONFIGURABLE cadence — not event subscription. The panel's
    reasons: (1) the system ALREADY beats — the telemetry consumer
    (drain_telemetry_once -> apply_telemetry) already runs on a rhythm; the
    supervisor hangs off that nature instead of bolting on a callback mechanism.
    (2) Real-time guards (geofence-in-flight, battery, link loss) are INHERENTLY
    sampling — there is no "geofence-breach event"; you must look. One heartbeat
    serves both arrival-detection and guards. (3) A predictable cadence is a SAFETY
    property: you know exactly how often the brain looks at the body.

  * The supervisor POLLS THE RECONCILED STATE, never the raw telemetry queue. The
    consumer is perception (telemetry -> state); the supervisor is decision (state ->
    next move). The brain reads the processed body-map, not the raw nerves. This
    separation is non-negotiable — it is what keeps the nervous system clean.

  * SILENCE IS NOT SUCCESS — inherited end to end. A step only advances when its
    atomic operation reaches a POSITIVE terminal state (completed). A timeout or a
    silent gap never advances anything; it is a stall the guards can act on.

  * Dependency-injected dispatch and state-read so the whole loop is unit-testable
    with NO drone, NO socket, NO hub: the test supplies a fake "dispatch" and a fake
    "read state", and drives the composite through its lifecycle deterministically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .composite_operations import CompositeOperation, CompositeState, CompositeStep

log = logging.getLogger("dosync.supervisor")


# Default sampling cadence. The panel required this be configurable, not hardcoded:
# a slow inspection can sample wider; a critical maneuver, finer.
_DEFAULT_POLL_INTERVAL_S = 0.2   # 5 Hz — the brain looks at the body 5x/second
# Safety ceiling: how long a single step may sit without reaching a terminal atom
# state before the supervisor treats it as stalled and aborts the composite. This is
# a backstop, not the primary mechanism (guards are) — silence must never hang forever.
_DEFAULT_STEP_TIMEOUT_S = 120.0


# A step dispatcher: given a CompositeStep, start its atomic operation and return the
# operation_id the supervisor will then watch. Injected so tests need no hub/drone.
DispatchFn = Callable[[CompositeStep], Awaitable[str]]

# A state reader: given an operation_id, return the reconciled atomic state as a
# string ("pending" | "in_progress" | "completed" | "failed" | "interrupted" | ...),
# or None if unknown. Reads the RECONCILED state — never the raw queue.
ReadStateFn = Callable[[str], Optional[str]]

# A guard check: inspect the live composite and return an abort reason string if a
# guard fires, or None to continue. The guards themselves are the next piece; the
# supervisor just calls this hook each tick. Injected; defaults to "no guards".
GuardFn = Callable[[CompositeOperation], Optional[str]]


# Atomic states that count as a positive, successful completion of a step.
_POSITIVE_TERMINAL = {"completed"}
# Atomic states that mean the step ended without success → the composite cannot
# simply advance; it must react (abort / return home).
_NEGATIVE_TERMINAL = {"failed", "rejected", "cancelled", "interrupted"}


@dataclass
class SupervisorConfig:
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S
    step_timeout_s: float = _DEFAULT_STEP_TIMEOUT_S


class OperationSupervisor:
    """Drives one CompositeOperation through its steps in a closed loop.

    The supervisor owns the composite's mission-level progression. For each step it:
      1. transitions the composite to the right mission state (in_transit / returning),
      2. dispatches the step's atomic action (injected DispatchFn) and records the
         resulting operation_id on the step,
      3. polls the reconciled atomic state at the configured cadence until it reaches
         a terminal state — advancing on a positive one, aborting on a negative one,
      4. on every tick, runs the guard hook; if a guard fires, it aborts (the safe
         default is to stop and let the caller route to return-home).

    It never drains the telemetry queue and never reaches into the atom's state
    machine — it reads reconciled state and advances its OWN (composite) machine.
    """

    def __init__(self, dispatch: DispatchFn, read_state: ReadStateFn,
                 guard: Optional[GuardFn] = None,
                 config: Optional[SupervisorConfig] = None,
                 sleep: Optional[Callable[[float], Awaitable[None]]] = None,
                 now: Optional[Callable[[], float]] = None):
        self._dispatch = dispatch
        self._read_state = read_state
        self._guard = guard or (lambda comp: None)
        self._config = config or SupervisorConfig()
        # Injected clock/sleep so tests run instantly and deterministically.
        if sleep is None:
            import asyncio
            sleep = asyncio.sleep
        self._sleep = sleep
        if now is None:
            import time
            now = time.monotonic
        self._now = now

    async def run(self, comp: CompositeOperation) -> CompositeState:
        """Drive the composite to a terminal mission state and return it.

        Returns COMPLETED if every step reached a positive terminal state, or
        ABORTED/FAILED if a guard fired, a step failed, or a step stalled.
        """
        if comp.state != CompositeState.PLANNING:
            raise ValueError(
                f"supervisor expects a freshly composed operation in PLANNING, "
                f"got {comp.state.value}")

        comp.transition_to(CompositeState.IN_TRANSIT, reason="supervisor started")

        while True:
            step = comp.current_step
            if step is None:
                # All steps consumed without a dedicated return step having moved us to
                # RETURNING — treat the sequence as done.
                break

            # The return step flips the mission into RETURNING (its own leg).
            if step.kind == "return" and comp.state == CompositeState.IN_TRANSIT:
                comp.transition_to(CompositeState.RETURNING, reason="returning to base")

            # Guard check BEFORE dispatching the next step — never start a step if a
            # guard already says stop.
            reason = self._guard(comp)
            if reason is not None:
                return self._abort(comp, f"guard: {reason}")

            # Dispatch the step's atomic action; record the operation_id to watch.
            try:
                operation_id = await self._dispatch(step)
            except Exception as e:
                return self._abort(comp, f"dispatch raised: {e}")
            step.operation_id = operation_id

            outcome = await self._await_step(comp, operation_id)
            if outcome == "positive":
                comp.advance()
                continue
            if outcome == "guard":
                # _await_step already knows the reason via the guard; abort generically.
                return self._abort(comp, "guard fired during step")
            if outcome == "stalled":
                return self._abort(comp, f"step '{step.action}' stalled (no positive signal)")
            # negative terminal
            return self._abort(comp, f"step '{step.action}' ended in a non-success state")

        # Every step done. If we were returning, we are home; otherwise close directly.
        if comp.state == CompositeState.RETURNING:
            comp.transition_to(CompositeState.COMPLETED, reason="returned to base")
        else:
            # No explicit return step; still a successful completion of all steps.
            comp.transition_to(CompositeState.RETURNING, reason="no explicit return step")
            comp.transition_to(CompositeState.COMPLETED, reason="all steps completed")
        return comp.state

    async def _await_step(self, comp: CompositeOperation, operation_id: str) -> str:
        """Poll the reconciled atomic state until terminal. Returns one of:
        'positive' | 'negative' | 'guard' | 'stalled'. Runs the guard hook each tick —
        the closed loop: every sample, decide."""
        started = self._now()
        while True:
            # Guard check every tick — real-time reaction, not only at waypoints.
            reason = self._guard(comp)
            if reason is not None:
                log.info("supervisor: guard fired mid-step on %s: %s",
                         comp.composite_id, reason)
                return "guard"

            state = self._read_state(operation_id)
            if state in _POSITIVE_TERMINAL:
                return "positive"
            if state in _NEGATIVE_TERMINAL:
                return "negative"

            # Backstop: a step that never reaches terminal must not hang forever.
            if self._now() - started > self._config.step_timeout_s:
                return "stalled"

            await self._sleep(self._config.poll_interval_s)

    def _abort(self, comp: CompositeOperation, reason: str) -> CompositeState:
        """Move the composite to ABORTED (safe default). The caller is responsible for
        routing the vehicle home — the supervisor records the decision; the return
        itself is a separate dispatched action the caller may issue."""
        if not comp.is_terminal:
            comp.transition_to(CompositeState.ABORTED, reason=reason)
        log.warning("supervisor: composite %s aborted — %s", comp.composite_id, reason)
        return comp.state
