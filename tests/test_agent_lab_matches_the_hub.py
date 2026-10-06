"""The agent-comparison laboratory answers exactly as the hub would.

The laboratory is a claude.ai artifact, which can call a model but not a hub.
Plans are precomputed by the hub (tools/agent_eval/build_lab_data.py); the
hub's refusals and the validation of proposed actions are ported to the
artifact's JavaScript. This runs the port and the hub on the same 1,200
seeded random requests -- unknown classes, invalid urgencies, misspelt places,
actions outside a class, malformed proposals -- and requires them to agree on
every one: the refusal reason, or the actions run and the proposals refused.
Skipped where Node.js is not installed.
"""
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "tools" / "agent_eval" / "agent-eval.template.html"
TEMPLATE_V3 = REPO / "tools" / "agent_eval" / "agent-eval-v3.template.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")


def _cases(data, rng):
    out = []
    for env, E in data["environments"].items():
        devs = E["devices"]
        for _ in range(300):
            c = rng.choice(E["classes"] + [None])
            cls = c["name"] if c else "no_such_class"
            urgency = rng.choice(["info", "warning", "alert", "emergency"] * 4 + ["urgent"])
            ctx = {}
            r = rng.random()
            if r < 0.35:
                ctx["location"] = rng.choice(E["places"])
            elif r < 0.5:
                ctx["location"] = rng.choice(["garage", "living room", "plant floor", "floor-9"])
            acts = c["actuators"] if c else []
            r = rng.random()
            if r < 0.3 and acts:
                ctx["action_types"] = rng.sample(acts, rng.randint(1, len(acts)))
            elif r < 0.36:
                ctx["action_types"] = rng.choice([["fly"], []])
            r = rng.random()
            if r < 0.45:
                props = []
                for _ in range(rng.randint(1, 5)):
                    d = rng.choice(devs + [None])
                    did = d["device_id"] if d else "ghost"
                    pool = (d["actions"] if d else []) + ["read_sensors", "fly"]
                    props.append({"device_id": did, "action": rng.choice(pool)})
                ctx["proposed_actions"] = props
            elif r < 0.5:
                ctx["proposed_actions"] = rng.choice([[], [{"device_id": 1}], "door:lock"])
            out.append({"env": env, "cls": cls, "urgency": urgency, "ctx": ctx})
    return out


def _hub_answers(cases, builder=None):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    from dosync.models import Intent, IntentClass, Urgency
    from tools.agent_eval.build_lab_data import build_hub
    builder = builder or build_hub
    answers, original = [], srv.hub
    try:
        with TestClient(srv.app) as client:
            for env in dict.fromkeys(c["env"] for c in cases):
                srv.hub = builder(env)
                for case in (c for c in cases if c["env"] == env):
                    before = client.get("/v1/status").json()["intents_rejected"]
                    r = client.post("/v1/intent/async", json={"intent": case["cls"], "urgency": case["urgency"],
                                                              "context": case["ctx"]})
                    if r.status_code == 422:
                        after = client.get("/v1/status").json()["intents_rejected"]
                        reason = [k for k in after if after[k] != before.get(k, 0)]
                        answers.append({"reason": reason[0] if len(reason) == 1 else str(reason)})
                        continue
                    assert r.status_code == 200, r.text
                    intent = Intent(intent=IntentClass(case["cls"]), urgency=Urgency(case["urgency"]),
                                    context=dict(case["ctx"]))
                    if case["ctx"].get("proposed_actions"):
                        plan = srv.hub.resolver.validate_proposals(intent)
                        refused = sorted((x["device_id"], x["action"], x["reason"]) for x in plan.refused_proposals)
                    else:
                        plan, refused = srv.hub.resolver.resolve(intent), []
                    answers.append({"actions": sorted([a.device_id, a.action] for a in plan.actions),
                                    "refused": [list(x) for x in refused]})
    finally:
        srv.hub = original
    return answers


def _port_answers(data, cases, tmp_path, template=TEMPLATE):
    js = template.read_text(encoding="utf-8")
    port = js[js.index("// ==== HUB PORT (begin)"):js.index("// ==== HUB PORT (end)")]
    (tmp_path / "data.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    (tmp_path / "run.js").write_text(
        "const fs=require('fs');\nconst DATA=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n" + port +
        """
const cases=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const out=cases.map(c=>{ const rej=precheck(c.env,c.cls,c.urgency,c.ctx); if(rej) return {reason:rej.reason};
  if (c.ctx.proposed_actions){ const v=validateProposals(c.env,c.cls,c.urgency,c.ctx);
    return {actions:v.actions.map(a=>[...a]).sort(), refused:v.refused.map(r=>[r.device_id,r.action,r.reason]).sort()}; }
  return {actions:resolvedPlan(c.env,c.cls,c.urgency,c.ctx).map(a=>[...a]).sort(), refused:[]}; });
process.stdout.write(JSON.stringify(out));
""", encoding="utf-8")
    run = subprocess.run(["node", str(tmp_path / "run.js"), str(tmp_path / "data.json"), str(tmp_path / "cases.json")],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr[-800:]
    return json.loads(run.stdout)


def test_the_laboratory_port_agrees_with_the_hub(tmp_path):
    from tools.agent_eval.build_lab_data import build
    data = build()
    cases = _cases(data, random.Random(20261002))
    hub, port = _hub_answers(cases), _port_answers(data, cases, tmp_path)

    def norm(a):
        return {k: sorted(map(tuple, v)) if isinstance(v, list) else v for k, v in a.items()}
    diffs = [(c, h, p) for c, h, p in zip(cases, hub, port) if norm(h) != norm(p)]
    kinds = {k: sum(1 for h in hub if h.get("reason") == k) for k in
             ("not_registered", "invalid_urgency", "unknown_location", "invalid_actions",
              "invalid_proposals", "ambiguous_actions")}
    assert all(kinds.values()), f"the random cases must exercise every refusal: {kinds}"
    assert sum(1 for h in hub if h.get("refused")) > 50, "too few proposal refusals exercised"
    assert not diffs, f"{len(diffs)} of {len(cases)} disagree; first: {diffs[0]}"


def test_the_third_round_laboratory_agrees_with_the_hub(tmp_path):
    """The same check on the third comparison's environments: the reference
    deployment with its 12 classes as registered (the wildcard of operate_device,
    classes left from other domains), the plant, and both with family 8's
    third-party Thing imported from its TD."""
    from tools.agent_eval.build_lab_data import build_hub_v3, build_v3
    data = build_v3()
    cases = _cases(data, random.Random(20261005))
    hub, port = _hub_answers(cases, builder=build_hub_v3), _port_answers(data, cases, tmp_path, TEMPLATE_V3)

    def norm(a):
        return {k: sorted(map(tuple, v)) if isinstance(v, list) else v for k, v in a.items()}
    diffs = [(c, h, p) for c, h, p in zip(cases, hub, port) if norm(h) != norm(p)]
    assert any(c["cls"] == "operate_device" for c in cases), "operate_device must be exercised"
    assert sum(1 for h in hub if h.get("reason") == "proposals_required") > 5
    assert not diffs, f"{len(diffs)} of {len(cases)} disagree; first: {diffs[0]}"
