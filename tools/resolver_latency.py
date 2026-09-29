#!/usr/bin/env python3
"""Latency of the REAL resolver, as registries grow.

benchmarks/benchmark_resolver.py measures a May 2026 copy of the resolver, with
its own types and thirteen intent classes that no longer exist; its numbers must
not be quoted. This measures `hub.resolver.resolve()` -- the code a hub runs --
over registries of increasing size.

Registries are built by replicating the devices of two committed registries (the
reference deployment's anonymized snapshot and the synthetic industrial one),
each copy with a unique id and a place in a hierarchy
(site-S/floor-F/room-R), so a location-restricted intent has something to
restrict. Four intents are timed: an emergency, an alert, a status query, and a
deployment class restricted to one room.

    PYTHONPATH=. python3 tools/resolver_latency.py                    # full run
    PYTHONPATH=. python3 tools/resolver_latency.py --sizes 10 100 --runs 50
    PYTHONPATH=. python3 tools/resolver_latency.py --json latency.json

Report the platform the numbers came from: the JSON records it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("DOSYNC_DB", ":memory:")
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dosync.hub import DoSyncHub                                    # noqa: E402
from dosync.models import Intent, IntentClass, Urgency              # noqa: E402
from tools.recall_benchmark import load_registry                    # noqa: E402

SOURCES = (REPO / "benchmarks/fixtures/prod_registry_anonymized.json",
           REPO / "benchmarks/corpus/industrial_registry.json")
ROOMS_PER_FLOOR, FLOORS_PER_SITE = 4, 5
RESTRICTED_ROOM = "site-0/floor-0/room-0"
INTENTS = (
    ("ensure_safety", Urgency.EMERGENCY, {}),
    ("alert_anomaly", Urgency.ALERT, {}),
    ("report_status", Urgency.INFO, {}),
    ("light_on_presence", Urgency.INFO, {"location": RESTRICTED_ROOM}),
)


def _templates() -> list[dict]:
    devices = []
    for src in SOURCES:
        devices.extend(json.loads(src.read_text(encoding="utf-8"))["devices"])
    return devices


def _registry(size: int, templates: list[dict]) -> dict:
    devices = []
    for i in range(size):
        d = copy.deepcopy(templates[i % len(templates)])
        room = i // 3                      # about three devices per room
        d["device_id"] = f"{d['device_id']}-{i:05d}"
        d["location"] = (f"site-{room // (ROOMS_PER_FLOOR * FLOORS_PER_SITE)}/"
                         f"floor-{(room // ROOMS_PER_FLOOR) % FLOORS_PER_SITE}/"
                         f"room-{room % ROOMS_PER_FLOOR}")
        devices.append(d)
    return {"devices": devices}


def _hub(size: int, templates: list[dict], tmp: Path) -> DoSyncHub:
    path = tmp / f"registry-{size}.json"
    path.write_text(json.dumps(_registry(size, templates)), encoding="utf-8")
    hub = DoSyncHub(db_path=":memory:")
    load_registry(path, hub)
    # load_registry builds manifests from the fixture shape; placements are set
    # the way an operator sets them.
    placements = {d["device_id"]: d["location"] for d in _registry(size, templates)["devices"]}
    for device in hub.registry.active():
        device.location = placements.get(device.device_id, "")
    hub.db.save_intent_class(name="light_on_presence", urgency="info",
                             resolution_tags=["light"], resolution_actuators=["turn_on"],
                             location_role="restricts", description="latency probe",
                             domain="deployment")
    return hub


def measure(sizes: list[int], runs: int, warmup: int) -> dict:
    import tempfile
    templates = _templates()
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for size in sizes:
            hub = _hub(size, templates, Path(tmp))
            for name, urgency, context in INTENTS:
                intent = Intent(intent=IntentClass(name), urgency=urgency, context=dict(context))
                for _ in range(warmup):
                    hub.resolver.resolve(intent)
                samples = []
                for _ in range(runs):
                    t0 = time.perf_counter()
                    plan = hub.resolver.resolve(intent)
                    samples.append((time.perf_counter() - t0) * 1000.0)
                samples.sort()
                results.append({
                    "devices": size, "intent": name, "urgency": urgency.value,
                    "actions": len(plan.actions),
                    "median_ms": round(statistics.median(samples), 3),
                    "p95_ms": round(samples[max(0, int(0.95 * len(samples)) - 1)], 3),
                    "runs": runs,
                })
    return {
        "platform": {"python": platform.python_version(), "machine": platform.machine(),
                     "system": platform.system(), "processor": platform.processor() or "",
                     "node": "redacted"},
        "resolver": "hub.resolver (live), dosync " + __import__("dosync").__version__,
        "results": results,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 100, 250, 500, 1000])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    report = measure(args.sizes, args.runs, args.warmup)
    p = report["platform"]
    print(f"{report['resolver']} · Python {p['python']} · {p['system']} {p['machine']}")
    print(f"{'devices':>8}  {'intent':<18} {'actions':>7}  {'median ms':>9}  {'p95 ms':>7}")
    for r in report["results"]:
        print(f"{r['devices']:>8}  {r['intent']:<18} {r['actions']:>7}  {r['median_ms']:>9.3f}  {r['p95_ms']:>7.3f}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
