#!/usr/bin/env python3
"""Ablation: capability-only selection, unweighted.

A device takes part iff it declares an actuator or sensor the class needs (every
sensing device when the class needs none). No ranking, no location restriction,
no emergency rules; pre-policy. The difference between this and the live
resolver on the same corpus measures what the location restriction and the
emergency rules contribute, one at a time:

    PYTHONPATH=. python3 tools/capability_ablation.py
"""
import json, sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
from pathlib import Path
from tools.recall_benchmark import load_registry, _register_domain_intents
from dosync.hub import DoSyncHub

def run(registry, truth):
    hub = DoSyncHub(db_path=":memory:"); load_registry(Path(registry), hub)
    gt = json.loads(Path(truth).read_text()); _register_domain_intents(hub, gt)
    T = [0, 0, 0]
    for case in gt["cases"]:
        cls = hub.db.get_intent_class(case["intent"]) or {}
        need = set(cls.get("resolution_actuators") or []) | set(cls.get("resolution_sensors") or [])
        sel = set()
        for d in hub.registry.active():
            declared = {a.type for a in d.actuators} | {s.type for s in d.sensors}
            if (need and declared & need) or (not need and d.sensors):
                sel.add(d.device_id)
        exp = set(case["expected"])
        T = [T[0] + len(sel & exp), T[1] + len(sel - exp), T[2] + len(exp - sel)]
    tp, fp, fn = T; r = tp/(tp+fn) if tp+fn else 0; p = tp/(tp+fp) if tp+fp else 0
    return tp, fp, fn, r, p, (2*p*r/(p+r) if p+r else 0)

for name, reg, gt in (("residential", "benchmarks/fixtures/prod_registry_anonymized.json", "benchmarks/fixtures/prod_ground_truth_operator.json"),
                      ("location", "benchmarks/fixtures/prod_registry_anonymized.json", "benchmarks/fixtures/prod_ground_truth_location.json"),
                      ("industrial", "benchmarks/corpus/industrial_registry.json", "benchmarks/corpus/industrial_ground_truth.json")):
    tp, fp, fn, r, p, f = run(reg, gt)
    print(f"capability-only  {name:12} tp={tp:3} fp={fp:3} fn={fn:2} | R={r:.3f} P={p:.3f} F1={f:.3f}")
