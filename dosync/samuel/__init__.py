"""SAMUEL — the governance kernel of DoSync.

Standard for Agent Mediation Under Enforceable Limits (spec/SAMUEL-SPEC-v0.2.md):
what an AI agent may do to physical devices, within limits the operator sets and
the hub enforces -- in a home, a plant, a ward, a fleet of drones alike.

If the agent is the mind and the devices are the body, DoSync is the nervous
system between them, and this package is the part of it that decides what may
pass. The mind proposes; something deterministic decides what runs. A proposal
may come from an agent (governed direct mode) or from DoSync's own resolver --
resolution is one more way of proposing, and it lives outside this package. Every
proposal, whoever made it, crosses the same rules here.

What the kernel decides, and where:

  intake.admit()                 an intent as a whole: its class grants it,
                                 its place exists, its actions are unambiguous
                                 (G2, G3, G4; refusals are G6)
  proposals.validate_proposals() each proposed action: declared, granted,
                                 placed, declared-first in an emergency, no
                                 opposite pair (G1-G5)
  oscillation.OscillationGuard   no device reversed more than allowed within a
                                 window, across intents (G4)
  confirmation.split_by_confirmation()
                                 what the operator marked waits for a person,
                                 the rest runs (G9)

What it does NOT do: choose devices, execute anything, talk HTTP, touch a
database, or know any adapter. It reads plain values and returns decisions; the
hub records them (G6), applies the operator's policy engine (G7) and keeps the
side door shut (G8). tests/test_samuel_kernel_boundary.py fails if a module here
imports anything of DoSync beyond its data models -- that boundary is what lets
SAMUEL be implemented without DoSync, in another language, from these files.
"""
from .intake import Admission, INTENT_REFUSALS, admit
from .proposals import PROPOSAL_REFUSALS, ClassGrant, validate_proposals
from .oscillation import OscillationGuard
from .confirmation import split_by_confirmation

__all__ = [
    "Admission", "INTENT_REFUSALS", "admit",
    "PROPOSAL_REFUSALS", "ClassGrant", "validate_proposals",
    "OscillationGuard", "split_by_confirmation",
]
