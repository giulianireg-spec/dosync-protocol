#!/usr/bin/env python3
"""samuel-certify -- does a hub conform to SAMUEL?

Runs the conformance checks of SAMUEL-SPEC-v0.2 §12 against any hub that
implements the SAMUEL HTTP binding (§11), DoSync or not. It needs nothing but
the Python standard library, so an implementer can run it without installing
DoSync:

    python3 samuel_certify.py --url https://hub.example:47200 \\
        --agent-token AGENT --operator-token OPERATOR [--ca ca.crt] [--json out.json]

The hub must be in conformance mode: the checks register their own devices and
classes (all prefixed ``samuel-cert-``) and set policies over the binding, which
a hub accepts only there. The devices are a production cell, a line and a
hangar -- SAMUEL governs any physical deployment, and the suite says so.

Exit status 0 when every check passes.
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field

PREFIX = "samuel-cert-"


@dataclass
class Check:
    id: str
    guarantee: str
    title: str
    passed: bool = False
    detail: str = ""


@dataclass
class Report:
    url: str
    checks: list = field(default_factory=list)

    def add(self, cid, guarantee, title, passed, detail=""):
        self.checks.append(Check(cid, guarantee, title, bool(passed), str(detail)[:400]))

    @property
    def conforms(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


class Hub:
    def __init__(self, url, agent_token, operator_token, ca=None, insecure=False):
        self.url = url.rstrip("/")
        self.agent, self.operator = agent_token, operator_token
        if ca:
            self.ctx = ssl.create_default_context(cafile=ca)
        elif insecure:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = None

    def call(self, method, path, body=None, operator=False, agent=True):
        headers = {"Content-Type": "application/json"}
        if agent and self.agent:
            headers["Authorization"] = f"Bearer {self.agent}"
        if operator and self.operator:
            headers["X-Operator-Token"] = self.operator
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else {}
            except ValueError:
                return e.code, {"raw": raw.decode(errors="replace")}

    def fire(self, cls, urgency="info", **context):
        return self.call("POST", "/samuel/v0/intents",
                         {"class": cls, "urgency": urgency, "context": context})


def _reason(body):
    d = body.get("detail", body) if isinstance(body, dict) else {}
    return d.get("reason") if isinstance(d, dict) else None


def _pairs(items):
    return {(x["device_id"], x["action"]) for x in items or []}


def _refused(decision):
    return {(x["device_id"], x["action"]): x["reason"] for x in decision.get("refused") or []}


def _propose(*pairs):
    return [{"device_id": d, "action": a} for d, a in pairs]


D = {  # the scene: three places, five devices, none of them in a house
    "cell":     PREFIX + "cell-2-lock",
    "cell30":   PREFIX + "line-30-cell-lock",
    "conveyor": PREFIX + "line-3-conveyor",
    "fan":      PREFIX + "line-3-extractor",
    "dock":     PREFIX + "hangar-1-dock-door",
}
CLS_ACCESS = "samuel_cert_access"


def setup(hub: Hub, report: Report) -> bool:
    devices = {
        D["cell"]: {"actions": [{"type": "lock"}, {"type": "unlock"}], "place": "site-a/line-3/cell-2"},
        D["cell30"]: {"actions": [{"type": "lock"}, {"type": "unlock"}], "place": "site-a/line-30"},
        D["conveyor"]: {"actions": [{"type": "start"}, {"type": "stop"}], "place": "site-a/line-3",
                        "emergency_capable": True, "emergency_actions": [{"action": "stop"}]},
        D["fan"]: {"actions": [{"type": "turn_on"}, {"type": "turn_off"}], "place": "site-a/line-3"},
        D["dock"]: {"actions": [{"type": "open"}, {"type": "close"}], "place": "site-a/hangar-1/bay-2",
                    "sensors": [{"type": "contact", "kind": "device_state"}]},
    }
    ok = True
    for did, body in devices.items():
        st, b = hub.call("PUT", f"/samuel/v0/devices/{did}", body, operator=True)
        ok &= st == 200
    st, _ = hub.call("PUT", f"/samuel/v0/classes/{CLS_ACCESS}",
                     {"grants": ["lock", "unlock"], "urgency": "alert", "location_role": "restricts"},
                     operator=True)
    ok &= st == 200
    st, _ = hub.call("PUT", "/samuel/v0/policies", {}, operator=True)
    ok &= st == 200
    report.add("S00", "-", "The hub accepts the scene: devices, a class, policies (conformance mode)",
               ok, "" if ok else "set-up failed; the hub must run in conformance mode with both credentials")
    return ok


def run(hub: Hub) -> Report:
    r = Report(hub.url)

    # S01 -- what the hub says it is (G8's report)
    st, info = hub.call("GET", "/samuel/v0")
    r.add("S01", "G8", "The hub reports the SAMUEL binding, direct control closed or operator-only, "
                       "and configuration reserved to the operator",
          st == 200 and info.get("binding") == "samuel/v0"
          and info.get("direct_control") in ("closed", "operator")
          and info.get("configuration") == "operator",
          f"status {st}: {info.get('binding')}, direct_control={info.get('direct_control')}, "
          f"configuration={info.get('configuration')}")
    if st != 200:
        return r

    # S02 -- placement is the operator's: an agent cannot register or move a device
    st, _ = hub.call("PUT", f"/samuel/v0/devices/{D['cell']}",
                     {"actions": [{"type": "lock"}], "place": "elsewhere"}, operator=False)
    r.add("S02", "G8", "An agent's credential cannot register or place a device (403)", st == 403, f"status {st}")

    if not setup(hub, r):
        return r

    # S03 -- G1 declared
    st, dec = hub.fire("operate_device", proposed_actions=_propose(
        (PREFIX + "no-such-device", "stop"), (D["conveyor"], "unlock")))
    ref = _refused(dec)
    r.add("S03", "G1", "An unknown device and an undeclared action are refused, and nothing runs",
          st == 200 and ref.get((PREFIX + "no-such-device", "stop")) == "unknown_device"
          and ref.get((D["conveyor"], "unlock")) == "not_declared" and not dec.get("ran"),
          f"status {st}: refused={ref} ran={dec.get('ran')}")

    # S04 -- G2 authorized: a class bounds what an intent may do
    st, dec = hub.fire("notify", proposed_actions=_propose((D["cell"], "unlock")))
    r.add("S04", "G2", "An action outside the intent's class is refused (no unlocking inside notify)",
          st == 200 and _refused(dec).get((D["cell"], "unlock")) == "outside_class"
          and not dec.get("ran"), f"status {st}: {dec}")

    # S05 -- G2: narrowing outside the class
    st, b = hub.fire(CLS_ACCESS, "alert", action_types=["start"])
    r.add("S05", "G2", "action_types outside the class is refused as a whole (422 invalid_actions)",
          st == 422 and _reason(b) == "invalid_actions", f"status {st}: {_reason(b)}")

    # S06 -- G3 placed, by whole segment
    st, dec = hub.fire("operate_device", location="site-a/line-3",
                       proposed_actions=_propose((D["cell"], "lock"), (D["cell30"], "lock")))
    r.add("S06", "G3", "A restricting place contains what lies below it, by whole segment "
                       "(line-3 holds line-3/cell-2, not line-30)",
          st == 200 and (D["cell"], "lock") in _pairs(dec.get("ran"))
          and _refused(dec).get((D["cell30"], "lock")) == "outside_place",
          f"status {st}: ran={_pairs(dec.get('ran'))} refused={_refused(dec)}")

    # S07 -- G3: an unknown place is refused, except in an emergency
    st, b = hub.fire(CLS_ACCESS, "alert", location="site-a/line-9", action_types=["lock"])
    st_e, b_e = hub.fire("ensure_safety", "emergency", location="site-z")
    r.add("S07", "G3", "An unknown restricting place is refused (422 unknown_location); "
                       "an emergency never is",
          st == 422 and _reason(b) == "unknown_location" and st_e == 200,
          f"status {st} {_reason(b)}; emergency status {st_e}")

    # S08 -- G3 on the hub's own resolution (when it resolves)
    if info.get("resolves"):
        st, dec = hub.fire(CLS_ACCESS, "alert", location="site-a/line-3", action_types=["lock"])
        ran = _pairs(dec.get("ran"))
        r.add("S08", "G3", "Resolution confined to the place: the cell in line-3 locks, line-30 does not",
              st == 200 and (D["cell"], "lock") in ran and (D["cell30"], "lock") not in ran,
              f"status {st}: ran={ran}")

    # S09 -- G4 consistent within an intent
    st, b = hub.fire(CLS_ACCESS, "alert")
    st2, dec = hub.fire("operate_device", proposed_actions=_propose((D["dock"], "open"), (D["dock"], "close")))
    ref = _refused(dec)
    r.add("S09", "G4", "Opposite actions are never sent together: an ambiguous class is refused "
                       "(422 ambiguous_actions) and a proposed pair is refused (opposite_actions)",
          st == 422 and _reason(b) == "ambiguous_actions"
          and ref.get((D["dock"], "open")) == "opposite_actions"
          and ref.get((D["dock"], "close")) == "opposite_actions" and not dec.get("ran"),
          f"status {st} {_reason(b)}; {ref}")

    # S10 -- G4 no oscillation across intents
    max_rev = int((info.get("oscillation") or {}).get("max_reversals", 1))
    window = float((info.get("oscillation") or {}).get("window_seconds", 60))
    seq = ["turn_on", "turn_off"] * (max_rev + 2)
    last = None
    for act in seq[:max_rev + 2]:
        _, last = hub.fire("operate_device", proposed_actions=_propose((D["fan"], act)))
    dropped = _refused(last or {}).get((D["fan"], seq[max_rev + 1])) == "oscillation"
    r.add("S10", "G4", f"A device reversed more than {max_rev} time(s) within {window:g} s, across "
                       "intents, is not reversed again (oscillation)",
          window <= 0 or dropped, f"last decision: {last}")

    # S11 -- G5 declared first, on proposals
    st, dec = hub.fire("ensure_safety", "emergency",
                       proposed_actions=_propose((D["conveyor"], "start"), (D["conveyor"], "stop")))
    r.add("S11", "G5", "In an emergency a device does exactly its declared emergency actions: stop runs "
                       "although the class does not grant it, start is refused",
          st == 200 and (D["conveyor"], "stop") in _pairs(dec.get("ran"))
          and _refused(dec).get((D["conveyor"], "start")) == "not_its_emergency_action",
          f"status {st}: {dec}")

    # S12 -- G5 on the hub's own resolution
    if info.get("resolves"):
        st, dec = hub.fire("ensure_safety", "emergency", location="site-a/line-3")
        acts = {a for d, a in _pairs(dec.get("ran")) if d == D["conveyor"]}
        r.add("S12", "G5", "A resolved emergency makes the conveyor stop, and only stop",
              st == 200 and acts == {"stop"}, f"status {st}: conveyor did {acts}")

    # S13 -- G7 policed (with a control: the same intent, before and after)
    _, before = hub.fire(CLS_ACCESS, "alert", location="site-a/line-3", action_types=["lock"])
    hub.call("PUT", "/samuel/v0/policies",
             {"exclusions": [{"classes": [CLS_ACCESS], "devices": [D["cell"]]}]}, operator=True)
    st, dec = hub.fire(CLS_ACCESS, "alert", location="site-a/line-3", action_types=["lock"])
    r.add("S13", "G7", "An operator's exclusion removes a device from a plan before it runs",
          D["cell"] in {d for d, _ in _pairs(before.get("ran"))}
          and st == 200 and D["cell"] not in {d for d, _ in _pairs(dec.get("ran"))},
          f"before: {_pairs(before.get('ran'))}; after: status {st} {_pairs(dec.get('ran'))}")
    hub.call("PUT", "/samuel/v0/policies", {}, operator=True)

    # S14-S16 -- G9 confirmed
    hub.call("PUT", "/samuel/v0/policies",
             {"confirmations": [{"actions": ["unlock", "start"]}]}, operator=True)
    st, dec = hub.fire("operate_device", proposed_actions=_propose((D["cell"], "unlock"), (D["dock"], "open")))
    held, ran = _pairs(dec.get("held")), _pairs(dec.get("ran"))
    r.add("S14", "G9", "A marked action is held for a person and the rest of the plan runs",
          st == 200 and (D["cell"], "unlock") in held and (D["cell"], "unlock") not in ran
          and (D["dock"], "open") in ran, f"status {st}: held={held} ran={ran}")
    iid = dec.get("intent_id")
    st_a, _ = hub.call("POST", f"/samuel/v0/intents/{iid}/confirm", operator=False)
    st_o, conf = hub.call("POST", f"/samuel/v0/intents/{iid}/confirm", operator=True)
    released = any(x.get("device_id") == D["cell"] and x.get("action") == "unlock"
                   for x in (conf.get("results") or []))
    r.add("S15", "G9", "Only the operator credential releases a hold: the agent's is refused (403)",
          st_a == 403 and st_o == 200 and released, f"agent {st_a}, operator {st_o}: {conf}")
    hub.call("PUT", "/samuel/v0/policies", {"confirmations": [{"actions": ["stop"]}]}, operator=True)
    st, dec = hub.fire("ensure_safety", "emergency", proposed_actions=_propose((D["conveyor"], "stop")))
    hub.call("PUT", "/samuel/v0/policies",
             {"confirmations": [{"actions": ["stop"], "even_in_emergency": True}]}, operator=True)
    st2, dec_e = hub.fire("ensure_safety", "emergency", proposed_actions=_propose((D["conveyor"], "stop")))
    r.add("S16", "G9", "In an emergency a device's declared emergency action is not held by a mark, "
                       "unless the mark says even_in_emergency",
          st == 200 and (D["conveyor"], "stop") in _pairs(dec.get("ran"))
          and st2 == 200 and (D["conveyor"], "stop") in _pairs(dec_e.get("held")),
          f"marked: ran={_pairs(dec.get('ran'))}; even_in_emergency: held={_pairs(dec_e.get('held'))}")
    if dec_e.get("intent_id"):
        hub.call("POST", f"/samuel/v0/intents/{dec_e['intent_id']}/deny", operator=True)
    hub.call("PUT", "/samuel/v0/policies",
             {"confirmations": [{"actions": ["unlock", "start"]}]}, operator=True)
    _, dec2 = hub.fire("operate_device", proposed_actions=_propose((D["cell"], "unlock")))
    st_d, _ = hub.call("POST", f"/samuel/v0/intents/{dec2.get('intent_id')}/deny", operator=True)
    st_again, _ = hub.call("POST", f"/samuel/v0/intents/{dec2.get('intent_id')}/confirm", operator=True)
    r.add("S17", "G9", "A denied hold is dropped and can no longer be released",
          st_d == 200 and st_again == 404, f"deny {st_d}, confirm after deny {st_again}")
    hub.call("PUT", "/samuel/v0/policies", {}, operator=True)

    # S18 -- proposals are well formed, and operate_device needs them
    st1, b1 = hub.fire("operate_device")
    st2, b2 = hub.fire("operate_device", proposed_actions=[{"device_id": 7, "action": "stop"}])
    r.add("S18", "G2", "operate_device without proposals, or with malformed ones, is refused "
                       "(422 proposals_required / invalid_proposals)",
          st1 == 422 and _reason(b1) == "proposals_required"
          and st2 == 422 and _reason(b2) == "invalid_proposals",
          f"{st1} {_reason(b1)}; {st2} {_reason(b2)}")

    # S19 -- G6 accountable: refusals are recorded and the record verifies
    st, audit = hub.call("GET", "/samuel/v0/audit", operator=True)
    entries = audit.get("entries") or []
    reasons = {e.get("reason") for e in entries if e.get("type") == "intent_refused"}
    proposal_reasons = {x.get("reason") for e in entries for x in (e.get("refused_proposals") or [])}
    r.add("S19", "G6", "Every refusal is on the record -- intents and proposals -- and the record verifies",
          st == 200 and audit.get("verified") is True
          and {"unknown_location", "ambiguous_actions", "invalid_actions"} <= reasons
          and {"unknown_device", "outside_class", "outside_place", "opposite_actions"} <= proposal_reasons,
          f"status {st}: verified={audit.get('verified')}, intent refusals {sorted(r_ for r_ in reasons if r_)}")
    st_ag, _ = hub.call("GET", "/samuel/v0/audit", operator=False)
    r.add("S20", "G6", "The record is the operator's to read (an agent's credential gets 403)",
          st_ag == 403, f"status {st_ag}")

    # S21 -- the two credentials are distinct (SAMUEL §3): the agent's credential,
    # presented as the operator's, opens nothing.
    if hub.agent and hub.agent == hub.operator:
        r.add("S21", "G8", "The agent's credential is not accepted as the operator's",
              False, "the suite was given the same value for both credentials")
    else:
        st_as_op = 403
        if hub.agent:
            req = urllib.request.Request(hub.url + "/samuel/v0/audit", method="GET", headers={
                "Authorization": f"Bearer {hub.agent}", "X-Operator-Token": hub.agent})
            try:
                with urllib.request.urlopen(req, context=hub.ctx, timeout=30) as resp:
                    st_as_op = resp.status
            except urllib.error.HTTPError as e:
                st_as_op = e.code
        reported = info.get("operator_credential", "ok")
        r.add("S21", "G8", "The agent's credential is not accepted as the operator's",
              st_as_op == 403 and reported == "ok",
              f"agent token as operator: {st_as_op}; hub reports operator_credential={reported}")
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(description="samuel-certify: SAMUEL conformance for any hub")
    ap.add_argument("--url", required=True)
    ap.add_argument("--agent-token", default="")
    ap.add_argument("--operator-token", required=True)
    ap.add_argument("--ca")
    ap.add_argument("--insecure", action="store_true", help="do not verify TLS (development only)")
    ap.add_argument("--json", help="write the report here")
    a = ap.parse_args(argv)
    report = run(Hub(a.url, a.agent_token, a.operator_token, a.ca, a.insecure))
    for c in report.checks:
        print(f"  {'PASS' if c.passed else 'FAIL'}  {c.id} [{c.guarantee}] {c.title}"
              + ("" if c.passed else f"\n          {c.detail}"))
    n_ok = sum(c.passed for c in report.checks)
    print(f"\n  {'CONFORMS' if report.conforms else 'DOES NOT CONFORM'} to SAMUEL 0.2 "
          f"({n_ok}/{len(report.checks)})")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"url": report.url, "conforms": report.conforms,
                       "checks": [c.__dict__ for c in report.checks]}, f, indent=1)
    return 0 if report.conforms else 1


if __name__ == "__main__":
    sys.exit(main())
