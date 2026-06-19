"""
DoSync — Operation reconciler (execution_model, Layer 3).
========================================================

Layer 1 defined the state machine. Layer 2 made operations survive a restart.
This layer is the active component that *drives* an operation through its states
based on what the device actually reports — telemetry.

THE ONE RULE, from the panel: telemetry wins. The hub never assumes an operation
succeeded; it advances only on a positive signal, and if telemetry contradicts the
hub's model, the telemetry is right and the model is corrected. Silence is not
success — a timeout or an absent signal never advances an operation to `completed`.

DELIBERATELY HARDWARE-AGNOSTIC. The reconciler knows nothing about MAVLink, drones,
ovens, or any protocol. It consumes ABSTRACT telemetry facts — a small vocabulary
of `TelemetryEvent`s — and maps them to state transitions. The future MAVLinkAdapter
is what translates real MAVLink messages into these abstract facts; the reconciler
stays pure logic, fully testable by injecting fake telemetry. That separation is
what lets the same reconciler serve a drone and an oven.

The reconciler does NOT decide policy. It moves operations between states and
records why. What to *do* about an operation that has been `paused_by_device` for
too long (notify the operator, trigger RTL) is the Policy Engine's job — the
reconciler only exposes the state and the time-in-state. (Panel: keep the model
and the policy separate, or the model becomes unimplementable.)
"""

from __future__ import annotations

from enum import Enum

from dosync.operations import Operation, OperationState, InvalidTransition


class TelemetryEvent(str, Enum):
    """The abstract vocabulary of facts a device can report about a running
    operation. An adapter translates device-native signals into these; the
    reconciler maps these to state transitions. This list is intentionally small
    and device-independent — a drone and an oven both speak it."""

    # ── Progress facts (positive signals — the only way forward) ──────────────
    STARTED = "started"                  # the action is confirmed underway
    PREPARING = "preparing"              # a pre-action setup phase began (richer profile).
                                         # Domain sub-phase (e.g. "arming") goes in the
                                         # operation's `phase`, not in a new event name.
    FINISHED = "finished"                # the action completed — positive completion signal.
                                         # (A speaker finishes a track; a vehicle arrives.)

    # ── Trouble facts ─────────────────────────────────────────────────────────
    REJECTED_BY_DEVICE = "rejected_by_device"   # device refused (busy, not ready, unsafe)
    FAILED = "failed"                    # failed partway through
    DEVICE_PAUSED = "device_paused"      # the device paused itself (lost stream, storage full, wind)
    DEVICE_RESUMED = "device_resumed"    # the device resumed on its own

    # ── Human / control facts ─────────────────────────────────────────────────
    MANUAL_CONTROL_TAKEN = "manual_control_taken"  # a human took manual control — interrupt
    CANCELLED = "cancelled"              # explicit cancellation request honored


# Maps a telemetry fact to the operation state it should drive toward. This is the
# whole policy of "what does this fact mean for the lifecycle", in one table.
# Note FINISHED → COMPLETED is the ONLY path to completion: there is no way to reach
# COMPLETED without a positive FINISHED signal. Silence cannot complete an operation.
_EVENT_TO_STATE: dict[TelemetryEvent, OperationState] = {
    TelemetryEvent.STARTED:              OperationState.IN_PROGRESS,
    TelemetryEvent.PREPARING:            OperationState.PREPARING,
    TelemetryEvent.FINISHED:             OperationState.COMPLETED,
    TelemetryEvent.REJECTED_BY_DEVICE:   OperationState.REJECTED,
    TelemetryEvent.FAILED:               OperationState.FAILED,
    TelemetryEvent.DEVICE_PAUSED:        OperationState.PAUSED_BY_DEVICE,
    TelemetryEvent.DEVICE_RESUMED:       OperationState.IN_PROGRESS,
    TelemetryEvent.MANUAL_CONTROL_TAKEN: OperationState.INTERRUPTED,
    TelemetryEvent.CANCELLED:            OperationState.CANCELLED,
}


class ReconcileResult:
    """The outcome of applying one telemetry fact to an operation. Carries enough
    detail for the caller to persist the change and write an audit entry."""

    __slots__ = ("operation", "changed", "from_state", "to_state", "note")

    def __init__(self, operation: Operation, changed: bool,
                 from_state: OperationState, to_state: OperationState, note: str = ""):
        self.operation = operation
        self.changed = changed
        self.from_state = from_state
        self.to_state = to_state
        self.note = note

    def __repr__(self) -> str:
        if self.changed:
            return (f"<Reconcile {self.operation.operation_id}: "
                    f"{self.from_state.value} → {self.to_state.value}>")
        return (f"<Reconcile {self.operation.operation_id}: no change "
                f"({self.from_state.value}); {self.note}>")


class OperationReconciler:
    """Applies abstract telemetry facts to operations, advancing their state
    machine. Pure logic — no I/O, no hardware. The hub feeds it telemetry (from an
    adapter) and persists the result; the reconciler decides the state change.
    """

    def reconcile(self, operation: Operation, event: TelemetryEvent,
                  reason: str = "", now: float | None = None) -> ReconcileResult:
        """Apply one telemetry fact to one operation.

        Returns a ReconcileResult describing whether the state changed. Telemetry
        wins: if the fact maps to a legal transition, it is applied. If the fact is
        irrelevant to the current state (e.g. a duplicate "started" while already
        in progress), it is a no-op, not an error — telemetry can be noisy and
        repeat. If the fact would require an illegal jump, that is surfaced as a
        no-op with an explanatory note rather than crashing the hub: a late or
        out-of-order telemetry packet must never take the hub down.
        """
        if isinstance(event, str):
            event = TelemetryEvent(event)

        from_state = operation.state
        target = _EVENT_TO_STATE[event]

        # Already there (idempotent telemetry) — common and harmless.
        if from_state == target:
            return ReconcileResult(operation, False, from_state, target,
                                   note=f"already in {target.value}")

        # Terminal operations never move — an interrupted op does not resume, a
        # completed op stays completed even if a stale packet arrives later.
        if operation.is_terminal:
            return ReconcileResult(operation, False, from_state, from_state,
                                   note=f"operation is terminal ({from_state.value}); "
                                        f"telemetry '{event.value}' ignored")

        # Apply if legal; otherwise no-op with a note. We do NOT force illegal
        # transitions — but we also never silently pretend success.
        if not operation.can_transition_to(target):
            return ReconcileResult(operation, False, from_state, from_state,
                                   note=f"telemetry '{event.value}' implies "
                                        f"{target.value}, illegal from {from_state.value}")

        full_reason = reason or f"telemetry: {event.value}"
        try:
            operation.transition_to(target, reason=full_reason, now=now)
        except InvalidTransition as e:
            # Defensive: can_transition_to said yes but transition raised. Surface
            # as no-op rather than propagate — the hub must stay up.
            return ReconcileResult(operation, False, from_state, from_state,
                                   note=f"transition rejected: {e}")

        return ReconcileResult(operation, True, from_state, target, note=full_reason)

    def reconcile_after_restart(self, operation: Operation,
                                now: float | None = None) -> ReconcileResult:
        """Place a recovered operation into the transient RECONCILING state on hub
        restart, so the hub re-confirms reality from telemetry before assuming the
        operation is still where it left off. The failsafe may have acted while the
        hub was down — the next real telemetry fact resolves it.

        Only meaningful for telemetry-capable operations; a core-only device has no
        telemetry to reconcile against, so it is left as-is.
        """
        from_state = operation.state
        if not operation.telemetry_capable:
            return ReconcileResult(operation, False, from_state, from_state,
                                   note="core-only operation: nothing to reconcile")
        if operation.is_terminal:
            return ReconcileResult(operation, False, from_state, from_state,
                                   note=f"terminal ({from_state.value}); no reconcile")
        if not operation.can_transition_to(OperationState.RECONCILING):
            return ReconcileResult(operation, False, from_state, from_state,
                                   note=f"cannot reconcile from {from_state.value}")
        operation.transition_to(OperationState.RECONCILING,
                                reason="hub restart: reconciling against telemetry",
                                now=now)
        return ReconcileResult(operation, True, from_state, OperationState.RECONCILING,
                               note="awaiting telemetry to resolve")
