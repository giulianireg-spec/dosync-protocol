#!/usr/bin/env python3
"""Govern two of Eclipse Thingweb node-wot's own example Things, end to end.

The Things are node-wot's examples/scripts/counter.js and
smart-coffee-machine.js, served over HTTP -- written by the Eclipse project, not
by DoSync. The hub imports their TDs, places them (the operator's decision), and
an agent's proposals go through operate_device: what passes runs on the real
Thing, and what is refused -- an undeclared action, a place the class is
confined from, an operator policy -- never reaches it.

    npm install @node-wot/cli
    curl -O https://raw.githubusercontent.com/eclipse-thingweb/node-wot/master/examples/scripts/counter.js
    curl -O https://raw.githubusercontent.com/eclipse-thingweb/node-wot/master/examples/scripts/smart-coffee-machine.js
    echo '{"servient":{"clientOnly":false},"http":{"port":8090}}' > wot-servient.conf.json
    npx wot-servient -f wot-servient.conf.json counter.js smart-coffee-machine.js &
    PYTHONPATH=. python3 tools/wot_nodewot_e2e.py

Run 2026-10-05: increment ran (count 0 -> 1); an undeclared action, an
increment outside the named place, and an increment under a policy blocking
operate_device were refused and left the count unchanged; reset ran (-> 0).
"""
import asyncio, json, urllib.request, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from dosync.hub import DoSyncHub
from dosync.adapters import AdapterExecutor
from dosync.adapters.wot import WotHttpAdapter
from dosync.models import Intent, IntentClass, Urgency
from dosync.wot_import import import_td
from dosync.policies import PolicyEngine, BlockIntentPolicy

def get(url): return json.loads(urllib.request.urlopen(url, timeout=5).read() or "null")
hub = DoSyncHub(db_path=":memory:")
ex = AdapterExecutor(hub); ex.register(WotHttpAdapter(hub=hub))
things = {}
for t, place in (("counter", "lab/bench"), ("smart-coffee-machine", "lab/kitchen")):
    m, rep = import_td(get(f"http://localhost:8090/{t}"))
    m.location = place
    hub.register_device(m); things[t] = m.device_id
    print(f"imported {t!r} as {m.device_id!r} at {place}: {rep['provenance']} | executable: {rep['executable']}")
cid = things["counter"]
count = lambda: get("http://localhost:8090/counter/properties/count")

async def operate(proposals, **ctx):
    r = await hub.execute_intent(Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                                        context={**ctx, "proposed_actions": proposals}), ex)
    return [(x.action, x.success, x.error, (x.response or {}) if isinstance(x.response, dict) else x.response) for x in r.results], [(x["action"], x["reason"]) for x in r.refused_proposals]

async def main():
    c0 = count()
    print("\n1) increment the real counter:", await operate([{"device_id": cid, "action": "increment"}]), f"| count {c0} → {count()}")
    c1 = count()
    print("2) an undeclared action:", await operate([{"device_id": cid, "action": "explode"}]), f"| count {c1} → {count()}")
    print("3) outside the named place:", await operate([{"device_id": cid, "action": "increment"}], location="lab/kitchen"), f"| count {c1} → {count()}")
    print("4) reset (mapped by its verb):", await operate([{"device_id": cid, "action": "reset"}]), f"| count → {count()}")
    hub.policy_engine = PolicyEngine(); hub.policy_engine.add(BlockIntentPolicy(intent_classes=["operate_device"], reason="no direct control"))
    c2 = count()
    print("5) under a policy blocking operate_device:", await operate([{"device_id": cid, "action": "increment"}]), f"| count {c2} → {count()}")
    kinds = [e.get("type") for e in hub.audit_log.entries()]
    print("\naudit log:", {k: kinds.count(k) for k in set(kinds) if k and ("intent" in k or "polic" in k)})
asyncio.run(main())
