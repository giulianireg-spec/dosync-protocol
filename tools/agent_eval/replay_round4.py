#!/usr/bin/env python3
"""Replay the fourth comparison against the reference hub.

The fourth comparison ran its agent against the laboratory's JavaScript port of
the hub (parity checked on 1,200 random requests). This replays what the agent
actually did -- every intent it fired, in order, in every run -- against the
reference hub itself (the real FastAPI server and hub, via its HTTP API, with the
same environments and, for the hardened condition, the same frozen operator
configuration), and checks that the hub decides exactly what the laboratory
recorded: which intents it refused and why, which actions ran, which were held
for confirmation.

What is replayable. The laboratory recorded, per intent, its class, urgency,
place and action_types, and for a proposal intent only the NUMBER of proposals;
the proposals themselves are recovered from the run's outcome (what ran, was
held or was refused). That recovery is exact when a run has at most one proposal
intent that the hub accepted; a run with several cannot be split back into its
intents, and is reported as not replayable rather than guessed. Runs in the
'direct' condition have no hub and are not replayed.

This does not model how the agent would have adapted to a different hub; it
checks that, given what the agent did, the reference hub decides the same.

    PYTHONPATH=. python3 tools/agent_eval/replay_round4.py [runs.json] [--json out.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "benchmarks/agent_eval/round4_runs.json"
ATTACKS = REPO / "benchmarks/agent_eval/attacks_v4.json"
OPERATOR = REPO / "benchmarks/agent_eval/operator_scenarios_v3.json"
MAX_SPLITS = 200


def env_of(run, attacks):
    if run["id"] in attacks:
        a = attacks[run["id"]]
        return ("home-v3" if a["env"] == "home" else "plant-v3") + (
            "-poisoned" if a["family"] == "injected-in-td" else "")
    return "home-v3"


def recover_proposals(run, resolved):
    """The proposals of a run's single accepted proposal intent, from its outcome:
    everything that ran, was held or was refused, minus what the run's resolution
    intents dispatched (computed by the reference hub, not taken from the lab)."""
    out = collections.Counter(tuple(a) for a in run["actions"])
    out.update((h[0], h[1]) for h in run.get("held") or [])
    out.update((r["device_id"], r["action"]) for r in run.get("refusals") or [])
    out.subtract(collections.Counter(resolved))
    return [k for k, n in out.items() for _ in range(max(n, 0))]


def _fire(client, it, props):
    """Fire one recorded intent; returns the 422 reason, or (ran, held, refused)."""
    import time
    ctx = {}
    if it.get("loc") is not None:
        ctx["location"] = it["loc"]
    if it.get("types") is not None:
        ctx["action_types"] = it["types"]
    if props:
        ctx["proposed_actions"] = [{"device_id": d, "action": a} for d, a in props]
    before = client.get("/v1/status").json()["intents_rejected"]
    r = client.post("/v1/intent/async", json={"intent": it["cls"], "urgency": it["urg"], "context": ctx})
    if r.status_code == 422:
        after = client.get("/v1/status").json()["intents_rejected"]
        reason = [k for k in after if after[k] != before.get(k, 0)]
        return reason[0] if len(reason) == 1 else str(reason)
    r.raise_for_status()
    iid = r.json()["intent_id"]
    for _ in range(500):
        body = client.get(f"/v1/intent/{iid}").json()
        if body.get("status") != "pending":
            break
        time.sleep(0.01)
    # dispatched, whatever the (absent) device answered: the decision is the hub's
    ran = [(x["device_id"], x["action"]) for x in body.get("results") or []]
    held = [(h["device_id"], h["action"]) for h in body.get("held_for_confirmation") or []]
    refused = [(x["device_id"], x["action"], x.get("reason")) for x in body.get("refused_proposals") or []]
    return ran, held, refused


def _want(run):
    return {
        "ran": sorted(map(tuple, run["actions"])),
        "held": sorted((h[0], h[1]) for h in run.get("held") or []),
        "refused": sorted((x["device_id"], x["action"], x.get("reason")) for x in run.get("refusals") or []),
        "rejected": [i["refused"] for i in run["intents"] if "refused" in i],
    }


def _replay_split(client, run, attacks, mode, srv, build_hub_v4):
    """A run with several accepted proposal intents: the record keeps each intent's
    NUMBER of proposals but not which ones. Replay EVERY split of the recovered
    proposals into intents of those sizes, and report under how many of them the
    reference hub decides exactly what the laboratory recorded."""
    import itertools
    env, hard = env_of(run, attacks), (mode == "gdirect-hardened")
    resolved = []
    srv.hub = build_hub_v4(env, hardened=hard)
    for it in run["intents"]:
        if not it["proposals"]:
            res = _fire(client, it, None)
            if not isinstance(res, str):
                resolved += res[0]
    pool = recover_proposals(run, resolved)
    sizes = [it["proposals"] for it in run["intents"] if it["proposals"] and "refused" not in it]
    idx = list(range(len(pool)))

    def splits(rest, sizes):
        if not sizes:
            yield []
            return
        for comb in itertools.combinations(rest, sizes[0]):
            left = [i for i in rest if i not in comb]
            for tail in splits(left, sizes[1:]):
                yield [comb] + tail

    from math import comb as _c
    n, count = len(pool), 1
    for k in sizes:
        count *= _c(n, k); n -= k
    total = same = 0
    if count > MAX_SPLITS:
        return {"id": run["id"], "mode": mode, "rep": run.get("rep"), "splits": 0,
                "identical_under": 0, "pool": len(pool), "sizes": sizes,
                "not_replayed": f"{count} splits, over the cap of {MAX_SPLITS}"}
    if sum(sizes) == len(pool):
        for sp in splits(idx, sizes):
            total += 1
            srv.hub = build_hub_v4(env, hardened=hard)
            parts = iter(sp)
            ran, held, refused, rejected = [], [], [], []
            for it in run["intents"]:
                props = [pool[i] for i in next(parts)] if it["proposals"] and "refused" not in it else None
                res = _fire(client, it, props)
                if isinstance(res, str):
                    rejected.append(res)
                    continue
                ran += res[0]; held += res[1]; refused += res[2]
            got = {"ran": sorted(ran), "held": sorted(held), "refused": sorted(refused), "rejected": rejected}
            same += got == _want(run)
    return {"id": run["id"], "mode": mode, "rep": run.get("rep"), "splits": total,
            "identical_under": same, "pool": len(pool), "sizes": sizes}


def replay(runs, attacks):
    import os
    os.environ.setdefault("DOSYNC_AUTH", "false")
    os.environ.setdefault("DOSYNC_DB", ":memory:")
    from fastapi.testclient import TestClient
    import dosync.server as srv
    from tools.agent_eval.build_lab_data import build_hub_v4

    out = {"replayed": 0, "identical": 0, "not_replayable": collections.Counter(),
           "skipped_direct": 0, "mismatches": [], "several_proposal_intents": [], "by_condition": collections.Counter()}
    original = srv.hub
    try:
        with TestClient(srv.app) as client:
            for run in runs:
                mode = run["mode"]
                if mode == "direct":
                    out["skipped_direct"] += 1
                    continue
                accepted_props = [i for i in run["intents"] if i["proposals"] and "refused" not in i]
                refused_props = [i for i in run["intents"] if i["proposals"] and "refused" in i]
                if len(accepted_props) > 1:
                    verdict = _replay_split(client, run, attacks, mode, srv, build_hub_v4)
                    out["several_proposal_intents"].append(verdict)
                    if "not_replayed" in verdict:
                        out["not_replayable"]["several proposal intents, too many splits"] += 1
                    elif verdict["identical_under"] != verdict["splits"]:
                        out["mismatches"].append(verdict)
                    continue
                if refused_props:
                    out["not_replayable"]["a refused proposal intent (proposals not recorded)"] += 1
                    continue
                env, hard = env_of(run, attacks), (mode == "gdirect-hardened")
                resolved = []
                if accepted_props:
                    # first pass: what the run's resolution intents dispatch, alone
                    srv.hub = build_hub_v4(env, hardened=hard)
                    for it in run["intents"]:
                        if it["proposals"]:
                            continue
                        res = _fire(client, it, None)
                        if res:
                            resolved += res[0]
                props = recover_proposals(run, resolved) if accepted_props else None
                srv.hub = build_hub_v4(env, hardened=hard)
                ran, held, refused, rejected = [], [], [], []
                for it in run["intents"]:
                    res = _fire(client, it, props if it["proposals"] else None)
                    if isinstance(res, str):
                        rejected.append(res)
                        continue
                    ran += res[0]; held += res[1]; refused += res[2]
                want = {
                    "ran": sorted(map(tuple, run["actions"])),
                    "held": sorted((h[0], h[1]) for h in run.get("held") or []),
                    "refused": sorted((x["device_id"], x["action"], x.get("reason"))
                                      for x in run.get("refusals") or []),
                    "rejected": [i["refused"] for i in run["intents"] if "refused" in i],
                }
                got = {"ran": sorted(ran), "held": sorted(held), "refused": sorted(refused),
                       "rejected": rejected}
                out["replayed"] += 1
                out["by_condition"][mode] += 1
                if got == want:
                    out["identical"] += 1
                else:
                    out["mismatches"].append({"id": run["id"], "mode": mode, "rep": run.get("rep"),
                                              "want": want, "got": got})
    finally:
        srv.hub = original
    out["not_replayable"] = dict(out["not_replayable"])
    out["by_condition"] = dict(out["by_condition"])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="?", type=Path, default=RUNS)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    runs = json.loads(a.runs.read_text(encoding="utf-8"))
    runs = runs["runs"] if isinstance(runs, dict) else runs
    attacks = {x["id"]: x for x in json.loads(ATTACKS.read_text(encoding="utf-8"))["attacks"]}
    res = replay(runs, attacks)
    text = json.dumps(res, indent=1, default=list)
    if a.json:
        a.json.write_text(text, encoding="utf-8")
    summary = {k: v for k, v in res.items() if k != "mismatches"}
    print(json.dumps(summary, indent=1))
    print("mismatches:", len(res["mismatches"]))
    for m in res["mismatches"][:20]:
        print(json.dumps(m))
    sys.exit(0 if not res["mismatches"] else 1)
