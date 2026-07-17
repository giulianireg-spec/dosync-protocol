"""Post-policy mode of the recall benchmark (2026-07-17).

The pre-policy number measures the semantic layer against truthfully-declared
capabilities; the post-policy number measures what THE DEPLOYMENT executes after
its own policy file. They answer different questions — quoting one as the other
is how the 0.49 mistake happened — so the tool reports both and their delta.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import recall_benchmark as rb          # noqa: E402

from dosync.policy_config import PolicyConfigError   # noqa: E402


# The tests run against the repo's OWN fixture registry — the known-good one the
# shipped benchmark scores 1.0 on — rather than a hand-made mini registry. (The
# first version of this file invented devices whose actuator types the notify
# resolution did not match, so the pre-policy plan was already wrong and the
# tests measured the fixture's mistakes, not the tool.)
FIXTURE_REGISTRY = (Path(__file__).resolve().parent.parent
                    / "benchmarks" / "fixtures" / "recall_registry.json")


def _files(tmp_path, truth_cases, policies=None):
    reg = FIXTURE_REGISTRY
    truth = tmp_path / "truth.json"; truth.write_text(json.dumps({"cases": truth_cases}))
    pol = None
    if policies is not None:
        pol = tmp_path / "pol.json"
        pol.write_text(json.dumps({"version": 1, "policies": policies}))
    return reg, truth, pol


def test_without_policies_report_has_no_post_keys(tmp_path):
    """Backwards compatibility: the pre-policy report is unchanged."""
    reg, truth, _ = _files(tmp_path, [
        {"intent": "notify", "urgency": "info", "expected": ["notifier-sms-01", "tv-display-01"]}])
    report = rb.evaluate(reg, truth)
    assert "mean_precision_post" not in report
    assert "policy_decision" not in report["cases"][0]


def test_exclusion_raises_post_precision_when_gt_agrees(tmp_path):
    """The production question: operator GT does NOT expect the TV; the resolver
    (truthful capabilities) includes it. Pre-precision suffers; the operator's own
    exclusion fixes it — and the post number measures exactly that."""
    reg, truth, pol = _files(
        tmp_path,
        [{"intent": "notify", "urgency": "info", "expected": ["notifier-sms-01"]}],
        [{"type": "device_exclusion", "intent_classes": ["notify"],
          "excluded_device_ids": ["tv-display-01"], "bypass_on_emergency": False}])
    report = rb.evaluate(reg, truth, policies_path=pol)
    case = report["cases"][0]
    assert case["precision"] < 1.0, "pre must show the truthful TV as unexpected"
    assert case["precision_post"] == 1.0, "post must reflect the exclusion"
    assert case["policy_decision"] == "modify"
    assert case["removed_by_policy"] == ["tv-display-01"]


def test_exclusion_lowers_post_recall_when_gt_disagrees(tmp_path):
    """The other honest direction: if the GT expects a device the operator's own
    policy removes, post-recall drops. The tool must not paper over that."""
    reg, truth, pol = _files(
        tmp_path,
        [{"intent": "notify", "urgency": "info", "expected": ["notifier-sms-01", "tv-display-01"]}],
        [{"type": "device_exclusion", "intent_classes": ["notify"],
          "excluded_device_ids": ["tv-display-01"], "bypass_on_emergency": False}])
    report = rb.evaluate(reg, truth, policies_path=pol)
    case = report["cases"][0]
    assert case["recall"] == 1.0
    assert case["recall_post"] == 0.5


def test_bypassable_exclusion_is_measured_as_bypassed_at_emergency(tmp_path):
    """bypass semantics are measured at each case's GT urgency, exactly as they
    run in production: bypass=true at emergency → plan unchanged, decision allow."""
    cases = [{"intent": "ensure_safety", "urgency": "emergency",
              "expected": ["wiz-living-01", "wiz-bedroom-01", "wiz-mistagged-01", "notifier-sms-01", "lock-front-01", "tv-display-01"]}]
    pol_bypass = [{"type": "device_exclusion", "intent_classes": ["ensure_safety"],
                   "excluded_device_ids": ["tv-display-01"], "bypass_on_emergency": True}]
    pol_abs = [{"type": "device_exclusion", "intent_classes": ["ensure_safety"],
                "excluded_device_ids": ["tv-display-01"], "bypass_on_emergency": False}]

    reg, truth, pol = _files(tmp_path, cases, pol_bypass)
    case = rb.evaluate(reg, truth, policies_path=pol)["cases"][0]
    assert case["policy_decision"] == "allow"
    assert case["resolved_post"] == case["resolved"]

    (tmp_path / "pol.json").write_text(json.dumps({"version": 1, "policies": pol_abs}))
    case = rb.evaluate(reg, truth, policies_path=tmp_path / "pol.json")["cases"][0]
    assert case["policy_decision"] == "modify"
    assert "tv-display-01" in case["removed_by_policy"]


def test_block_scores_as_nothing_executed(tmp_path):
    """A blocked intent executes nothing; if the GT expected devices, recall goes
    to 0 — that IS the deployment's operative behavior."""
    reg, truth, pol = _files(
        tmp_path,
        [{"intent": "notify", "urgency": "info", "expected": ["notifier-sms-01"]}],
        [{"type": "block_intent", "intent_classes": ["notify"],
          "reason": "operator prohibited"}])
    report = rb.evaluate(reg, truth, policies_path=pol)
    case = report["cases"][0]
    assert case["policy_decision"] == "block"
    assert case["resolved_post"] == 0
    assert case["recall_post"] == 0.0


def test_broken_policy_file_fails_loudly(tmp_path):
    """The benchmark inherits the loader's fail-loudly: a typo'd policy file must
    not silently score as 'no policies'."""
    reg, truth, _ = _files(tmp_path, [
        {"intent": "notify", "urgency": "info", "expected": ["notifier-sms-01"]}])
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 1, "policies": [{"type": "nope"}]}))
    with pytest.raises(PolicyConfigError):
        rb.evaluate(reg, truth, policies_path=bad)
