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
                                description=sn.get("description", ""))
                     for sn in sensors_raw],
            actuators=[ActuatorSpec(id=a["id"], type=a["type"],
                                    description=a.get("description", ""))
                       for a in actuators_raw],
            events=[],
            emergency_capable=bool(d.get("emergency_capable", False)),
            cert_tier=d.get("cert_tier", "standard"),
        )
        hub.registry.register(manifest)


def evaluate(registry_path: Path, truth_path: Path) -> dict:
    """Run every ground-truth case through the real resolver. Returns the report."""
    hub = DoSyncHub(db_path=":memory:")          # seeds the real universal intents
    load_registry(registry_path, hub)
    resolver = hub.resolver

    truth = json.loads(truth_path.read_text())
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

        tp = got & expected
        precision = len(tp) / len(got) if got else (1.0 if not expected else 0.0)
        recall = len(tp) / len(expected) if expected else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

        # Explain every miss with the resolver's own reasoning.
        misses = []
        missing = expected - got
        if missing:
            explanation = resolver.explain(intent)
            reasons = {d["device_id"]: d.get("reason", "?")
                       for d in explanation.get("excluded", [])}
            for dev in sorted(missing):
                misses.append({"device_id": dev,
                               "reason": reasons.get(dev, "not in registry / not excluded-listed")})

        per_intent.append({
            "intent": case["intent"], "urgency": case.get("urgency", "info"),
            "expected": len(expected), "resolved": len(got),
            "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(f1, 3),
            "unexpected": sorted(got - expected),
            "misses": misses,
        })

    n = len(per_intent)
    report = {
        "cases": per_intent,
        "mean_precision": round(sum(c["precision"] for c in per_intent) / n, 3),
        "mean_recall": round(sum(c["recall"] for c in per_intent) / n, 3),
        "mean_f1": round(sum(c["f1"] for c in per_intent) / n, 3),
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="DoSync resolver recall benchmark")
    ap.add_argument("--registry", type=Path, default=FIXTURE_DIR / "recall_registry.json")
    ap.add_argument("--truth", type=Path, default=FIXTURE_DIR / "recall_ground_truth.json")
    ap.add_argument("--json", type=Path, help="write full JSON report here")
    ap.add_argument("--min-recall", type=float, help="exit 1 if mean recall is below this")
    args = ap.parse_args()

    report = evaluate(args.registry, args.truth)

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
