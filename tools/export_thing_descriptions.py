#!/usr/bin/env python3
"""Describe DoSync devices as W3C WoT Thing Descriptions, and check nothing is lost.

DoSync's own contribution is the governance of an agent's goal -- which devices
take part, where, and under what rules -- not the description of devices, which
the W3C Thing Description (TD) 1.1 standardises. This tool shows the two fit:

  1. EXPORT   each device's manifest as a TD 1.1 instance. The descriptive half
              maps to TD terms (title, actions with input schemas, read-only
              properties with unit and bounds, events). What governance needs and
              TD does not define -- the capability type an action or sensor
              realises, the operator-set location, emergency participation and
              emergency actions, a sensor's kind -- travels in a `dosync:`
              extension vocabulary, declared in the JSON-LD @context.
  2. VALIDATE each TD against the official TD 1.1 JSON Schema (vendored).
  3. ROUND-TRIP rebuild every device from its TD alone, resolve the same
              scenarios with both registries, and compare the plans.

Forms address the hub, not the device: invoking an action through a TD goes
through the hub's governed binding (`POST /v1/things/{id}/actions/{name}`), so
every SAMUEL guarantee applies; the hub serves these same TDs at
`GET /v1/things` (dosync/wot_export.py, where the mapping now lives).

    PYTHONPATH=. python3 tools/export_thing_descriptions.py --out /tmp/tds
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DOSYNC_DB", ":memory:")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dosync.hub import DoSyncHub                                                  # noqa: E402
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,      # noqa: E402
                           EventSpec, Intent, IntentClass, SensorSpec, Severity, Urgency)
from tools.recall_benchmark import _register_domain_intents, load_registry        # noqa: E402

TD_SCHEMA = REPO / "tools" / "vendor" / "w3c-wot-td-1.1-validation.schema.json"
SETS = (
    ("residential", "benchmarks/fixtures/prod_registry_anonymized.json",
     ("benchmarks/fixtures/prod_ground_truth_operator.json",
      "benchmarks/fixtures/prod_ground_truth_location.json")),
    ("industrial", "benchmarks/corpus/industrial_registry.json",
     ("benchmarks/corpus/industrial_ground_truth.json",)),
)


from dosync.wot_export import (DOSYNC_NS, TD_CONTEXT, manifest_to_td,  # noqa: E402,F401
                               td_to_manifest)  # moved into the package 2026-10-12


def governance_fields(m: CapabilityManifest) -> dict:
    """Everything the resolver, the policies and the emergency rules read."""
    return {
        "category": m.category.value, "tags": sorted(m.tags),
        "actuators": sorted((a.id, a.type, json.dumps(a.params_schema or {}, sort_keys=True),
                             a.execution_model) for a in m.actuators),
        "sensors": sorted((s.id, s.type, s.unit or "", tuple(s.range or ()), s.kind)
                          for s in m.sensors),
        "emergency_capable": bool(m.emergency_capable),
        "emergency_actions": [(a["action"], json.dumps(a.get("params") or {}, sort_keys=True))
                              for a in m.emergency_actions],
        "location": m.location,
    }


def _validator():
    from jsonschema import Draft7Validator
    return Draft7Validator(json.loads(TD_SCHEMA.read_text(encoding="utf-8")))


def _plans(hub: DoSyncHub, truth: dict) -> list:
    out = []
    for case in truth["cases"]:
        plan = hub.resolver.resolve(Intent(intent=IntentClass(case["intent"]),
                                           urgency=Urgency(case.get("urgency", "info")),
                                           context=dict(case.get("context") or {})))
        out.append(sorted((a.device_id, a.action) for a in plan.actions))
    return out


def run(out_dir: Path | None = None) -> dict:
    validator = _validator()
    report = {"devices": 0, "valid": 0, "invalid": [], "fields_preserved": 0, "lost": [],
              "scenarios": 0, "identical_plans": 0, "differing": []}
    for name, registry, truths in SETS:
        original = DoSyncHub(db_path=":memory:")
        load_registry(REPO / registry, original)
        rebuilt = DoSyncHub(db_path=":memory:")
        for m in original.registry.active():
            td = manifest_to_td(m)
            report["devices"] += 1
            errors = list(validator.iter_errors(td))
            if errors:
                report["invalid"].append((m.device_id, errors[0].message[:120]))
            else:
                report["valid"] += 1
            if out_dir:
                (out_dir / name).mkdir(parents=True, exist_ok=True)
                (out_dir / name / f"{m.device_id}.td.json").write_text(
                    json.dumps(td, indent=2, ensure_ascii=False), encoding="utf-8")
            back = td_to_manifest(td)
            if governance_fields(back) == governance_fields(m):
                report["fields_preserved"] += 1
            else:
                report["lost"].append(m.device_id)
            rebuilt.register_device(back)
        for truth_path in truths:
            truth = json.loads((REPO / truth_path).read_text(encoding="utf-8"))
            _register_domain_intents(original, truth)
            _register_domain_intents(rebuilt, truth)
            a, b = _plans(original, truth), _plans(rebuilt, truth)
            for case, pa, pb in zip(truth["cases"], a, b):
                report["scenarios"] += 1
                if pa == pb:
                    report["identical_plans"] += 1
                else:
                    report["differing"].append((name, case["intent"], case.get("context")))
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, help="directory to write the exported TDs to")
    args = ap.parse_args()
    r = run(args.out)
    print(f"Thing Descriptions valid against TD 1.1: {r['valid']}/{r['devices']}")
    print(f"devices whose governance fields survive the TD: {r['fields_preserved']}/{r['devices']}")
    print(f"scenarios with an identical plan from the TDs alone: {r['identical_plans']}/{r['scenarios']}")
    for item in r["invalid"] + r["lost"] + r["differing"]:
        print("  ", item)


if __name__ == "__main__":
    main()
