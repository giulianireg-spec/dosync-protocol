"""H2 — can a cached reading verify an action? (2026-08-01)

The horizon item asked the question rather than assuming an answer: "first
someone must answer whether a CACHED reading is independent observation at all,
and what freshness bound keeps that honest. A panel question, not a default to
slide into."

The panel's answer was that they are not the same thing. A reading the hub asks
for after acting is causally posterior to the action. A reading the device
happened to send may predate it — and then it describes the world before we did
anything. If it arrived AFTER dispatch and recently, it is evidence: weaker,
because we did not ask for it, but evidence.

And a finding that reordered the work: `update_state()` stored values with no
arrival time at all, so the freshness bound H2 proposed was unimplementable —
not by design, but because the data was not being retained.
"""
import asyncio
import time

import pytest

from dosync.hub import DoSyncHub, _TimedExecutor
from dosync.models import (DeviceAction, VerificationStatus, VerifyBinding)


class _NoPollExecutor:
    """Stands in for an executor whose adapters cannot be polled — the MQTT and
    GPIO case, which is the entire reason H2 exists."""

    async def execute(self, action, urgency):
        raise AssertionError("not used by these tests")


@pytest.fixture
def verifier():
    hub = DoSyncHub(db_path=":memory:")
    ex = _TimedExecutor(_NoPollExecutor())
    ex._hub = hub
    return hub, ex


def _action(dispatched_at=None):
    a = DeviceAction(device_id="lamp", action="turn_on", params={})
    a.dispatched_at = dispatched_at if dispatched_at is not None else time.time()
    return a


# ── The prerequisite ────────────────────────────────────────────────────────

def test_pushed_readings_are_stamped_with_arrival_time():
    """Nothing else in this file is possible without it, and until now the cache
    held values with no age."""
    hub = DoSyncHub(db_path=":memory:")
    before = time.time()
    hub.resolver.update_state("pir", {"motion": True})

    stamped = hub.resolver.reading_age("pir", "motion")
    assert stamped is not None and stamped >= before
    assert hub.resolver.reading_age("pir", "never-reported") is None


def test_stamping_does_not_pollute_the_state_itself():
    """A stamp appearing beside a sensor would show up as a reading to anything
    that iterates state."""
    hub = DoSyncHub(db_path=":memory:")
    hub.resolver.update_state("pir", {"motion": True})
    assert list(hub.resolver._state_cache["pir"].keys()) == ["motion"]


# ── Opt-in: the default must not change ─────────────────────────────────────

def test_without_opt_in_the_behaviour_is_unchanged(verifier):
    """No global window is correct (panel, Aguirre): an ambient thermometer
    reporting every five minutes is fine, a door sensor reporting every five
    minutes is useless for confirming a lock. Absent a declaration, the honest
    answer stays `unverifiable`."""
    hub, ex = verifier
    # The reading must arrive AFTER dispatch, or the timing guard rejects it
    # anyway and this test passes for the wrong reason — it would prove the
    # timing check works, not the opt-in. (Caught by deleting the opt-in check
    # and watching this stay green.)
    action = _action()
    hub.resolver.update_state("pir", {"motion": True})
    assert hub.resolver.reading_age("pir", "motion") >= action.dispatched_at

    binding = VerifyBinding(sensor_id="pir:motion", expected_reading=True)
    result = asyncio.run(ex._verify_action(action, binding))

    assert result.status == VerificationStatus.UNVERIFIABLE,         "a perfectly good pushed reading must still be ignored without opt-in"
    assert result.evidence == "polled"


# ── The window is measured against the ACTION, not the clock ────────────────

def test_a_reading_that_predates_the_action_does_not_verify(verifier):
    """The condition that is easy to get wrong. A reading from five seconds ago
    is fresh by any clock — and if the action was dispatched four seconds ago,
    it describes the world before we acted and confirms nothing."""
    hub, ex = verifier
    hub.resolver.update_state("pir", {"motion": True})
    hub.resolver._state_stamps["pir"]["motion"] = time.time() - 5

    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    result = asyncio.run(ex._verify_action(_action(dispatched_at=time.time()), binding))

    assert result.status != VerificationStatus.VERIFIED, \
        "a reading older than the action is evidence of nothing"


def test_a_reading_after_dispatch_and_in_window_verifies(verifier):
    hub, ex = verifier
    action = _action()
    hub.resolver.update_state("pir", {"motion": True})   # arrives after dispatch

    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    result = asyncio.run(ex._verify_action(action, binding))

    assert result.status == VerificationStatus.VERIFIED
    assert result.observed is True


def test_a_reading_outside_the_window_does_not_verify(verifier):
    hub, ex = verifier
    action = _action()
    hub.resolver.update_state("pir", {"motion": True})
    hub.resolver._state_stamps["pir"]["motion"] = time.time() - 120

    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    assert asyncio.run(ex._verify_action(action, binding)).status \
        != VerificationStatus.VERIFIED


def test_a_pushed_reading_can_contradict(verifier):
    """Verification is not a rubber stamp: a pushed reading that disagrees is a
    contradiction, exactly as a polled one would be."""
    hub, ex = verifier
    action = _action()
    hub.resolver.update_state("pir", {"motion": False})

    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    result = asyncio.run(ex._verify_action(action, binding))
    assert result.status == VerificationStatus.CONTRADICTED


# ── `verified` must not mean two different things ───────────────────────────

def test_pushed_evidence_is_labelled_as_such(verifier):
    """Torres, on the panel: if a cached reading produces plain `verified`, we
    are claiming we confirmed with the same force as a direct query. We did not,
    and an auditor must be able to tell without reading the code."""
    hub, ex = verifier
    action = _action()
    hub.resolver.update_state("pir", {"motion": True})

    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    result = asyncio.run(ex._verify_action(action, binding))

    assert result.evidence == "pushed"
    assert result.observed_at is not None, \
        "when the evidence arrived is part of what the evidence is"


# ── Silence is not the same as absence ──────────────────────────────────────

def test_a_silent_change_reporting_sensor_is_not_a_failure(verifier):
    """Kim, on the panel: a sensor that publishes ON CHANGE and did not publish
    because nothing changed is healthy. Reporting it as `unverifiable` sends an
    operator hunting a broken sensor that is working."""
    hub, ex = verifier
    binding = VerifyBinding("pir:motion", True, accept_cached_within_s=30)
    result = asyncio.run(ex._verify_action(_action(), binding))

    assert result.status == VerificationStatus.NO_CHANGE_REPORTED
    assert result.status != VerificationStatus.UNVERIFIABLE


def test_the_four_original_statuses_still_mean_what_they_meant():
    """A fifth status must not blur the four that already carried meaning."""
    values = {s.value for s in VerificationStatus}
    assert {"unverified", "verified", "contradicted", "unverifiable"} <= values
    assert len({s for s in VerificationStatus}) == 5
