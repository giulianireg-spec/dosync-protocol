"""Formal claim state machine validation (spec §3.1, panel #5, 2026-07-21).

The consistency model documents a four-state FSM for per-device claims
(ABSENT/HELD/RELEASING/EXPIRED). These tests pin the FSM directly against the
_Claim implementation — so the formal model in the spec cannot drift from the
code — and assert that every invariant I1–I6 named in the spec is backed by a
real, present test.
"""
import re
from pathlib import Path

import pytest

from dosync.device_arbiter import _Claim

REPO = Path(__file__).resolve().parent.parent


def _claim(set_at=100.0, grace=3.0, max_hold=30.0):
    return _Claim(rank=3, urgency="emergency", set_at=set_at, grace=grace,
                  max_hold=max_hold)


# ── The four states, exactly as the spec predicates define them ──────────────

def test_HELD_state_active_until_max_hold():
    c = _claim(set_at=100.0, max_hold=30.0)
    assert c.released_at is None
    assert c.is_active(100.0) is True        # just set
    assert c.is_active(129.9) is True        # within max_hold
    assert c.is_active(130.0) is False       # EXPIRED at the cap (I2)


def test_RELEASING_state_active_for_grace_only():
    c = _claim(set_at=100.0, grace=3.0)
    c.release(105.0)
    assert c.released_at == 105.0
    assert c.is_active(107.9) is True         # within grace
    assert c.is_active(108.0) is False        # EXPIRED after grace (I3)


def test_release_is_idempotent_cannot_extend_grace():
    """release() sets released_at once; a second call cannot push the grace out."""
    c = _claim(set_at=100.0, grace=3.0)
    c.release(105.0)
    c.release(200.0)                          # must be ignored
    assert c.released_at == 105.0
    assert c.is_active(108.0) is False


def test_grace_is_bounded_ownership_not_fixed_duration():
    """I3: after release, ownership lasts `grace` from the RELEASE, not from set_at."""
    early = _claim(set_at=100.0, grace=3.0, max_hold=30.0)
    early.release(101.0)                      # released almost immediately
    assert early.is_active(103.9) is True     # grace runs from 101, not from 100
    assert early.is_active(104.0) is False


def test_max_hold_caps_an_unreleased_claim():
    """I2: a claim never released still expires at the safety cap — no permanent lock."""
    c = _claim(set_at=100.0, max_hold=30.0)
    assert c.is_active(130.0) is False


# ── The invariants named in the spec are each backed by a present test ───────

def test_every_named_invariant_has_a_backing_test():
    """The spec §3.1 lists invariants I1–I6, each annotated with the test that
    would fail if violated. This meta-test verifies those named tests actually
    exist — the formal model cannot cite tests that aren't there."""
    spec = (REPO / "spec" / "CONSISTENCY-MODEL.md").read_text()
    cited = set(re.findall(r"\(test_[a-z0-9_, \n]+\)", spec))
    named_tests = set()
    for group in cited:
        named_tests |= set(re.findall(r"test_[a-z0-9_]+", group))
    assert named_tests, "spec cites no backing tests — formal model unmoored"

    preempt = (REPO / "tests" / "test_emergency_preemption.py").read_text()
    for t in named_tests:
        assert t in preempt, f"spec §3.1 cites {t} but it is not in test_emergency_preemption.py"


def test_spec_documents_all_four_states():
    spec = (REPO / "spec" / "CONSISTENCY-MODEL.md").read_text()
    for state in ("ABSENT", "HELD", "RELEASING", "EXPIRED"):
        assert f"`{state}`" in spec, f"state {state} not documented in §3.1"


def test_spec_records_the_two_emergency_open_edge():
    """The Benítez edge case must be recorded, not silently omitted."""
    spec = (REPO / "spec" / "CONSISTENCY-MODEL.md").read_text()
    assert "two emergencies" in spec.lower() or "two-emergency" in spec.lower()
