#!/usr/bin/env python3
"""
DoSync — Resolver recall benchmark (QA-1a).
===========================================

Measures per-intent precision/recall of the REAL resolver against a labeled
ground truth, and explains every miss using the resolver's own explain() —
so each miss comes with the exact exclusion reason (actionable, not a number).

Why this exists (2026-07-11): the previous measurement (tools/scoring_sensitivity.py,
paper Table 3, "recall 0.49") used a frozen copy of the resolver and a frozen
resolution map, over 13 intents of which 8 no longer exist as protocol seeds.
Numbers from it should not be quoted. Worse, the live resolver had a one-line
wiring bug (see tests/test_resolution_wiring.py) that silently emptied every
resolution — this tool is what surfaced it.

This tool imports the live resolver and the live DB seeds, so it measures the
protocol as it actually is:

    PYTHONPATH=. python3 tools/recall_benchmark.py                       # fixture
    PYTHONPATH=. python3 tools/recall_benchmark.py \
        --registry my_registry.json --truth my_truth.json --min-recall 0.8

Registry JSON: {"devices": [{device_id, device_name, category, tags,
emergency_capable, actuators:[{id,type,description}]}, ...]}
Ground truth JSON: {"cases": [{intent, urgency, context, expected:[ids]}, ...]}

To evaluate PRODUCTION: export the live registry (GET /v1/devices), write the
ground truth you expect as an operator, and run with both files. The misses
report tells you exactly which tags to fix (see TAG-VOCABULARY.md).

POST-POLICY MODE (--policies <file>, added 2026-07-17 once POL-1 made the policy
layer configurable): every case is scored twice — on the resolver's raw plan
(pre-policy) and on that plan after PolicyEngine.evaluate() with the deployment
policies loaded from the given file (post-policy). The pre number measures the
protocol's semantic layer against truthfully-declared capabilities; the post
number measures WHAT THE DEPLOYMENT ACTUALLY EXECUTES. They are different
questions, and quoting one as the other is how the 0.49 mistake happened. The
delta between them is the measured effect of the operator's own policy file.

Two deliberate scoping choices:
  * Only DEPLOYMENT policies (the file) are loaded — not the hub's infrastructure
    policies. Rate limiting would block later benchmark cases and measure the
    benchmark itself, not the deployment; conflict resolution needs live intent
    state.
  * Each case is evaluated at its ground-truth urgency, so bypass_on_emergency
    semantics are measured exactly as they run in production.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           Intent, IntentClass, SensorSpec, Urgency)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures"


def load_registry(path: Path, hub: DoSyncHub) -> None:
    data = json.loads(path.read_text())
    for d in data["devices"]:
        # Accept BOTH shapes: the fixture shape (sensors/actuators at top level)
        # and the live API shape (GET /v1/devices nests them under
        # "capabilities") — so a raw export of the production registry works
        # as-is, with no transformation step to get wrong.
        caps = d.get("capabilities") or {}
        sensors_raw = d.get("sensors") or caps.get("sensors") or []
        actuators_raw = d.get("actuators") or caps.get("actuators") or []
        manifest = CapabilityManifest(
            device_id=d["device_id"],
            device_name=d.get("device_name", d["device_id"]),
            manufacturer=d.get("manufacturer", "fixture"),
            model=d.get("model", "fixture"),
            firmware=d.get("firmware", "1"),
            category=DeviceCategory(d.get("category", "actuator")),
            tags=list(d.get("tags", [])),
            sensors=[SensorSpec(id=sn["id"], type=sn["type"],
                                description=sn.get("description", ""),
                                kind=sn.get("kind", "environment"))
                     for sn in sensors_raw],
            actuators=[ActuatorSpec(id=a["id"], type=a["type"],
                                    description=a.get("description", ""))
                       for a in actuators_raw],
            events=[],
            emergency_capable=bool(d.get("emergency_capable", False)),
            cert_tier=d.get("cert_tier", "standard"),
        )
        hub.registry.register(manifest)


#: What precision and recall mean when a set is empty.
#:
#: Undeclared conventions produced a table that is impossible under standard
#: definitions — a WF-IoT reviewer caught rows reading precision 1.00 with recall
#: 0.00, and correctly observed that precision 1.00 requires at least one
#: selected device to be relevant, which forces recall above zero.
#:
#: The reviewer inferred empty-set conventions. The actual cause was two separate
#: defects:
#:
#:   1. ROUNDING. Selecting one correct device out of three hundred expected
#:      gives precision 1.0 and recall 0.0033, which printed as 1.00 and 0.00 —
#:      arithmetically fine, and unreadable as anything but a contradiction.
#:
#:   2. A CONVENTION THAT REWARDED A FALSE POSITIVE. With nothing expected and
#:      something selected, recall returned 1.0: the resolver touched a device it
#:      should not have and scored a perfect recall for it. In a protocol whose
#:      argument is governance, scoring an unwanted actuation as success is worse
#:      than a display bug.
#:
#: Both are fixed below, and every row now carries its raw tp/fp/fn counts so a
#: reader can check the arithmetic instead of inferring it.
EMPTY_SET_CONVENTIONS = {
    "expected=∅, selected=∅": "precision 1.0, recall 1.0 — correctly did nothing",
    "expected=∅, selected≠∅": "precision 0.0, recall 0.0 — acted when it should not have",
    "expected≠∅, selected=∅": "precision 0.0, recall 0.0 — missed everything",
}


def _score(got: set, expected: set) -> tuple[float, float, float]:
    """Precision, recall and F1, with empty-set behaviour stated rather than implied.

    See EMPTY_SET_CONVENTIONS. The case worth naming: selecting devices when none
    were expected now scores zero on both, where it previously scored recall 1.0.
    """
    tp = got & expected

    if not expected and not got:
        return 1.0, 1.0, 1.0            # asked to touch nothing, touched nothing
    if not expected:
        return 0.0, 0.0, 0.0            # acted when it should not have
    if not got:
        return 0.0, 0.0, 0.0            # missed everything

    precision = len(tp) / len(got)
    recall = len(tp) / len(expected)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _counts(got: set, expected: set) -> dict:
    """Raw counts, reported alongside every rate.

    A rate rounded to two decimals can read as impossible while being correct —
    0.0033 prints as 0.00. Counts cannot: a reader who sees tp=1, fp=0, fn=299
    can do the arithmetic and needs no convention explained to them.
    """
    tp = got & expected
    return {"tp": len(tp), "fp": len(got - expected), "fn": len(expected - got),
            "selected": len(got), "expected": len(expected)}


#: Why a device the ground truth expected did not appear in the plan.
#:
#: A benchmark reporting "F1 0.00" without saying whether the intent was never
#: registered, the device lacked a tag, or the resolver simply chose otherwise is
#: measuring three things and reporting their sum. Of twelve misses in the first
#: multi-domain run, ten were not the resolver.
MISS_CAUSES = {
    "intent_not_registered": "the intent class does not exist in this hub",
    "vocabulary": "the device lacks a tag the intent requires",
    "resolution": "the resolver evaluated the device and chose otherwise",
    "not_in_registry": "the device id is not in the registry at all",
}


def classify_miss(reason: str, intent_registered: bool) -> str:
    """Attribute a miss to a cause, so a recall number means one thing.

    Order matters: an unregistered intent explains every miss in its case, and
    checking it first stops those from being blamed on the resolver.
    """
    if not intent_registered:
        return "intent_not_registered"
    if "required specific tags" in reason:
        return "vocabulary"
    if "not in registry" in reason:
        return "not_in_registry"
    return "resolution"


def _register_domain_intents(hub, truth: dict) -> set:
    """Register the intent classes a corpus declares, before evaluating it.

    Without this the tool could only measure the five universal intents — which
    is why every published evaluation of this project covered the home domain.
    Not because the author chose it: `line_shutdown` and
    `prepare_operating_room` fell through to the default behaviour and scored as
    status queries. **A protocol claiming to be domain-agnostic had no way to
    evaluate the claim.**
    """
    declared = truth.get("intent_classes") or {}
    registered = set()
    for name, spec in declared.items():
        if hub.db.get_intent_class(name):
            continue
        hub.db.save_intent_class(
            name=name,
            urgency=spec.get("urgency", "info"),
            resolution_tags=spec.get("target_tags", []),
            resolution_actuators=spec.get("target_actuators", []),
            description=spec.get("description", f"corpus intent {name}"),
            domain=spec.get("domain", "corpus"))
        registered.add(name)
    return registered


def evaluate(registry_path: Path, truth_path: Path,
             policies_path: Path | None = None) -> dict:
    """Run every ground-truth case through the real resolver. Returns the report.

    With policies_path, each case is ALSO scored post-policy: the resolved plan is
    passed through PolicyEngine.evaluate() with the deployment policies from the
    file, at the case's own urgency — measuring what the deployment executes, not
    just what the resolver proposes.
    """
    hub = DoSyncHub(db_path=":memory:")          # seeds the real universal intents
    load_registry(registry_path, hub)

    truth = json.loads(truth_path.read_text())
    _register_domain_intents(hub, truth)

    resolver = hub.resolver

    engine = None
    if policies_path is not None:
        from dosync.policies import PolicyEngine
        from dosync import policy_config
        engine = PolicyEngine()
        policy_config.load_into(engine, policies_path, hub=hub)

    per_intent = []
    for case in truth["cases"]:
        intent = Intent(
            intent_id=f"bench-{case['intent']}",
            intent=IntentClass(case["intent"]),
            urgency=Urgency(case.get("urgency", "info")),
            context=case.get("context", {}),
        )
        expected = set(case["expected"])
        plan = resolver.resolve(intent)
        got = {a.device_id for a in plan.actions}

        precision, recall, f1 = _score(got, expected)

        # Explain every miss with the resolver's own reasoning.
        misses = []
        missing = expected - got
        if missing:
            explanation = resolver.explain(intent)
            reasons = {d["device_id"]: d.get("reason", "?")
                       for d in explanation.get("excluded", [])}
            registered = hub.db.get_intent_class(case["intent"]) is not None
            for dev in sorted(missing):
                reason = reasons.get(dev, "not in registry / not excluded-listed")
                misses.append({"device_id": dev, "reason": reason,
                               "cause": classify_miss(reason, registered)})

        row = {
            "intent": case["intent"], "urgency": case.get("urgency", "info"),
            "expected": len(expected), "resolved": len(got),
            "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3),
            # Raw counts alongside every rate. A reviewer reading precision 1.00
            # next to recall 0.00 cannot tell rounding from a broken metric; tp,
            # fp and fn make the row checkable without trusting the tool.
            "counts": _counts(got, expected),
            "unexpected": sorted(got - expected),
            "misses": misses,
        }

        if engine is not None:
            from dosync.policies import PolicyDecision
            result = engine.evaluate(intent, plan)
            decision = result.decision.value if result else "allow"
            if result and result.decision == PolicyDecision.MODIFY:
                got_post = {a.device_id for a in result.modified_actions}
            elif result and result.decision == PolicyDecision.BLOCK:
                # A blocked plan executes nothing. Scoring it against the expected
                # devices is the honest reading: if the operator's GT expects
                # devices to act and their own policy blocks the intent, that IS
                # the deployment's operative behavior — the numbers should say so.
                got_post = set()
            else:
                # ALLOW — and CONFIRM, deliberately: a confirmed plan executes
                # unchanged, so its content is what the deployment runs; the
                # confirmation gate delays it but does not alter it. The decision
                # is recorded per-case so a CONFIRM never hides in the mean.
                got_post = got
            p_post, r_post, f_post = _score(got_post, expected)
            row["policy_decision"] = decision
            row["resolved_post"] = len(got_post)
            row["precision_post"] = round(p_post, 3)
            row["recall_post"] = round(r_post, 3)
            row["f1_post"] = round(f_post, 3)
            row["removed_by_policy"] = sorted(got - got_post)
            row["unexpected_post"] = sorted(got_post - expected)

        per_intent.append(row)

    n = len(per_intent)
    report = {
        # Aggregated, so a reader sees how much of a recall figure is the
        # resolver without reading every row.
        "miss_causes": {k: sum(1 for c in per_intent
                               for m in (c.get("misses") or []) if m.get("cause") == k)
                        for k in MISS_CAUSES},
        "cases": per_intent,
        "mean_precision": round(sum(c["precision"] for c in per_intent) / n, 3),
        "mean_recall": round(sum(c["recall"] for c in per_intent) / n, 3),
        "mean_f1": round(sum(c["f1"] for c in per_intent) / n, 3),
    }
    if engine is not None:
        report["policies_file"] = str(policies_path)
        report["policies_loaded"] = sorted(p.name for p in engine._policies)
        report["mean_precision_post"] = round(sum(c["precision_post"] for c in per_intent) / n, 3)
        report["mean_recall_post"] = round(sum(c["recall_post"] for c in per_intent) / n, 3)
        report["mean_f1_post"] = round(sum(c["f1_post"] for c in per_intent) / n, 3)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="DoSync resolver recall benchmark")
    ap.add_argument("--registry", type=Path, default=FIXTURE_DIR / "recall_registry.json")
    ap.add_argument("--truth", type=Path, default=FIXTURE_DIR / "recall_ground_truth.json")
    ap.add_argument("--json", type=Path, help="write full JSON report here")
    ap.add_argument("--min-recall", type=float, help="exit 1 if mean recall is below this")
    ap.add_argument("--policies", type=Path, default=None,
                    help="deployment policy file (DOSYNC_POLICIES format); scores every "
                         "case pre- AND post-policy and reports the delta")
    args = ap.parse_args()

    report = evaluate(args.registry, args.truth, policies_path=args.policies)

    post = "mean_precision_post" in report
    if post:
        print(f"Deployment policies: {', '.join(report['policies_loaded'])}")
        print(f"  ({report['policies_file']})\n")
        print(f"{'intent':<18} {'urg':<10} {'prec':>5} {'→post':>6} {'recall':>6} {'→post':>6} {'policy':<8} removed")
        print("-" * 92)
        for c in report["cases"]:
            removed = ", ".join(c["removed_by_policy"]) or "-"
            print(f"{c['intent']:<18} {c['urgency']:<10} {c['precision']:>5} {c['precision_post']:>6} "
                  f"{c['recall']:>6} {c['recall_post']:>6} {c['policy_decision']:<8} {removed}")
        print("-" * 92)
        print(f"{'MEAN':<29} {report['mean_precision']:>5} {report['mean_precision_post']:>6} "
              f"{report['mean_recall']:>6} {report['mean_recall_post']:>6}")
        dp = round(report["mean_precision_post"] - report["mean_precision"], 3)
        dr = round(report["mean_recall_post"] - report["mean_recall"], 3)
        print(f"\nPolicy effect on the mean: precision {'+' if dp >= 0 else ''}{dp} · "
              f"recall {'+' if dr >= 0 else ''}{dr}")
        print("(pre = what the semantic layer proposes over truthful capabilities; "
              "post = what THIS deployment executes)")
    else:
        print(f"{'intent':<18} {'urg':<10} {'prec':>5} {'recall':>6} {'f1':>5}  misses")
        print("-" * 78)
        for c in report["cases"]:
            miss_str = ", ".join(m["device_id"] for m in c["misses"]) or "-"
            print(f"{c['intent']:<18} {c['urgency']:<10} {c['precision']:>5} {c['recall']:>6} {c['f1']:>5}  {miss_str}")
        print("-" * 78)
        print(f"{'MEAN':<29} {report['mean_precision']:>5} {report['mean_recall']:>6} {report['mean_f1']:>5}")

    any_miss = any(c["misses"] for c in report["cases"])
    if any_miss:
        print("\nMiss diagnosis (from the resolver's own explain):")
        for c in report["cases"]:
            for m in c["misses"]:
                print(f"  [{c['intent']}] {m['device_id']}: {m['reason']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nJSON report: {args.json}")

    if args.min_recall is not None and report["mean_recall"] < args.min_recall:
        print(f"\nFAIL: mean recall {report['mean_recall']} < {args.min_recall}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
