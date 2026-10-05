#!/usr/bin/env python3
"""How DoSync's importer maps a corpus of third-party W3C WoT Thing Descriptions.

Run against the W3C plugfest repository (no TD is vendored here):

    git clone --depth 1 https://github.com/w3c/wot-testing /tmp/wot-testing
    PYTHONPATH=. python3 tools/wot_import_report.py /tmp/wot-testing

Measured 2026-10-05: 701 distinct TDs (by content), 433 distinct Things (by id);
all 701 import; of 2,145 governable affordances, 51% are writable properties,
40% keep their own names, 7% map by a verb in the name and 2% by @type; 64% have
an HTTP form the hub can execute.
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dosync.wot_import import import_td  # noqa: E402


def report(root: Path) -> dict:
    seen, tds = set(), []
    for f in sorted(root.rglob("*.json*")):
        if "TD" not in str(f) and not f.name.endswith(".td.json"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict) or "title" not in d or not ("properties" in d or "actions" in d):
            continue
        h = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            tds.append(d)
    ok, ids, prov, exe, failures = 0, set(), collections.Counter(), collections.Counter(), []
    for d in tds:
        try:
            m, rep = import_td(d)
        except Exception as e:
            failures.append(f"{d.get('title')}: {e}")
            continue
        ok += 1
        ids.add(m.device_id)
        for t, how in rep["provenance"].items():
            prov[how.split(" ")[0]] += 1
            exe["http" if rep["executable"][t] else "other"] += 1
    total = sum(prov.values())
    return {"tds": len(tds), "imported": ok, "distinct_things": len(ids), "failures": failures[:20],
            "affordances": total,
            "provenance": {k: {"n": v, "share": round(v / total, 3)} for k, v in prov.most_common()},
            "executable_over_http": round(exe["http"] / total, 3) if total else None}


if __name__ == "__main__":
    print(json.dumps(report(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/wot-testing")), indent=2))
