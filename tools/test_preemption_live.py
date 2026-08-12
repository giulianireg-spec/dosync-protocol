#!/usr/bin/env python3
"""DoSync — live, DETERMINISTIC emergency-preemption drill (C1.1).

Rather than depending on a routine intent overlapping the emergency (which
depends on the tags in the database), this drill:

  1. Fires ensure_safety [emergency] and waits for it to complete. The
     emergency claims every device it acts on (claim-first in the arbiter).
  2. Picks one of the devices the emergency actually touched.
  3. Inside the grace window, fires a lower-urgency DIRECT ACTION
     (POST /v1/device/action, which the hub executes as urgency=info)
     against that device.
  4. Verifies the action comes back SUPERSEDED (success=False +
     response.superseded) and that the audit chain recorded
     action_superseded_by_priority.

That is exactly the normative guarantee of spec §3: a lower-urgency write to
a device the emergency owns is discarded. Deterministic: the device is claimed
with certainty because we just completed the emergency over it.

Usage (on the hub host):
  DOSYNC_TOKEN=<token> python3 tools/test_preemption_live.py --ca certs/ca.crt

This is a live harness against a running hub, not a CI test — it actuates
real devices. It follows the same convention as tools/test_failover_live.py.
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.request


def req(method, url, token, body=None, ca=None, insecure=False):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    if data:
        r.add_header("Content-Type", "application/json")
    ctx = None
    if url.startswith("https"):
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def poll(base, token, iid, ca, insecure, timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, body = req("GET", f"{base}/v1/intent/{iid}", token, None, ca, insecure)
        if body.get("status") not in ("pending", None):
            return body
        time.sleep(0.4)
    return {"status": "timeout"}


def main():
    ap = argparse.ArgumentParser(description="Live emergency-preemption drill")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default="47200")
    ap.add_argument("--scheme", default="https", choices=["http", "https"])
    ap.add_argument("--ca", default=os.environ.get("DOSYNC_CA_CERT", "certs/ca.crt"))
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--device", default="",
                    help="force a specific device (device_id)")
    args = ap.parse_args()
    token = os.environ.get("DOSYNC_TOKEN", "")
    if not token:
        print("ERROR: export DOSYNC_TOKEN")
        sys.exit(2)
    base = f"{args.scheme}://{args.host}:{args.port}"
    ca = args.ca if args.scheme == "https" else None
    ins = args.insecure
    print(f"Hub: {base}\n")

    # audit BEFORE
    _, ab = req("GET", f"{base}/v1/audit", token, None, ca, ins)
    n_before = sum(1 for e in ab.get("entries", [])
                   if e.get("type") == "action_superseded_by_priority")

    # 1. emergency → claims the devices it touches
    print("→ ensure_safety [emergency] ...")
    s, b = req("POST", f"{base}/v1/intent/async", token,
               {"intent": "ensure_safety", "urgency": "emergency",
                "context": {"trigger": "preemption_live_test"}}, ca, ins)
    iid = b.get("intent_id")
    if not iid:
        print(f"ERROR firing the emergency: {s}/{b}")
        sys.exit(1)
    res = poll(base, token, iid, ca, ins)
    results = res.get("results", []) or []
    print(f"  emergency → status={res.get('status')}  actions={res.get('actions_taken')}")

    # 2. pick one of the devices the emergency touched (∴ claimed)
    touched = [r.get("device_id", "") for r in results]
    target = args.device or (touched[0] if touched else "")
    if not target:
        print("  Could not identify a device touched by the emergency (empty results).")
        print("  Pass --device <device-id> to force one.")
        sys.exit(1)
    print(f"  claimed device chosen: {target}\n")

    # 3. LOWER-urgency direct action on that device, inside the grace window
    print(f"→ direct action [info] turn_on brightness=10 on {target} "
          "(must come back superseded) ...")
    sa, ba = req("POST", f"{base}/v1/device/action", token,
                 {"device_id": target, "action": "turn_on",
                  "params": {"brightness": 10}}, ca, ins)
    superseded = bool((ba.get("response") or {}).get("superseded"))
    success = ba.get("success")
    print(f"  response: http={sa}  success={success}  superseded={superseded}")
    if ba.get("error"):
        print(f"  error: {ba.get('error')}")

    # 4. audit AFTER
    time.sleep(0.5)
    _, aa = req("GET", f"{base}/v1/audit", token, None, ca, ins)
    sup = [e for e in aa.get("entries", [])
           if e.get("type") == "action_superseded_by_priority"]
    n_new = len(sup) - n_before

    print("\n── Result ─────────────────────────────────")
    print(f"  direct action superseded: {superseded}  (success={success})")
    print(f"  new supersede entries in audit: {n_new}")
    for e in sup[-4:]:
        print(f"    · {e.get('device_id')} ({e.get('action')}) "
              f"claimed_by={e.get('claimed_by_urgency')}")

    ok = (superseded is True) and (success is False) and (n_new >= 1)
    print("\n──────────────────────────────────────────")
    if ok:
        print("  ✓ PREEMPTION CONFIRMED — the emergency is device-final;")
        print("    the lower-urgency action was discarded and audited.")
        sys.exit(0)
    else:
        print("  ✗ Preemption was not observed. Possible causes:")
        print("    - more than <grace> seconds passed between emergency and "
              "action (retry)")
        print("    - the chosen device was not claimed (try --device with a "
              "device the emergency touched)")
        sys.exit(1)


if __name__ == "__main__":
    main()
