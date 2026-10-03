#!/usr/bin/env python3
"""Build the data the agent-comparison laboratory runs on, from the real hub.

The laboratory is a claude.ai artifact: it can call a model, but not a hub. So
everything the hub would answer is computed here, with the reference
implementation, and embedded in the artifact:

- the devices an agent lists (what each declares, its tags and location);
- the intent classes it may fire (actions, sensors, location role);
- the resolver's plan for EVERY (class, urgency, place, action_types subset),
  where a place is any string some device is at (a location path prefix or, as
  protocol 0.5 still honours, a location written as a tag).

The hub's refusals and the validation of proposed actions are ported to the
artifact's JavaScript; tests/test_agent_lab_matches_the_hub.py checks the port
against the hub before any run.

    PYTHONPATH=. python3 tools/agent_eval/build_lab_data.py --out /tmp/lab_data.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DOSYNC_DB", ":memory:")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dosync.hub import DoSyncHub                                          # noqa: E402
from dosync.models import Intent, IntentClass, Urgency                     # noqa: E402
from tools.recall_benchmark import _register_domain_intents, load_registry  # noqa: E402

URGENCIES = ["info", "warning", "alert", "emergency"]
REGISTRIES = {"home": ("benchmarks/fixtures/prod_registry_anonymized.json",
                       ["benchmarks/fixtures/prod_ground_truth_operator.json",
                        "benchmarks/fixtures/prod_ground_truth_location.json"]),
              "plant": ("benchmarks/corpus/industrial_registry.json",
                        ["benchmarks/corpus/industrial_ground_truth.json"])}
SCENARIOS_V2 = REPO / "benchmarks/agent_eval/scenarios_v2.json"


def build_hub(env: str) -> DoSyncHub:
    """The hub for an environment: 'home'/'plant' as in the first comparison,
    'home-v2'/'plant-v2' with the frozen operator configuration applied."""
    base = env.replace("-v2", "")
    registry, truths = REGISTRIES[base]
    hub = DoSyncHub(db_path=":memory:")
    load_registry(REPO / registry, hub)
    for t in truths:
        _register_domain_intents(hub, json.loads((REPO / t).read_text(encoding="utf-8")))
    if env.endswith("-v2"):
        conf = json.loads(SCENARIOS_V2.read_text(encoding="utf-8"))["operator_configuration"][base]
        _register_domain_intents(hub, {"intent_classes": conf["intent_classes"]})
        for device_id, actions in conf["emergency_actions"].items():
            device = hub.registry.get(device_id)
            device.emergency_actions = [{"action": a["action"], "params": dict(a.get("params") or {})}
                                        for a in actions]
    return hub


def places(hub: DoSyncHub) -> list[str]:
    out = set()
    for d in hub.registry.active():
        out.update(d.tags)
        if d.location:
            parts = d.location.split("/")
            out.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return sorted(out)


def subsets(items):
    items = sorted(items)
    for r in range(1, len(items) + 1):
        yield from itertools.combinations(items, r)


def plan_key(cls, urgency, place, types):
    return f"{cls}|{urgency}|{place or ''}|{','.join(sorted(types)) if types else ''}"


def environment(env: str) -> dict:
    hub = build_hub(env)
    devices = [{"device_id": d.device_id, "name": d.device_name, "category": d.category.value,
                "tags": list(d.tags), "location": d.location or "",
                "actions": sorted({a.type for a in d.actuators}),
                "sensors": sorted({s.type for s in d.sensors}),
                "emergency_capable": bool(d.emergency_capable),
                "emergency_actions": [a["action"] for a in (d.emergency_actions or [])]}
               for d in hub.registry.active()]
    classes = []
    for c in hub.db.list_intent_classes():
        row = hub.db.get_intent_class(c["name"]) or {}
        classes.append({"name": c["name"], "description": c.get("description", ""),
                        "default_urgency": c.get("urgency", "info"),
                        "location_role": row.get("location_role", "restricts"),
                        "actuators": list(row.get("resolution_actuators") or []),
                        "sensors": list(row.get("resolution_sensors") or [])})
    known = places(hub)
    plans = {}
    for c in classes:
        type_options = [None] + [list(s) for s in subsets(c["actuators"])]
        for urgency in URGENCIES:
            for place in [None] + known:
                for types in type_options:
                    ctx = {}
                    if place:
                        ctx["location"] = place
                    if types:
                        ctx["action_types"] = types
                    plan = hub.resolver.resolve(Intent(intent=IntentClass(c["name"]),
                                                       urgency=Urgency(urgency), context=ctx))
                    plans[plan_key(c["name"], urgency, place, types)] = sorted(
                        {(a.device_id, a.action) for a in plan.actions})
    return {"devices": devices, "classes": classes, "places": known,
            "plans": {k: [list(x) for x in v] for k, v in plans.items()}}


def scenarios() -> list[dict]:
    out = []
    ids = ["R1", "R2", "R3", "R4", "R5", "L1", "L2", "L3", "L4", "L5", "L6", "I1", "I2", "I3", "I4", "I5"]
    phrases = json.loads((REPO / "benchmarks/agent_eval/round1_phrases.json").read_text(encoding="utf-8"))
    files = [("home", "benchmarks/fixtures/prod_ground_truth_operator.json"),
             ("home", "benchmarks/fixtures/prod_ground_truth_location.json"),
             ("plant", "benchmarks/corpus/industrial_ground_truth.json")]
    cases = [(env, c) for env, f in files for c in json.loads((REPO / f).read_text(encoding="utf-8"))["cases"]]
    for sid, (env, c) in zip(ids, cases):
        out.append({"id": sid, "set": "original", "env": env, "phrase": phrases[sid],
                    "intent": c["intent"], "urgency": c.get("urgency", "info"),
                    "location": (c.get("context") or {}).get("location"),
                    "expected_devices": sorted(c["expected"])})
    for s in json.loads(SCENARIOS_V2.read_text(encoding="utf-8"))["scenarios"]:
        out.append({"id": s["id"], "set": "new", "env": s["env"] + "-v2", "phrase": s["phrase"],
                    "expected_actions": s["expected"]})
    return out


def build() -> dict:
    return {"generated_from": "dosync reference hub (tools/agent_eval/build_lab_data.py)",
            "scenarios_v2_sha256": __import__("hashlib").sha256(SCENARIOS_V2.read_bytes()).hexdigest(),
            "environments": {e: environment(e) for e in ("home", "plant", "home-v2", "plant-v2")},
            "scenarios": scenarios()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = build()
    a.out.write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {a.out}: {sum(len(e['plans']) for e in data['environments'].values())} plans, "
          f"{len(data['scenarios'])} scenarios")
