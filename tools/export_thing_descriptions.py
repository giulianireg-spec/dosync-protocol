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
through `POST /v1/device/action`, so the operator's policies and the audit log
apply; sensor readings and events are observed on the hub's WebSocket.

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
TD_CONTEXT = "https://www.w3.org/2022/wot/td/v1.1"
DOSYNC_NS = "https://dosync.dev/ns/governance#"
SETS = (
    ("residential", "benchmarks/fixtures/prod_registry_anonymized.json",
     ("benchmarks/fixtures/prod_ground_truth_operator.json",
      "benchmarks/fixtures/prod_ground_truth_location.json")),
    ("industrial", "benchmarks/corpus/industrial_registry.json",
     ("benchmarks/corpus/industrial_ground_truth.json",)),
)


def manifest_to_td(m: CapabilityManifest, base: str = "https://dosync-hub.local:47200/") -> dict:
    ws = base.replace("https://", "wss://").replace("http://", "ws://") + "ws"
    td = {
        "@context": [TD_CONTEXT, {"dosync": DOSYNC_NS}],
        "id": f"urn:dosync:device:{m.device_id}",
        "title": m.device_name,
        "base": base,
        "securityDefinitions": {"bearer_sc": {"scheme": "bearer", "in": "header"}},
        "security": "bearer_sc",
        "dosync:category": m.category.value,
        "dosync:tags": list(m.tags),
        "dosync:emergencyCapable": bool(m.emergency_capable),
        "properties": {}, "actions": {}, "events": {},
    }
    if m.location:
        td["dosync:location"] = m.location
    if m.emergency_actions:
        td["dosync:emergencyActions"] = [dict(a) for a in m.emergency_actions]
    for a in m.actuators:
        td["actions"][a.id] = {
            "title": a.description or a.id,
            "dosync:capability": a.type,
            "synchronous": a.execution_model == "instant",
            "input": {"type": "object",
                      "properties": {"device_id": {"const": m.device_id},
                                     "action": {"const": a.id},
                                     # exactly as declared ({} when none): writing
                                     # a placeholder schema here changed every
                                     # action's description on the round trip
                                     "params": dict(a.params_schema or {})},
                      "required": ["device_id", "action"]},
            "forms": [{"href": "v1/device/action", "op": "invokeaction",
                       "contentType": "application/json"}],
        }
    for s in m.sensors:
        prop = {"title": s.description or s.id, "readOnly": True, "observable": True,
                "dosync:capability": s.type, "dosync:sensorKind": s.kind,
                "forms": [{"href": ws, "op": "observeproperty"}]}
        if s.unit:
            prop["unit"] = s.unit
        if s.range and len(s.range) == 2:
            prop["minimum"], prop["maximum"] = s.range
        td["properties"][s.id] = prop
    for e in m.events:
        td["events"][e.id] = {"title": e.description or e.id,
                              "dosync:severity": e.severity.value,
                              "forms": [{"href": ws, "op": "subscribeevent"}]}
    return td


def td_to_manifest(td: dict) -> CapabilityManifest:
    """Rebuild, from the TD alone, everything governance reads."""
    device_id = td["id"].rsplit(":", 1)[-1]
    actuators = [ActuatorSpec(k, v["dosync:capability"], v.get("title", ""),
                              params_schema=(v["input"]["properties"].get("params") or {}),
                              execution_model="instant" if v.get("synchronous", True) else "long_running")
                 for k, v in (td.get("actions") or {}).items()]
    sensors = [SensorSpec(id=k, type=v["dosync:capability"], description=v.get("title", ""),
                          unit=v.get("unit"),
                          range=([v["minimum"], v["maximum"]] if "minimum" in v and "maximum" in v else None),
                          kind=v.get("dosync:sensorKind", "environment"))
               for k, v in (td.get("properties") or {}).items()]
    events = [EventSpec(k, Severity(v.get("dosync:severity", "info")), v.get("title", ""))
              for k, v in (td.get("events") or {}).items()]
    return CapabilityManifest(
        device_id=device_id, device_name=td["title"], manufacturer="td", model="td", firmware="td",
        category=DeviceCategory(td["dosync:category"]), tags=list(td.get("dosync:tags") or []),
        actuators=actuators, sensors=sensors, events=events,
        emergency_capable=bool(td.get("dosync:emergencyCapable")),
        emergency_actions=[dict(a) for a in (td.get("dosync:emergencyActions") or [])],
        location=td.get("dosync:location", ""))


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
