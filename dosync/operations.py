"""
DoSync — Long-running operation lifecycle.
==========================================

Most DoSync actions are instantaneous: send the command, get a result, done.
That is the `instant` execution model and it is unchanged by this module.

Some actions take time and pass through states while they run — "fly to this
waypoint" arms, takes off, navigates, and only then arrives. A network timeout
is not the same as a failure; a pilot grabbing the sticks is not a failure at
all. This module models that lifecycle. It is the foundation the drone adapter
will build on, but it knows nothing about drones, MAVLink, or any hardware —
it is pure state-machine logic, fully unit-testable on its own.

DESIGN (validated by a two-pass expert panel incl. a drone manufacturer and a
real pilot):

  * The HUB owns operation state, not the device. The device reports raw facts
    ("arrived", "failed", "pilot took control"); the hub models the lifecycle.
    The device remains the source of truth for its *real* state — the hub keeps
    a replica reconciled against telemetry and never assumes. (Reconciliation
    itself lives in a later layer; this module defines the states and the legal
    transitions between them.)

  * The state machine is HIERARCHICAL, not flat:
      - A minimal CORE every long-running device satisfies:
            pending -> in_progress -> completed | failed
      - First-class added states (always available):
            rejected     — the device refused by its physical state (not armed,
                           no GPS, failsafe active). "Can't even start" — distinct
                           from failing partway through.
            cancelled    — explicit cancellation (only if supports_cancel).
            interrupted  — human intervention (a pilot takes the sticks). NOT a
                           failure: a normal, expected outcome. Does NOT resume —
                           if the system retakes control it is a NEW operation.
      - Optional SUB-STATES, only meaningful for richer profiles:
            preparing           — a pre-action setup phase, made visible instead of
                                  hidden inside an opaque in_progress. Universal: a
                                  camera focusing, a blind releasing its lock, an
                                  aerial vehicle arming/taking off. The device-
                                  specific sub-phase is carried in Operation.phase
                                  (the protocol understands `preparing`; a domain
                                  profile specializes it).
            paused_by_device    — the device paused *itself*. Universal: a speaker
                                  whose stream dropped, a camera whose storage filled,
                                  an aerial vehicle holding position in wind. Neither
                                  failed, nor interrupted, nor advancing.
            reconciling         — transient state after a hub restart: the hub
                                  reconciles against telemetry before assuming
                                  anything. Generic; its criticality varies by domain.
    A simple device (an oven) only ever sees the core. A speaker adds cancellation;
    a camera adds preparing; an aerial vehicle uses all of it. Each device declares
    how rich it is — the names belong to the protocol, the domain detail to `phase`.

  * SILENCE IS NOT SUCCESS. Reaching `completed` requires a positive signal, never
    the mere absence of an error or a timeout. This is the opposite of the instant
    model and is non-negotiable where the error is physical.

  * Time-in-state is first-class data: every state records when it was entered,
    so a Policy Engine (separate component) can decide what to do about a state
    that has lasted too long. This module exposes the timing; it does NOT decide
    policy.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class OperationState(str, Enum):
    """Lifecycle states of a long-running operation.

    Inherits from str so values serialize cleanly to JSON / SQLite and compare
    against plain strings, consistent with the rest of DoSync's enums.
    """

    # ── Core (every long_running device) ──────────────────────────────────────
    PENDING = "pending"            # accepted by the hub, not yet confirmed started
    IN_PROGRESS = "in_progress"    # confirmed underway (positive signal received)
    COMPLETED = "completed"        # confirmed finished successfully (positive signal)
    FAILED = "failed"              # failed partway through

    # ── First-class added states (always available) ──────────────────────────
    REJECTED = "rejected"          # device refused by physical state — never started
    CANCELLED = "cancelled"        # explicit cancellation
    INTERRUPTED = "interrupted"    # human intervention — normal outcome, does not resume

    # ── Optional sub-states (richer profiles only) ────────────────────────────
    PREPARING = "preparing"        # pre-action setup phase (camera focusing, blind
                                   # releasing its lock, a drone arming/taking off).
                                   # Device-specific sub-phase carried in Operation.phase.
    PAUSED_BY_DEVICE = "paused_by_device"  # the device paused itself (speaker lost its
                                   # stream, camera storage full, drone holding in wind)
    RECONCILING = "reconciling"    # transient: hub reconciling against telemetry after restart


# States in which an operation is finished and will not transition further.
TERMINAL_STATES: frozenset[OperationState] = frozenset({
    OperationState.COMPLETED,
    OperationState.FAILED,
    OperationState.REJECTED,
    OperationState.CANCELLED,
    OperationState.INTERRUPTED,
})

# Sub-states that only apply to telemetry-capable profiles. A core-only device
# (declares long_running without emits_telemetry) must never be driven into one.
TELEMETRY_ONLY_STATES: frozenset[OperationState] = frozenset({
    OperationState.PREPARING,
    OperationState.PAUSED_BY_DEVICE,
    OperationState.RECONCILING,
})

# Legal transitions. Key = from-state, value = set of allowed next states.
# Encodes the panel's rules:
#   - core flow pending -> in_progress -> completed/failed
#   - rejected only from pending (can't even start)
#   - interrupted/failed/cancelled reachable from any active (non-terminal) state
#   - terminal states have no outgoing transitions (interrupted does NOT resume)
#   - sub-state (preparing) sits between pending and in_progress
#   - paused_by_device is reachable from active states and can resume
#   - reconciling can reach any non-pending outcome once telemetry clarifies
_ALLOWED_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.PENDING: frozenset({
        OperationState.PREPARING,
        OperationState.IN_PROGRESS,
        OperationState.REJECTED,
        OperationState.CANCELLED,
        OperationState.INTERRUPTED,
        OperationState.FAILED,
        OperationState.RECONCILING,   # recovered on restart while pending
    }),
    OperationState.PREPARING: frozenset({
        OperationState.IN_PROGRESS,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.INTERRUPTED,
        OperationState.REJECTED,
        OperationState.RECONCILING,   # recovered on restart while preparing
    }),
    OperationState.IN_PROGRESS: frozenset({
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.INTERRUPTED,
        OperationState.PAUSED_BY_DEVICE,
        OperationState.RECONCILING,   # recovered on restart while in progress
    }),
    OperationState.PAUSED_BY_DEVICE: frozenset({
        OperationState.IN_PROGRESS,   # device resumes on its own (wind eased, stream back)
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.INTERRUPTED,
        OperationState.RECONCILING,   # recovered on restart while paused
    }),
    OperationState.RECONCILING: frozenset({
        OperationState.IN_PROGRESS,
        OperationState.PAUSED_BY_DEVICE,
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.INTERRUPTED,
        OperationState.CANCELLED,
    }),
    # Terminal states: no outgoing transitions.
    OperationState.COMPLETED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.REJECTED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.INTERRUPTED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when a state transition is not permitted by the state machine."""


@dataclass
class StateTransition:
    """A single recorded transition — the audit trail of an operation's life."""
    from_state: OperationState | None   # None for the initial entry into PENDING
    to_state: OperationState
    at: float                           # unix timestamp
    reason: str = ""                    # human-readable cause (e.g. "telemetry: arrived")


@dataclass
class Operation:
    """A long-running operation tracked by the hub.

    The hub creates one of these when a long_running action begins, then advances
    it through the state machine as positive signals arrive. The device is the
    source of truth; this object is the hub's reconciled replica.
    """
    device_id: str
    action: str
    operation_id: str = field(default_factory=lambda: f"op_{uuid.uuid4().hex[:12]}")
    state: OperationState = OperationState.PENDING
    created_at: float = field(default_factory=time.time)
    state_entered_at: float = field(default_factory=time.time)
    history: list[StateTransition] = field(default_factory=list)
    # Whether the device backing this operation emits telemetry. Gates the
    # richer sub-states so a core-only device can't be driven into them.
    telemetry_capable: bool = False
    # Optional domain-specific sub-phase detail, meaningful while in PREPARING.
    # The protocol understands the generic PREPARING state; a domain profile can
    # specialize it — e.g. an aerial vehicle sets phase="arming" then "taking_off",
    # a camera "focusing". The protocol never interprets this string; the device
    # and its operator do. Keeps the core universal while preserving granularity.
    phase: str = ""

    def __post_init__(self):
        if isinstance(self.state, str):
            self.state = OperationState(self.state)
        # Record the initial entry into the starting state.
        if not self.history:
            self.history.append(StateTransition(
                from_state=None, to_state=self.state,
                at=self.created_at, reason="created",
            ))

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def time_in_state(self, now: float | None = None) -> float:
        """Seconds spent in the current state. First-class data for the Policy
        Engine to reason about (this module never acts on it itself)."""
        return (now if now is not None else time.time()) - self.state_entered_at

    def can_transition_to(self, target: OperationState) -> bool:
        """Whether `target` is a legal next state from the current one, honoring
        both the transition table and the telemetry gate."""
        if isinstance(target, str):
            target = OperationState(target)
        if target in TELEMETRY_ONLY_STATES and not self.telemetry_capable:
            return False
        return target in _ALLOWED_TRANSITIONS.get(self.state, frozenset())

    def transition_to(self, target: OperationState, reason: str = "",
                      now: float | None = None) -> "StateTransition":
        """Advance to `target`, recording the transition. Raises InvalidTransition
        if the move is not permitted — silence is never success, and an illegal
        jump is never silently accepted."""
        if isinstance(target, str):
            target = OperationState(target)
        ts = now if now is not None else time.time()

        if self.is_terminal:
            raise InvalidTransition(
                f"operation {self.operation_id} is terminal ({self.state.value}); "
                f"cannot transition to {target.value}. "
                f"(An interrupted/finished operation does not resume — start a new one.)"
            )
        if target in TELEMETRY_ONLY_STATES and not self.telemetry_capable:
            raise InvalidTransition(
                f"state {target.value} requires a telemetry-capable device; "
                f"operation {self.operation_id} backs a core-only device."
            )
        if target not in _ALLOWED_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidTransition(
                f"illegal transition {self.state.value} -> {target.value} "
                f"for operation {self.operation_id}."
            )

        transition = StateTransition(from_state=self.state, to_state=target,
                                     at=ts, reason=reason)
        self.history.append(transition)
        self.state = target
        self.state_entered_at = ts
        return transition

    def to_dict(self) -> dict:
        """Serialize for persistence / API. Mirrors the project's dataclass style."""
        return {
            "operation_id": self.operation_id,
            "device_id": self.device_id,
            "action": self.action,
            "state": self.state.value,
            "created_at": self.created_at,
            "state_entered_at": self.state_entered_at,
            "telemetry_capable": self.telemetry_capable,
            "phase": self.phase,
            "history": [
                {
                    "from_state": t.from_state.value if t.from_state else None,
                    "to_state": t.to_state.value,
                    "at": t.at,
                    "reason": t.reason,
                }
                for t in self.history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Operation":
        """Rehydrate an Operation from its persisted dict — the inverse of
        to_dict(). Used when telemetry arrives for an operation that lives in the
        DB and must be reconciled against.

        Fidelity matters here: we restore the real state, the real
        state_entered_at, and the full transition history. A naive
        Operation(state=...) would let __post_init__ synthesize a fresh history
        and reset state_entered_at to *now*, silently destroying time_in_state —
        the very signal the Policy Engine relies on to spot a stalled operation.
        So we build the object, then overwrite those fields with the stored ones.
        """
        op = cls(
            device_id=data["device_id"],
            action=data["action"],
            operation_id=data["operation_id"],
            state=OperationState(data["state"]),
            created_at=data["created_at"],
            telemetry_capable=data.get("telemetry_capable", False),
            phase=data.get("phase", ""),
        )
        # Restore the true timing and history (the constructor reset/synthesized
        # these — replace them with what actually happened).
        op.state_entered_at = data["state_entered_at"]
        op.history = [
            StateTransition(
                from_state=OperationState(h["from_state"]) if h["from_state"] else None,
                to_state=OperationState(h["to_state"]),
                at=h["at"],
                reason=h.get("reason", ""),
            )
            for h in data.get("history", [])
        ]
        return op
