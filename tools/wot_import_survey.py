#!/usr/bin/env python3
"""How the TD importer maps public, third-party Thing Descriptions.

Runs dosync.wot_import.import_td over every Thing Description found under the
given directories and reports, per corpus:

  * distinct TDs (by content), how many validate against the W3C TD 1.1 JSON
    Schema (tools/vendor), and how many import without error;
  * actions and writable properties, and HOW each was mapped -- the dosync:
    vocabulary, a semantic @type, a verb in the name, a writable property, or
    the affordance's own name (with collisions counted separately);
  * Things whose mapped actions include a canonical opposite pair (lock/unlock,
    turn_on/turn_off, open/close, start/stop, arm/disarm), on which G4 then
    applies;
  * affordances the reference hub can invoke (an http(s) form) versus ones it
    governs but cannot invoke (CoAP, MQTT, Modbus, ...);
  * a round trip: the imported manifest registers with a hub and an
    operate_device proposal for each mapped action is accepted by the resolver
    (validate_proposals), i.e. every imported action is governable.

A corpus is a directory searched recursively for *.json / *.jsonld; a file may
hold one TD or a list of them. Anything that is not a TD (no WoT TD @context, no
title, no affordances) is skipped. Directories named ``invalid`` are skipped
(the Eclipse Thingweb playground keeps deliberately broken TDs there).

    PYTHONPATH=. python3 tools/wot_import_survey.py NAME=DIR [NAME=DIR ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "tools/vendor/w3c-wot-td-1.1-validation.schema.json"
OPPOSITE = ({"lock", "unlock"}, {"turn_on", "turn_off"}, {"open", "close"},
            {"start", "stop"}, {"arm", "disarm"})


def _is_td(x) -> bool:
    if not isinstance(x, dict) or "title" not in x:
        return False
    if not any(k in x for k in ("actions", "properties", "events")):
        return False
    ctx = json.dumps(x.get("@context", ""))
    return any(s in ctx for s in ("w3.org/2019/wot/td", "w3.org/2022/wot/td", "wot/td"))


def find_tds(root: Path):
    seen = set()
    for f in sorted(root.rglob("*")):
        if f.suffix not in (".json", ".jsonld") or not f.is_file():
            continue
        if "invalid" in f.parts or "node_modules" in f.parts:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for x in (data if isinstance(data, list) else [data]):
            if _is_td(x):
                h = hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()
                if h not in seen:
                    seen.add(h)
                    yield str(f.relative_to(root)), x


def survey(root: Path):
    from dosync.wot_import import import_td
    from dosync.hub import DoSyncHub
    from dosync.models import Intent, IntentClass, Urgency
    try:
        from jsonschema import Draft7Validator
        validator = Draft7Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    except Exception:          # schema validation is reported only when available
        validator = None

    r = collections.Counter()
    how = collections.Counter()
    failures = []
    for path, td in find_tds(root):
        r["tds"] += 1
        if validator is not None:
            r["schema_valid"] += not any(True for _ in validator.iter_errors(td))
        try:
            manifest, report = import_td(td)
        except Exception as e:  # noqa: BLE001 -- a survey records every failure
            failures.append({"file": path, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        r["imported"] += 1
        prov = report["provenance"]
        n_aff = len(prov)
        r["with_actuation"] += bool(n_aff)
        r["sensors"] += len(report["sensors"])
        for t, h in prov.items():
            how[h] += 1
        r["affordances"] += n_aff
        r["executable"] += sum(1 for v in report["executable"].values() if v)
        types = set(prov)
        if any(p <= types for p in OPPOSITE):
            r["things_with_opposite_pair"] += 1
        # governable: the hub accepts each mapped action, alone, as an
        # operate_device proposal; and proposing a Thing's opposite pair together
        # is refused (G4), which shows the mapping reached the guarantees.
        if n_aff:
            hub = DoSyncHub(db_path=":memory:")
            hub.register_device(manifest)

            def _accepts(actions):
                plan = hub.resolver.validate_proposals(Intent(
                    intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": manifest.device_id, "action": t}
                                                  for t in actions]}))
                return {a.action for a in plan.actions}, plan.refused_proposals

            for t in prov:
                ok, refused = _accepts([t])
                if t in ok:
                    r["governable"] += 1
                else:
                    failures.append({"file": path, "action": t, "refused": refused[:3]})
            for pair in OPPOSITE:
                if pair <= types:
                    ok, refused = _accepts(sorted(pair))
                    r["opposite_pairs"] += 1
                    r["opposite_pairs_refused_together"] += (not ok and all(
                        x.get("reason") == "opposite_actions" for x in refused))
    return {"counts": dict(r), "mapped_by": dict(how), "failures": failures}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("corpora", nargs="+", help="NAME=DIR")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    out = {}
    for spec in a.corpora:
        name, d = spec.split("=", 1)
        out[name] = survey(Path(d))
    text = json.dumps(out, indent=1)
    if a.json:
        a.json.write_text(text, encoding="utf-8")
    for name, res in out.items():
        print(name, json.dumps(res["counts"]), json.dumps(res["mapped_by"]),
              "failures:", len(res["failures"]))
        for f in res["failures"][:8]:
            print("   ", json.dumps(f)[:300])
