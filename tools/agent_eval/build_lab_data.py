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


ROUND3 = {"operator": REPO / "benchmarks/agent_eval/operator_scenarios_v3.json",
          "attacks": REPO / "benchmarks/agent_eval/attacks_v3.json",
          "home_registry": REPO / "benchmarks/fixtures/prod_registry_2026_10_anonymized.json",
          "home_classes": REPO / "benchmarks/fixtures/prod_classes_2026_10.json"}

ROUND4 = {"operator": REPO / "benchmarks/agent_eval/operator_scenarios_v3.json",
          "attacks": REPO / "benchmarks/agent_eval/attacks_v4.json",
          "hardened": REPO / "benchmarks/agent_eval/hardened_config_v4.json",
          "home_registry": REPO / "benchmarks/fixtures/prod_registry_2026_10_anonymized.json",
          "home_classes": REPO / "benchmarks/fixtures/prod_classes_2026_10.json"}


def build_hub_v4(env: str, hardened: bool = False) -> DoSyncHub:
    """Fourth comparison: the same environments as the third (the reference
    deployment 'home-v3', the plant 'plant-v3', each with family injected-in-td's
    third-party Thing as '-poisoned'), on the hub WITH the new protections. When
    ``hardened`` is set, the operator's confirmation policies for the env's domain
    (hardened_config_v4.json) are loaded, exactly as the 'gdirect-hardened'
    condition runs them."""
    hub = build_hub_v3(env)
    if hardened:
        from dosync.policies import PolicyEngine
        from dosync.policy_config import load_policies
        domain = "home-v3" if env.replace("-poisoned", "") == "home-v3" else "plant-v3"
        conf = json.loads(ROUND4["hardened"].read_text(encoding="utf-8")).get(domain, {})
        if conf:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(conf, f)
                path = f.name
            hub.policy_engine = hub.policy_engine or PolicyEngine()
            for p in load_policies(path, hub=hub):
                hub.policy_engine.add(p)
            os.unlink(path)
    return hub


def build_hub_v3(env: str) -> DoSyncHub:
    """Third comparison: 'home-v3' is the reference deployment as exported on
    2026-10-05 (anonymized), with its 12 registered classes as they are;
    'plant-v3' is the plant with the second comparison's operator
    configuration. A '-poisoned' suffix adds family 8's third-party Thing,
    imported from its TD with the real importer."""
    from dosync.wot_import import import_td
    base = env.replace("-poisoned", "")
    if base == "home-v3":
        hub = DoSyncHub(db_path=":memory:")
        load_registry(ROUND3["home_registry"], hub)
        seeded = {c["name"] for c in hub.db.list_intent_classes()}
        for c in json.loads(ROUND3["home_classes"].read_text(encoding="utf-8"))["intent_classes"]:
            if c["name"] not in seeded:
                hub.db.save_intent_class(name=c["name"], urgency=c["urgency"],
                                         resolution_tags=c["resolution_tags"],
                                         resolution_actuators=c["resolution_actuators"],
                                         resolution_sensors=c.get("resolution_sensors") or [],
                                         location_role=c["location_role"],
                                         description=c["description"], domain=c["domain"],
                                         composition_kind=c.get("composition_kind"))
    elif base == "plant-v3":
        hub = build_hub("plant-v2")
    else:
        raise ValueError(env)
    if env.endswith("-poisoned"):
        attacks = json.loads(ROUND3["attacks"].read_text(encoding="utf-8"))
        domain = "home" if base == "home-v3" else "plant"
        manifest, _ = import_td(attacks["poisoned_things"][domain])
        manifest.location = attacks["poisoned_thing_place"][domain] or ""
        hub.register_device(manifest)
    return hub


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
    hub = build_hub_v3(env) if "-v3" in env else build_hub(env)
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


def scenarios_v3() -> list[dict]:
    out = []
    for s in json.loads(ROUND3["operator"].read_text(encoding="utf-8"))["scenarios"]:
        out.append({"id": s["id"], "set": "operator", "env": "home-v3", "phrase": s["phrase"],
                    "required": s["required"], "acceptable": s["acceptable"]})
    for a in json.loads(ROUND3["attacks"].read_text(encoding="utf-8"))["attacks"]:
        env = ("home-v3" if a["env"] == "home" else "plant-v3") + ("-poisoned" if a["family"] == 8 else "")
        out.append({"id": a["id"], "set": "attack", "env": env, "family": a["family"], "guarantee": a["guarantee"],
                    "phrase": a["phrase"], "required": a["required"], "forbidden": a["forbidden"],
                    "acceptable": a["acceptable"], "forbid_opposite_on": a.get("forbid_opposite_on", []),
                    "forbid_any_actuation": bool(a.get("forbid_any_actuation"))})
    return out


def build_v3() -> dict:
    import hashlib
    return {"generated_from": "dosync reference hub (tools/agent_eval/build_lab_data.py --round 3)",
            "round": 3,
            "inputs_sha256": {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in ROUND3.items()},
            "environments": {e: environment(e) for e in ("home-v3", "home-v3-poisoned", "plant-v3", "plant-v3-poisoned")},
            "scenarios": scenarios_v3()}


def scenarios_v4() -> list[dict]:
    """The fourth comparison's conversations: the 12 operator scenarios (reused
    from the third, for the cost-of-hardening measure) and the 13 new attacks,
    each carrying its per-condition prediction and what it forbids."""
    out = []
    for s in json.loads(ROUND4["operator"].read_text(encoding="utf-8"))["scenarios"]:
        out.append({"id": s["id"], "set": "operator", "env": "home-v3", "phrase": s["phrase"],
                    "required": s["required"], "acceptable": s["acceptable"]})
    for a in json.loads(ROUND4["attacks"].read_text(encoding="utf-8"))["attacks"]:
        env = ("home-v3" if a["env"] == "home" else "plant-v3") + (
            "-poisoned" if a["family"] == "injected-in-td" else "")
        out.append({"id": a["id"], "set": "attack", "env": env, "family": a["family"],
                    "phrase": a["phrase"], "forbidden": a.get("forbidden", []),
                    "prediction": a.get("prediction", {}),
                    "forbid_opposite_on": a.get("forbid_opposite_on", []),
                    "forbid_both": a.get("forbid_both", [])})
    return out


def build_v4() -> dict:
    import hashlib
    hardened = json.loads(ROUND4["hardened"].read_text(encoding="utf-8"))
    return {"generated_from": "dosync reference hub (tools/agent_eval/build_lab_data.py --round 4)",
            "round": 4,
            "inputs_sha256": {k: hashlib.sha256(p.read_bytes()).hexdigest() for k, p in ROUND4.items()},
            "environments": {e: environment(e) for e in ("home-v3", "home-v3-poisoned", "plant-v3", "plant-v3-poisoned")},
            # The operator's confirmation policies for the hardened condition, by
            # the env's domain. The lab applies these as the hub does (G9).
            "hardened_config": {dom: hardened.get(dom, {}).get("policies", [])
                                for dom in ("home-v3", "plant-v3")},
            "scenarios": scenarios_v4()}


def build() -> dict:
    return {"generated_from": "dosync reference hub (tools/agent_eval/build_lab_data.py)",
            "scenarios_v2_sha256": __import__("hashlib").sha256(SCENARIOS_V2.read_bytes()).hexdigest(),
            "environments": {e: environment(e) for e in ("home", "plant", "home-v2", "plant-v2")},
            "scenarios": scenarios()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--round", type=int, default=2, choices=(2, 3, 4))
    a = ap.parse_args()
    data = {2: build, 3: build_v3, 4: build_v4}[a.round]()
    a.out.write_text(json.dumps(data), encoding="utf-8")
    print(f"wrote {a.out}: {sum(len(e['plans']) for e in data['environments'].values())} plans, "
          f"{len(data['scenarios'])} scenarios")
