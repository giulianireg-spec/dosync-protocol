"""Precision, recall, and what the benchmark can actually measure.

Two threads meet in this file, both traceable to WF-IoT 2026 review comments.

**The metrics (2026-08-09).** A reviewer caught rows in Table 3 reading precision
1.00 with recall 0.00 and observed correctly that this is impossible under
standard definitions. Reproducing it found two defects, and only one was the one
guessed: rounding (one hit out of three hundred prints as 1.00 and 0.00), and a
convention that returned recall 1.0 when the resolver actuated a device the
ground truth said to leave alone — scoring an unwanted actuation as success, in a
protocol whose argument is governance.

**The tool (2026-08-11).** Building a multi-domain corpus revealed that the
benchmark never registered domain intent classes, so anything outside the five
universal intents scored zero for reasons unrelated to resolution. Ten of twelve
misses in the first run were not the resolver.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from recall_benchmark import EMPTY_SET_CONVENTIONS, _counts, _score


def test_doing_nothing_when_nothing_was_expected_is_success():
    """The only empty-set case that deserves a perfect score."""
    assert _score(set(), set()) == (1.0, 1.0, 1.0)


def test_acting_when_nothing_was_expected_is_not_recall_one():
    """The defect that mattered. This previously returned recall 1.0: the
    resolver actuated a device the ground truth said to leave alone, and the
    metric called it a perfect result."""
    p, r, f = _score({"lock-front"}, set())
    assert (p, r, f) == (0.0, 0.0, 0.0), \
        "touching a device that should not be touched is not a perfect recall"


def test_selecting_nothing_when_something_was_expected_scores_zero():
    assert _score(set(), {"lamp"}) == (0.0, 0.0, 0.0)


def test_the_impossible_looking_row_is_rounding_and_the_counts_prove_it():
    """One hit out of three hundred expected. The rates still print as 1.00 and
    0.00 — rounding cannot be argued away — but tp/fp/fn make it checkable."""
    got, expected = {"d1"}, {f"d{i}" for i in range(1, 301)}
    p, r, _ = _score(got, expected)
    assert round(p, 2) == 1.00 and round(r, 2) == 0.00

    c = _counts(got, expected)
    assert c == {"tp": 1, "fp": 0, "fn": 299, "selected": 1, "expected": 300}, \
        "a reader must be able to reconstruct both rates from the counts"


def test_every_reported_row_carries_its_counts():
    """A rate alone cannot be audited. A reviewer who sees precision 1.00 beside
    recall 0.00 cannot tell rounding from a broken metric without them."""
    import inspect

    import recall_benchmark
    src = inspect.getsource(recall_benchmark.evaluate)
    assert '"counts": _counts(got, expected)' in src


def test_the_conventions_are_declared_not_implied():
    """The reviewer had to infer them from the numbers. That is the actual
    failure: an evaluation whose rules live only in an expression."""
    assert len(EMPTY_SET_CONVENTIONS) == 3
    for description in EMPTY_SET_CONVENTIONS.values():
        assert "precision" in description and "recall" in description


def test_ordinary_cases_are_unchanged():
    """The fix must not move any number that was already right — the paper's
    good scenarios have to stay comparable."""
    p, r, f = _score({"a", "b"}, {"a", "b", "c"})
    assert p == 1.0
    assert round(r, 4) == round(2 / 3, 4)
    assert round(f, 4) == round(2 * 1.0 * (2 / 3) / (1.0 + 2 / 3), 4)


# ── The benchmark could not evaluate what the protocol claims (2026-08-11) ──

def test_the_benchmark_registers_domain_intents_before_evaluating():
    """`tools/recall_benchmark.py` had zero calls to register an intent class, so
    `line_shutdown` and `prepare_operating_room` fell through to the default
    behaviour and scored as status queries — F1 0.00 for reasons that had nothing
    to do with resolution.

    The consequence is larger than a broken tool: **a protocol claiming to be
    domain-agnostic had no way to evaluate the claim.** Every published
    evaluation covered the home domain not because the author chose it, but
    because the other intents were unmeasurable.
    """
    import inspect

    import recall_benchmark
    src = inspect.getsource(recall_benchmark.evaluate)
    assert "_register_domain_intents" in src


def test_a_corpus_can_declare_its_own_intent_classes(tmp_path):
    import json

    from dosync.hub import DoSyncHub
    from recall_benchmark import _register_domain_intents

    hub = DoSyncHub(db_path=":memory:")
    assert hub.db.get_intent_class("line_shutdown") is None

    registered = _register_domain_intents(hub, {
        "intent_classes": {
            "line_shutdown": {"urgency": "emergency",
                              "target_tags": ["machinery", "emergency"],
                              "target_actuators": ["stop"]}}})

    assert "line_shutdown" in registered
    assert hub.db.get_intent_class("line_shutdown") is not None


def test_every_miss_is_attributed_to_a_cause():
    """A benchmark reporting F1 0.00 without distinguishing "the intent does not
    exist" from "the device lacks a tag" from "the resolver chose otherwise" is
    measuring three things and reporting their sum.

    In the first multi-domain run, ten of twelve misses were not the resolver.
    """
    from recall_benchmark import MISS_CAUSES, classify_miss

    assert classify_miss("anything at all", intent_registered=False) == \
        "intent_not_registered", \
        "an unregistered intent explains every miss in its case and must be " \
        "checked first, or those misses are blamed on the resolver"

    assert classify_miss(
        "required specific tags ['lock'] not in device tags ['access']",
        intent_registered=True) == "vocabulary"

    assert classify_miss("not in registry / not excluded-listed",
                         intent_registered=True) == "not_in_registry"

    assert classify_miss("scored below threshold", intent_registered=True) == \
        "resolution"

    assert set(MISS_CAUSES) == {"intent_not_registered", "vocabulary",
                                "resolution", "not_in_registry"}


def test_the_report_aggregates_causes():
    """So a reader sees how much of a recall figure is the resolver without
    reading every row."""
    import inspect

    import recall_benchmark
    src = inspect.getsource(recall_benchmark.evaluate)
    assert '"miss_causes"' in src
