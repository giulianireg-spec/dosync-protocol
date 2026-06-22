"""
DoSync — Composite operations (a coordinated sequence of atomic operations).
============================================================================

`operations.py` models a SINGLE long-running action: one device, one action, one
state machine (a drone's `go_to`, an oven's `bake`). That is the ATOM.

This module models the MOLECULE: a high-level goal that decomposes into an ORDERED
SEQUENCE of atomic operations, coordinated continuously by the hub. "Inspect the
area" is not one movement — it is take_off, then go_to waypoint 1, then 2, 3, 4,
then return_home, then land. A brain directing a body to walk to a point is not one
instruction; it is a continuous stream of coordinated sub-actions, each one shaped
by what the previous one actually produced.

DESIGN (validated by a multi-pass expert panel incl. a drone manufacturer, a pilot,
and two systems professors):

  * This is a SEPARATE TYPE from Operation, not an extension of it. The panel
    rejected stuffing a waypoint list into the atomic Operation: an atom is
    deliberately atomic (one device, one action), and a composite has a state
    machine of a DIFFERENT LEVEL — `planning -> in_transit -> returning ->
    completed/aborted` — with concepts (returning, abort-the-whole-sequence) that
    only make sense for a SET, never for a single atom. Two genuinely different
    state machines justify two types; it is not duplication.

  * GENERIC, not drone-specific. The panel was explicit: `inspect_area` is the
    INTENT (drone domain); the structure that executes it is generic. A robotic arm
    (reach, grip, move, release), a 3D printer (preheat, print, cool), a rover —
    all are coordinated sequences of atomic operations. The name belongs to the
    abstraction level, not the domain. So: CompositeOperation, not Mission.

  * The atom stays PURE. The atomic Operation and its reconciler are untouched.
    A CompositeOperation REFERENCES its sub-operations by their operation_id (for
    hierarchical audit-log traceability: "composite started -> [waypoint 1: op_abc
    completed] -> ... -> composite completed"), but never reaches inside their state
    machine. It coordinates; the atom executes.

  * It REUSES the carpentry of operations.py (the StateTransition record, the
    to_dict/from_dict discipline that faithfully preserves time_in_state) but has
    its OWN, simpler, mission-level state machine. Same tools, different machine.

  * SILENCE IS NOT SUCCESS — inherited. A composite only advances when its current
    sub-operation produces a positive signal (the atom reaches `completed`). It
    never advances on a timer. This is the whole point of the closed loop: the
    brain reacts to what the body actually did, waypoint by waypoint.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

from .operations import StateTransition  # reuse the atomic transition record


class CompositeState(str, Enum):
    """Mission-level lifecycle states. Deliberately distinct from (and simpler than)
    the atomic OperationState — these describe a SEQUENCE, not a single action.

    Inherits from str so values serialize cleanly to JSON / SQLite, consistent with
    the rest of DoSync's enums.
    """

    PLANNING = "planning"          # composed, sub-operations not yet started
    IN_TRANSIT = "in_transit"      # executing the ordered steps (the main phase)
    RETURNING = "returning"        # executing the final return-to-base step
    COMPLETED = "completed"        # all steps finished successfully (positive signal)
    ABORTED = "aborted"            # stopped early — a guard fired, or a step failed.
                                   # On abort, remaining steps are cancelled and the
                                   # safe default (return home) is what RETURNING is
                                   # for; a composite that cannot even return goes
                                   # straight to ABORTED.
    FAILED = "failed"              # could not complete and could not safely return


# States in which a composite is finished and will not transition further.
COMPOSITE_TERMINAL_STATES: frozenset[CompositeState] = frozenset({
    CompositeState.COMPLETED,
    CompositeState.ABORTED,
    CompositeState.FAILED,
})

# Legal mission-level transitions.
#   planning -> in_transit (start) | aborted (cancelled before takeoff) | failed
#   in_transit -> returning (steps done, head home) | aborted (guard/failure mid-run)
#                 | failed (cannot continue nor return)
#   returning -> completed (home reached) | failed (could not get home)
#   terminals: no outgoing transitions
_ALLOWED_COMPOSITE_TRANSITIONS: dict[CompositeState, frozenset[CompositeState]] = {
    CompositeState.PLANNING: frozenset({
        CompositeState.IN_TRANSIT,
        CompositeState.ABORTED,
        CompositeState.FAILED,
    }),
    CompositeState.IN_TRANSIT: frozenset({
        CompositeState.RETURNING,
        CompositeState.ABORTED,
        CompositeState.FAILED,
    }),
    CompositeState.RETURNING: frozenset({
        CompositeState.COMPLETED,
        CompositeState.ABORTED,   # return interrupted (e.g. human took control)
        CompositeState.FAILED,
    }),
    CompositeState.COMPLETED: frozenset(),
    CompositeState.ABORTED: frozenset(),
    CompositeState.FAILED: frozenset(),
}


class InvalidCompositeTransition(Exception):
    """Raised when a mission-level state transition is not permitted."""


@dataclass
class CompositeStep:
    """One step in a composite: a single atomic action to dispatch, plus the
    operation_id of the atomic Operation once it is created (for hierarchical
    traceability). `kind` labels the step's role in the sequence so the supervisor
    and the audit log can reason about it without parsing params.
    """
    device_id: str
    action: str                              # e.g. "take_off", "go_to", "return_home"
    params: dict = field(default_factory=dict)
    kind: str = "step"                       # "takeoff" | "waypoint" | "return" | "step"
    operation_id: str | None = None          # set when the atomic op is created
    done: bool = False                       # set when this step reaches a terminal atom state

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "action": self.action,
            "params": self.params,
            "kind": self.kind,
            "operation_id": self.operation_id,
            "done": self.done,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CompositeStep":
        return cls(
            device_id=d["device_id"],
            action=d["action"],
            params=d.get("params", {}),
            kind=d.get("kind", "step"),
            operation_id=d.get("operation_id"),
            done=d.get("done", False),
        )


@dataclass
class CompositeOperation:
    """A high-level operation coordinating an ordered sequence of atomic operations.

    The hub creates one when a composing intent (e.g. inspect_area) resolves into a
    multi-step plan. The supervisor (separate component) drives it forward: it
    dispatches the current step, waits for that step's atomic operation to reach a
    positive terminal state, advances the index, and repeats — reacting in real time
    to telemetry, never on a timer.
    """
    device_id: str                            # the primary device (e.g. the drone)
    intent: str                               # the originating intent (e.g. "inspect_area")
    steps: list[CompositeStep]
    composite_id: str = field(default_factory=lambda: f"comp_{uuid.uuid4().hex[:12]}")
    state: CompositeState = CompositeState.PLANNING
    current_index: int = 0                    # which step is active
    created_at: float = field(default_factory=time.time)
    state_entered_at: float = field(default_factory=time.time)
    history: list[StateTransition] = field(default_factory=list)
    context: dict = field(default_factory=dict)  # e.g. geofence center/radius, altitude

    def __post_init__(self):
        if isinstance(self.state, str):
            self.state = CompositeState(self.state)
        if not self.history:
            # Reuse the atomic StateTransition record; from_state None = initial entry.
            self.history.append(StateTransition(
                from_state=None, to_state=self.state,  # type: ignore[arg-type]
                at=self.created_at, reason="composed",
            ))

    # ── State ────────────────────────────────────────────────────────────────
    @property
    def is_terminal(self) -> bool:
        return self.state in COMPOSITE_TERMINAL_STATES

    def time_in_state(self, now: float | None = None) -> float:
        """Seconds spent in the current mission-level state. First-class data for a
        guard/supervisor to reason about (this module never acts on it itself)."""
        return (now if now is not None else time.time()) - self.state_entered_at

    def can_transition_to(self, target: CompositeState) -> bool:
        if isinstance(target, str):
            target = CompositeState(target)
        return target in _ALLOWED_COMPOSITE_TRANSITIONS.get(self.state, frozenset())

    def transition_to(self, target: CompositeState, reason: str = "",
                      now: float | None = None) -> StateTransition:
        """Advance the mission-level state, recording the transition. Raises
        InvalidCompositeTransition on an illegal move — a finished mission does not
        resume, and an illegal jump is never silently accepted."""
        if isinstance(target, str):
            target = CompositeState(target)
        ts = now if now is not None else time.time()

        if self.is_terminal:
            raise InvalidCompositeTransition(
                f"composite {self.composite_id} is terminal ({self.state.value}); "
                f"cannot transition to {target.value}."
            )
        if target not in _ALLOWED_COMPOSITE_TRANSITIONS.get(self.state, frozenset()):
            raise InvalidCompositeTransition(
                f"illegal composite transition {self.state.value} -> {target.value} "
                f"for {self.composite_id}."
            )

        transition = StateTransition(from_state=self.state, to_state=target,  # type: ignore[arg-type]
                                     at=ts, reason=reason)
        self.history.append(transition)
        self.state = target
        self.state_entered_at = ts
        return transition

    # ── Step coordination (the supervisor uses these; they hold no policy) ─────
    @property
    def current_step(self) -> CompositeStep | None:
        """The step currently being executed, or None if the index is past the end."""
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return None

    @property
    def remaining_steps(self) -> list[CompositeStep]:
        """Steps not yet started (from current_index onward). Used on abort to know
        what must be cancelled."""
        return self.steps[self.current_index:]

    def advance(self) -> CompositeStep | None:
        """Mark the current step done and move to the next. Returns the new current
        step, or None if the sequence is exhausted. Pure bookkeeping — the supervisor
        decides WHETHER to advance (only on a positive signal); this just moves the
        cursor."""
        step = self.current_step
        if step is not None:
            step.done = True
        self.current_index += 1
        return self.current_step

    @property
    def all_steps_done(self) -> bool:
        return all(s.done for s in self.steps) if self.steps else True

    # ── Persistence (reuses the to_dict/from_dict discipline of operations.py) ──
    def to_dict(self) -> dict:
        return {
            "composite_id": self.composite_id,
            "device_id": self.device_id,
            "intent": self.intent,
            "state": self.state.value,
            "current_index": self.current_index,
            "created_at": self.created_at,
            "state_entered_at": self.state_entered_at,
            "context": self.context,
            "steps": [s.to_dict() for s in self.steps],
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
    def from_dict(cls, data: dict) -> "CompositeOperation":
        """Rehydrate from a persisted dict — the inverse of to_dict(). Fidelity
        matters: restore the real state, the real state_entered_at, and the full
        history, exactly as operations.py does for the atom. A naive constructor call
        would let __post_init__ synthesize a fresh history and reset state_entered_at
        to now, destroying time_in_state — the signal a guard relies on to spot a
        stalled mission. So build, then overwrite with the stored values."""
        comp = cls(
            device_id=data["device_id"],
            intent=data["intent"],
            steps=[CompositeStep.from_dict(s) for s in data.get("steps", [])],
            composite_id=data["composite_id"],
            state=CompositeState(data["state"]),
            current_index=data.get("current_index", 0),
            created_at=data["created_at"],
            context=data.get("context", {}),
        )
        comp.state_entered_at = data["state_entered_at"]
        comp.history = [
            StateTransition(
                from_state=CompositeState(h["from_state"]) if h["from_state"] else None,  # type: ignore[arg-type]
                to_state=CompositeState(h["to_state"]),  # type: ignore[arg-type]
                at=h["at"],
                reason=h.get("reason", ""),
            )
            for h in data.get("history", [])
        ]
        return comp
