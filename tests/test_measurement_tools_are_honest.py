"""The measurement tools must reproduce a known baseline before they are trusted.

These tools produced false numbers three times while being written: an intent
passed as a string instead of an `IntentClass`, domain intents never registered,
and a hub built without the in-memory database that seeds the universal intents.
All three made every regime score identically, and all three were caught by
noticing the output did not match the official benchmark — by hand, each time.

That is manual verification wearing the clothes of a method. These tests do it
automatically: if a tool stops reproducing the numbers the shipped resolver
produces, the tool is wrong until proven otherwise, and no conclusion drawn from
it should be believed.

Three things a reader should know before trusting this file:

**Where the expected numbers come from.** 0.64, 0.61 and 1.00 are what
`tools/recall_benchmark.py` — the benchmark the paper reported — scores on the
industrial corpus, the clinical corpus and the tuned fixture. They are not a
target; they are the current behaviour, recorded so that a tool claiming to
measure it can be checked against it.

**Some of these are text searches, and they are weaker than they look.** A test
that asserts a string is present in a source file catches someone deleting a
correction. It does not catch someone breaking the correction while keeping the
words. Where a behavioural test was possible it was written instead; where the
property is structural — "the rulebook is not in the methods table" — the text
search is what there is.

**These will fail when the resolver is redesigned, on purpose.** They guard the
measurement tools against accidental drift, not the resolver against deliberate
change. When the resolver changes, the expected numbers are updated in the same
commit as a decision — not discovered broken afterwards.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CORPORA = {
    # Updated 4 September, deliberately and in the same commit as the change
    # that moved them. These numbers are what the shipped resolver scores, not
    # a target: the guard exists so that a tool drifting from the resolver is
    # caught, and it did catch this — it fired the moment the gate changed,
    # which is what it is for.
    #
    # They rose because participation is now decided by declared capability
    # rather than by a curated tag: 0.64 → 0.85 industrial, 0.61 → 0.83
    # clinical, both at recall 1.00. The corpora were expected to FALL, since
    # they measure agreement with tags; that they improved this much says the
    # tag filter was costing accuracy even by its own standard.
    #
    # Most of the gain is not the scoring gate — it is candidate selection. The
    # gate alone moved these to 0.66 and 0.65, because a tag index upstream had
    # already decided who would be scored at all.
    "industrial": (REPO / "benchmarks/corpus/industrial_registry.json",
                   REPO / "benchmarks/corpus/industrial_ground_truth.json",
                   0.85),
    "clinical":   (REPO / "benchmarks/corpus/clinical_registry.json",
                   REPO / "benchmarks/corpus/clinical_ground_truth.json",
                   0.83),
    "recall":     (REPO / "benchmarks/fixtures/recall_registry.json",
                   REPO / "benchmarks/fixtures/recall_ground_truth.json",
                   1.00),
}


def _tool():
    """Reloaded, not cached.

    A first version imported the module once and let Python cache it, so
    reintroducing a known defect into the file changed nothing and the tests
    passed anyway — a guard that cannot fail is not a guard.
    """
    import importlib
    import sys

    sys.path.insert(0, str(REPO / "tools"))
    import counterfactual_resolver as cf
    return importlib.reload(cf)


def _baselines():
    """Also reloaded. The first behavioural test here passed with the defect it
    was written to catch, for the same reason: Python had the module cached."""
    import importlib
    import sys

    sys.path.insert(0, str(REPO / "tools"))
    import resolver_baselines as rb
    return importlib.reload(rb)


@pytest.mark.parametrize("corpus", sorted(CORPORA))
def test_the_counterfactual_reproduces_the_shipped_resolver(corpus):
    """`current` must match what the resolver actually scores.

    If it does not, the tool is measuring something else and every regime it
    reports is meaningless — which is precisely what happened three times.
    """
    import json

    cf = _tool()
    reg_path, truth_path, expected_f1 = CORPORA[corpus]
    truth_doc = json.loads(truth_path.read_text(encoding="utf-8"))
    cases = truth_doc.get("cases", truth_doc)

    rows = cf._evaluate(reg_path, truth_doc, cases, "current")
    f1 = sum(r["f1"] for r in rows) / len(rows)

    assert abs(f1 - expected_f1) < 0.02, (
        f"the counterfactual scores {corpus} at F1 {f1:.2f}, but the shipped "
        f"resolver scores {expected_f1:.2f}. The tool is not measuring the "
        "resolver — fix the tool before reading anything it reports.")


def test_the_capability_gate_now_uses_the_sensors_it_once_could_not():
    """This test used to assert the opposite, and that was correct at the time.

    An early gate read `resolution["sensors"]`, a key that did not exist. The
    branch never ran, the tool reported a capability gate "including sensors"
    that only looked at actuators, and nobody noticed because the number was
    plausible. The fix was an assertion that the key was absent — a guard that
    would fire the day the field arrived.

    It fired. `resolution_sensors` exists now, so the guard is replaced by its
    inverse: the gate must actually read them. A motion detector answering an
    alert has no actuators, and a gate blind to sensors drops it — measured at
    F1 1.00 → 0.67 before the field existed, and back at 1.00 after.
    """
    source = (REPO / "tools" / "counterfactual_resolver.py").read_text(encoding="utf-8")
    assert 'resolution.get("sensors"' in source, (
        "the capability gate ignores the sensors an intent declares")
    assert 'assert "sensors" not in resolution' not in source, (
        "the old guard is still there and would now fire on correct data")


def test_the_rulebook_is_not_reported_as_a_result():
    """It is handed the ground truth, so its score is arithmetic. Printing it
    beside real methods invites comparing a method against an answer key."""
    source = (REPO / "tools" / "resolver_baselines.py").read_text(encoding="utf-8")
    assert "perfect by construction" in source
    assert '("RULEBOOK", ' not in source, \
        "the rulebook is back in the methods table, where it does not belong"


def test_the_no_tags_baseline_actually_ignores_tags():
    """Behaviour, not a string count.

    An earlier version asserted that `if use_tags:` appeared twice in the
    source, which would pass with both occurrences on the same side of the
    comparison. What matters is that the two modes disagree on a registry
    where tags carry information the capabilities do not.
    """
    import json

    rb = _baselines()
    from dosync.hub import DoSyncHub
    from recall_benchmark import _register_domain_intents, load_registry

    reg_path, truth_path, _ = CORPORA["clinical"]
    truth_doc = json.loads(truth_path.read_text(encoding="utf-8"))
    hub = DoSyncHub(db_path=":memory:")
    load_registry(reg_path, hub)
    _register_domain_intents(hub, truth_doc)

    # `alert_anomaly` specifically: its resolution carries the tag `sensor`,
    # which is the one tag doing work no capability declares — no intent class
    # says which sensors answer it. So the two modes must disagree here even
    # though they agree on `ensure_safety`, where every tag duplicates an
    # actuator the device already declares.
    with_tags = rb.resolve_keyword(hub, "alert_anomaly", "alert", use_tags=True)
    without = rb.resolve_keyword(hub, "alert_anomaly", "alert", use_tags=False)

    # Naming the devices rather than asserting the sets merely differ. An
    # earlier version checked `with_tags != without`, which still passed when
    # only one of the two `use_tags` branches was broken — the same weakness as
    # counting occurrences of a string, in another shape.
    #
    # These two are reachable only through the tag `sensor`: they declare no
    # actuators, and no intent class says which sensors answer `alert_anomaly`.
    # They are the concrete case of the gap that tag is covering.
    only_by_tag = with_tags - without
    assert only_by_tag == {"sensor-or3-pressure", "sensor-or3-temp"}, (
        f"expected the two OR-3 sensors to be reachable only through the tag "
        f"`sensor`, got {sorted(only_by_tag)}. Either the corpus changed or "
        "`use_tags` is being ignored")

    # What this catches, and what it does not, verified by mutation:
    #   ignoring `use_tags` on both sides   → fails, as it should
    #   ignoring it on one side only        → passes, and correctly so: a tag
    #                                         match needs the tag on both the
    #                                         device and the intent, so a
    #                                         one-sided break changes nothing
    #                                         here and there is nothing to
    #                                         detect
    # Stated rather than left implicit, because a guard whose reach is unknown
    # gets trusted further than it earns.


@pytest.mark.parametrize("corpus", sorted(CORPORA))
def test_the_baselines_reproduce_their_own_numbers(corpus):
    """`resolver_baselines.py` produced the most consequential finding of the
    phase — that removing tags improves word overlap — and had no test at all.

    This pins the shipped resolver's score through that tool. If it drifts, the
    tool and the benchmark disagree, and the finding needs rechecking before it
    is repeated.
    """
    import json

    rb = _baselines()
    from dosync.hub import DoSyncHub
    from recall_benchmark import _register_domain_intents, load_registry

    reg_path, truth_path, expected = CORPORA[corpus]
    truth_doc = json.loads(truth_path.read_text(encoding="utf-8"))
    cases = truth_doc.get("cases", truth_doc)
    hub = DoSyncHub(db_path=":memory:")
    load_registry(reg_path, hub)
    _register_domain_intents(hub, truth_doc)

    _, _, f1 = rb._evaluate(rb._dosync, hub, cases)
    assert abs(f1 - expected) < 0.02, (
        f"the baselines tool scores DoSync on {corpus} at {f1:.2f}, but the "
        f"benchmark scores {expected:.2f} — they cannot both be right")


# ── resolution_sensors (2026-09-03) ───────────────────────────────────────────

def test_intents_can_declare_which_sensors_answer_them():
    """Before this field, an intent could say what must be ACTED on and not what
    must be DETECTED. `alert_anomaly` carried the tag `sensor` to make a motion
    detector and a thermometer resolve — a curated label standing in for a
    missing field, which is the reviewer's tag-curation critique in miniature.
    """
    from dosync.hub import DoSyncHub

    hub = DoSyncHub(db_path=":memory:")
    alert = hub.db.get_intent_class("alert_anomaly")
    assert alert["resolution_sensors"], (
        "alert_anomaly declares no sensors; the devices that answer it by "
        "detecting depend on the tag `sensor` again")

    # Four of the five universals are answered by acting, not detecting.
    for name in ("control_access", "report_status", "notify"):
        assert hub.db.get_intent_class(name)["resolution_sensors"] == [], (
            f"{name} declares sensors it is not answered by")


def test_a_generic_sensor_type_is_not_a_participation_signal():
    """`number` is a shape, not a meaning.

    A particulate counter and a pressure gauge both report `number`. Including
    it in alert_anomaly made every numeric sensor in the industrial corpus
    qualify for any alert, and precision fell from 0.73 to 0.60 — measured. A
    label that fits everything selects nothing, which is the same reason the tag
    vocabulary keeps failing.
    """
    from dosync.hub import DoSyncHub

    hub = DoSyncHub(db_path=":memory:")
    sensors = hub.db.get_intent_class("alert_anomaly")["resolution_sensors"]
    assert "number" not in sensors, (
        "`number` is back in alert_anomaly's sensors: it says only that the "
        "device reads a number, which cannot decide participation")


def test_an_absent_sensors_column_means_empty_not_wildcard():
    """A hub upgrading from a database without this column must not change
    behaviour. Absent means "this intent is not answered by sensors", never
    "any sensor will do" — the conservative reading, because the permissive one
    would silently widen every intent on a deployment nobody touched.
    """
    from dosync.hub import DoSyncHub

    hub = DoSyncHub(db_path=":memory:")
    hub.db._conn.execute(
        "INSERT INTO intent_classes (name,urgency,resolution_tags,"
        "resolution_actuators,description,domain,is_universal,created_at) "
        "VALUES ('legacy_intent','info','[]','[]','','test',0,0)")
    hub.db._conn.commit()

    row = hub.db.get_intent_class("legacy_intent")
    assert row["resolution_sensors"] == [], (
        "a row written without the column reads as something other than an "
        "empty list")
