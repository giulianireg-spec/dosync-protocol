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
    assert CertReport.EXPECTED_COUNTS["conformance"] == 56
    for tier, n in CertReport.EXPECTED_COUNTS.items():
        assert n > 0, f"{tier} declares no expected count"


def test_an_aborted_run_does_not_certify():
    """The dangerous case: stopping before any check fails would otherwise look
    like a clean sweep."""
    r = _report(passed=5, failed=0)
    assert r.incomplete is True
    assert r.certified is False, \
        "five green checks out of fifty-six is not a certification"


def test_an_aborted_run_is_not_reported_as_a_failure():
    """The case that actually happened. `failed` was 1, and the verdict blamed
    the hub for a suite that never started."""
    r = _report(passed=4, failed=1)
    assert r.incomplete is True
    assert r.executed == 5 and r.expected == 56


def test_a_complete_run_with_failures_is_a_real_failure():
    """And the distinction must not swallow genuine failures — an incomplete
    flag that hides real problems would be worse than the bug it fixed."""
    r = _report(passed=51, failed=5)
    assert r.incomplete is False
    assert r.certified is False


def test_a_complete_clean_run_certifies():
    r = _report(passed=56, failed=0)
    assert r.incomplete is False and r.certified is True


def test_the_signed_report_carries_the_distinction():
    """A third party reading the file must be able to tell the two apart, and
    the signature must cover it — otherwise an incomplete run could be presented
    as a clean one."""
    r = _report(passed=4, failed=1)
    d = r.to_dict()
    assert d["incomplete"] is True
    assert d["executed"] == 5 and d["expected"] == 56
    assert d["certified"] is False
