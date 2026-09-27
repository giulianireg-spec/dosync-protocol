"""A certification suite owes the same honesty it demands (2026-08-02).

An empty `DOSYNC_TOKEN` made device registration fail, the run aborted after 5
of 56 checks, and the report said **"NOT CERTIFIED — 1 test(s) failed"**. An
operator reading that concludes their hub failed conformance. It was never
tested.

Worse: a run that aborted BEFORE reaching any failing check would have reported
zero failures and certified — on almost no evidence.

This is the distinction the protocol insists on everywhere else. `unverifiable`
is not `contradicted`: one says the device disagreed, the other says we could not
look. "Not searchable" is not "found nothing". A suite that cannot tell "your hub
is wrong" from "I never ran" fails its own standard.
"""
from dosync.certify import CertReport
from dosync.certify import TestResult as _CheckResult  # aliased: pytest tries to
# collect anything named Test* as a test class, and warns that it cannot because
# of the constructor. The suite is kept at zero warnings, and renaming a public
# class of the certification module to satisfy a test runner would be the tail
# wagging the dog.


FULL = CertReport.EXPECTED_COUNTS["conformance"]


def _report(tier="conformance", passed=0, failed=0):
    r = CertReport(host="h", port=47200, tier=tier)
    for i in range(passed):
        r.add(_CheckResult(f"P{i}", True, ""))
    for i in range(failed):
        r.add(_CheckResult(f"F{i}", False, ""))
    r.finalize()
    return r


def test_every_tier_declares_how_many_checks_it_runs():
    """Without an expected count there is nothing to compare against, and an
    aborted run is indistinguishable from a complete one."""
    # Not pinned to a number: every new check would force an edit here, and the
    # counts are proven where it matters -- CI runs every tier against a live
    # hub, which is how basic's 12-for-10 was found. Cumulative tiers only grow.
    counts = CertReport.EXPECTED_COUNTS
    assert counts["basic"] < counts["standard"] < counts["emergency"] < counts["conformance"]
    for tier, n in counts.items():
        assert n > 0, f"{tier} declares no expected count"


def test_an_aborted_run_does_not_certify():
    """The dangerous case: stopping before any check fails would otherwise look
    like a clean sweep."""
    r = _report(passed=5, failed=0)
    assert r.incomplete is True
    assert r.certified is False, \
        f"five green checks out of {FULL} is not a certification"


def test_an_aborted_run_is_not_reported_as_a_failure():
    """The case that actually happened. `failed` was 1, and the verdict blamed
    the hub for a suite that never started."""
    r = _report(passed=4, failed=1)
    assert r.incomplete is True
    assert r.executed == 5 and r.expected == FULL


def test_a_complete_run_with_failures_is_a_real_failure():
    """And the distinction must not swallow genuine failures — an incomplete
    flag that hides real problems would be worse than the bug it fixed."""
    r = _report(passed=FULL - 5, failed=5)
    assert r.incomplete is False
    assert r.certified is False


def test_a_complete_clean_run_certifies():
    r = _report(passed=FULL, failed=0)
    assert r.incomplete is False and r.certified is True


def test_the_signed_report_carries_the_distinction():
    """A third party reading the file must be able to tell the two apart, and
    the signature must cover it — otherwise an incomplete run could be presented
    as a clean one."""
    r = _report(passed=4, failed=1)
    d = r.to_dict()
    assert d["incomplete"] is True
    assert d["executed"] == 5 and d["expected"] == FULL
    assert d["certified"] is False


# ── Not applicable (protocol 0.5) ─────────────────────────────────────────────
# A check that must fire a real emergency (C21) cannot run safely against a
# production hub. Counting it as passed would claim what nobody checked; as
# failed, every production hub would be uncertifiable. It is recorded as not
# applicable, with its reason, and the tier's expected count drops by one.

def _unfinalized(passed):
    r = CertReport(host="h", port=47200, tier="conformance")
    for i in range(passed):
        r.add(_CheckResult(f"P{i}", True, ""))
    return r


def test_a_not_applicable_check_lowers_what_is_expected():
    r = _unfinalized(FULL - 1)
    r.not_applicable.append(("C21  emergency", "fires a real emergency"))
    r.finalize()
    assert r.expected == FULL - 1 and not r.incomplete and r.certified


def test_without_it_the_same_run_is_incomplete():
    r = _unfinalized(FULL - 1)
    r.finalize()
    assert r.incomplete and not r.certified


def test_the_signed_report_lists_what_was_not_applicable():
    a, b = _unfinalized(FULL - 1), _unfinalized(FULL - 1)
    b.timestamp = a.timestamp
    a.not_applicable.append(("C21  emergency", "fires a real emergency"))
    a.finalize(); b.finalize()
    assert a.to_dict()["not_applicable"] == [
        {"check": "C21  emergency", "reason": "fires a real emergency"}]
    assert a.fingerprint != b.fingerprint, "the signature does not cover what was not run"
