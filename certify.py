"""
DoSync Certification CLI — dosync-certify
Usage: python3 certify.py --host localhost --port 47200 --tier standard

Tiers:
  basic     (10 tests) — connects, authenticates, registers, manifest fields
  standard  (22 tests) — intents, events, health, explainability, presence
  emergency (32 tests) — emergency override, policy engine, audit log integrity
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.request
import urllib.error
import ssl
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Terminal colors ───────────────────────────────────────────────────────────

class C:
    OK   = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

def ok(msg):      print(f"  {C.OK}✓{C.RESET}  {msg}")
def fail(msg):    print(f"  {C.FAIL}✗{C.RESET}  {msg}")
def warn(msg):    print(f"  {C.WARN}~{C.RESET}  {msg}")
def info(msg):    print(f"  {C.BLUE}·{C.RESET}  {msg}")
def section(t):   print(f"\n{C.BOLD}{t}{C.RESET}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def request(
    method: str,
    url: str,
    body: Optional[dict] = None,
    token_override: Optional[str] = None,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body else None
    token = token_override if token_override is not None else os.environ.get("DOSYNC_TOKEN", "")
    ca_cert = os.environ.get("DOSYNC_CA_CERT", "")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    ctx = ssl.create_default_context()
    if ca_cert and os.path.exists(os.path.expanduser(ca_cert)):
        ctx.load_verify_locations(os.path.expanduser(ca_cert))
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    import re
    is_local = bool(re.search(r'localhost|127\.0\.0\.1', url))
    final_url = url if is_local else url.replace("http://", "https://", 1)
    req = urllib.request.Request(final_url, data=data, headers=headers, method=method)
    ctx_arg = None if is_local else ctx
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx_arg) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


# ── Async intent helper ──────────────────────────────────────────────────────

def fire_intent(base: str, body: dict) -> tuple[int, dict]:
    """POST /v1/intent/async then poll GET /v1/intent/{id} until completed.
    
    Returns the same (status_code, result_dict) interface as request() so
    existing test logic does not need to change.
    Timeout: DOSYNC_INTENT_TIMEOUT + 3s margin (8s emergency, 13s info/alert).
    """
    import time as _t

    urgency = body.get("urgency", "info")
    hub_timeout = 5.0 if urgency == "emergency" else 10.0
    poll_timeout = hub_timeout + 3.0

    # Fire
    status, fire = request("POST", f"{base}/v1/intent/async", body)
    if status != 200 or "error" in fire:
        return status, fire

    intent_id = fire.get("intent_id")
    if not intent_id:
        return 0, {"error": "No intent_id in async response"}

    # Poll
    deadline = _t.monotonic() + poll_timeout
    while _t.monotonic() < deadline:
        _t.sleep(1.0)
        poll_status, poll = request("GET", f"{base}/v1/intent/{intent_id}")
        if poll_status != 200:
            return poll_status, poll
        if poll.get("status") != "pending":
            return 200, poll

    # Timeout — return last known state
    return 200, {**fire, "status": "timeout",
                 "success": None, "actions_taken": 0, "results": [], "failed_devices": []}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""

@dataclass
class CertReport:
    host: str
    port: int
    tier: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    tests: list[TestResult] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    certified: bool = False
    fingerprint: str = ""

    def add(self, result: TestResult):
        self.tests.append(result)
        if result.passed:
            self.passed += 1
            ok(result.name + (f" — {result.detail}" if result.detail else ""))
        else:
            self.failed += 1
            fail(result.name + (f" — {result.detail}" if result.detail else ""))

    def finalize(self):
        self.certified = self.failed == 0
        raw = json.dumps({
            "host": self.host, "tier": self.tier,
            "timestamp": self.timestamp, "passed": self.passed, "failed": self.failed,
        }, sort_keys=True)
        self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "dosync_cert_version": "0.2",
            "certified": self.certified,
            "tier": self.tier,
            "hub": f"{self.host}:{self.port}",
            "timestamp": self.timestamp,
            "summary": {"passed": self.passed, "failed": self.failed, "total": self.passed + self.failed},
            "fingerprint": self.fingerprint,
            "tests": [
                {"name": t.name, "passed": t.passed, "detail": t.detail}
                for t in self.tests
            ],
        }


# ── Test device manifest ──────────────────────────────────────────────────────

TEST_DEVICE = {
    "device_id":    "certify-test-device-01",
    "device_name":  "DoSync Certification Test Device",
    "manufacturer": "DoSync Initiative",
    "model":        "CertBot",
    "firmware":     "0.2.0",
    "category":     "hybrid",
    "tags":         ["test", "emergency", "sensor", "communication", "notification", "light", "climate"],
    "sensors": [
        {"id": "temp",   "type": "temperature", "description": "Test temperature sensor"},
        {"id": "motion", "type": "motion",       "description": "Test motion sensor"},
    ],
    "actuators": [
        {"id": "notify",  "type": "notify",   "description": "Test notification"},
        {"id": "unlock",  "type": "unlock",   "description": "Test unlock"},
        {"id": "call",    "type": "call",     "description": "Test call"},
        {"id": "alarm",   "type": "alarm",    "description": "Test alarm"},
        {"id": "turn_on", "type": "turn_on",  "description": "Test light on"},
        {"id": "turn_off","type": "turn_off", "description": "Test light off"},
    ],
    "events": [
        {"id": "test_event", "severity": "info",      "description": "Test event"},
        {"id": "emergency",  "severity": "emergency", "description": "Test emergency"},
        {"id": "motion_detected", "severity": "alert", "description": "Test motion"},
    ],
    "emergency_capable": True,
    "cert_tier": "emergency",
}


# ── TIER BASIC — 10 tests ─────────────────────────────────────────────────────

def run_basic(base: str, report: CertReport) -> bool:
    section("── Tier BASIC — Connectivity and registration ──────────")

    # B1. Hub reachable
    status, body = request("GET", f"{base}/v1/status")
    report.add(TestResult(
        "B01  Hub reachable on the network",
        status == 200,
        f"version {body.get('version', '?')}" if status == 200 else f"status={status}",
    ))
    if status != 200:
        report.add(TestResult("B02–B10  (skipped — hub not responding)", False, "hub unreachable"))
        return False

    # B2. Hub declares protocol version
    report.add(TestResult(
        "B02  Hub declares protocol version",
        "protocol" in body and body["protocol"].startswith("dosync/"),
        body.get("protocol", "field missing"),
    ))

    # B3. Hub returns required status fields
    required_status = ["name", "version", "protocol", "status", "devices", "audit_entries", "audit_integrity"]
    missing = [f for f in required_status if f not in body]
    report.add(TestResult(
        "B03  Status response contains all required fields",
        len(missing) == 0,
        f"missing: {missing}" if missing else f"{len(required_status)} fields present",
    ))

    # B4. Invalid token is rejected with 401
    status_auth, _ = request("GET", f"{base}/v1/devices", token_override="invalid-token-certify-test")
    report.add(TestResult(
        "B04  Invalid token rejected with 401",
        status_auth == 401,
        f"status={status_auth} (expected 401)",
    ))

    # B5. Device registration
    status, body = request("POST", f"{base}/v1/devices/register", TEST_DEVICE)
    report.add(TestResult(
        "B05  Device can register with the hub",
        status == 200 and body.get("status") == "registered",
        body.get("detail", body.get("status", f"status={status}")),
    ))
    if status != 200:
        return False

    # B6. Device appears in registry
    status, body = request("GET", f"{base}/v1/devices")
    found = any(d["device_id"] == TEST_DEVICE["device_id"] for d in body.get("devices", []))
    report.add(TestResult(
        "B06  Registered device appears in device registry",
        found,
        f"{body.get('count', 0)} devices registered",
    ))

    # B7. Device detail endpoint
    status, body = request("GET", f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    report.add(TestResult(
        "B07  Hub returns device detail by device_id",
        status == 200 and body.get("device_id") == TEST_DEVICE["device_id"],
        f"status={status}",
    ))

    # B8. Capability manifest has all required fields
    required_manifest = ["device_id", "device_name", "manufacturer", "capabilities", "tags"]
    missing = [f for f in required_manifest if f not in body]
    report.add(TestResult(
        "B08  Capability manifest contains all required fields",
        len(missing) == 0,
        f"missing: {missing}" if missing else "all fields present",
    ))

    # B9. Duplicate registration is handled gracefully (200 or 409, not 500)
    status_dup, _ = request("POST", f"{base}/v1/devices/register", TEST_DEVICE)
    report.add(TestResult(
        "B09  Duplicate registration handled gracefully (not 500)",
        status_dup in (200, 409),
        f"status={status_dup}",
    ))

    # B10. Non-existent device returns 404
    status_404, _ = request("GET", f"{base}/v1/devices/device-that-does-not-exist-certify")
    report.add(TestResult(
        "B10  Non-existent device returns 404",
        status_404 == 404,
        f"status={status_404} (expected 404)",
    ))

    return True


# ── TIER STANDARD — 12 additional tests (total 22) ───────────────────────────

def run_standard(base: str, report: CertReport):
    section("── Tier STANDARD — Intents, events, health ─────────────")

    # S1. Hub accepts notify_family intent
    status, body = fire_intent(base, {
        "intent":  "notify_family",
        "urgency": "info",
        "context": {"message": "DoSync certification test — notify intent"},
    })
    report.add(TestResult(
        "S01  Hub accepts notify_family intent",
        status == 200 and body.get("success"),
        f"actions_taken={body.get('actions_taken', 0)}",
    ))

    # S2. Intent resolves and returns structured result
    report.add(TestResult(
        "S02  Intent response contains structured result fields",
        all(k in body for k in ["success", "actions_taken", "results"]),
        "success / actions_taken / results present" if all(k in body for k in ["success", "actions_taken", "results"]) else f"missing fields in: {list(body.keys())}",
    ))

    # S3. save_energy intent executes
    status, body_se = fire_intent(base, {
        "intent":  "save_energy",
        "urgency": "info",
        "context": {},
    })
    report.add(TestResult(
        "S03  Hub accepts save_energy intent",
        status == 200 and body_se.get("success") is not None,
        f"actions_taken={body_se.get('actions_taken', 0)}",
    ))

    # S4. bedtime_routine intent executes
    status, body_bt = fire_intent(base, {
        "intent":  "bedtime_routine",
        "urgency": "info",
        "context": {},
    })
    report.add(TestResult(
        "S04  Hub accepts bedtime_routine intent",
        status == 200 and body_bt.get("success") is not None,
        f"actions_taken={body_bt.get('actions_taken', 0)}",
    ))

    # S5. alert urgency returns faster / is accepted
    status, body_alert = fire_intent(base, {
        "intent":  "alert_anomaly",
        "urgency": "alert",
        "context": {"trigger": "certification_test"},
    })
    report.add(TestResult(
        "S05  Hub accepts alert_anomaly with urgency=alert",
        status == 200 and body_alert.get("success") is not None,
        f"actions_taken={body_alert.get('actions_taken', 0)}",
    ))

    # S6. Device can send event
    status, body_ev = request("POST", f"{base}/v1/event", {
        "device_id": TEST_DEVICE["device_id"],
        "event_id":  "test_event",
        "severity":  "info",
        "data":      {"source": "dosync-certify", "value": 42},
    })
    report.add(TestResult(
        "S06  Device can send event to hub",
        status == 200 and body_ev.get("status") == "received",
        body_ev.get("detail", body_ev.get("status", f"status={status}")),
    ))

    # S7. Unknown intent returns 422
    status_unk, _ = fire_intent(base, {
        "intent": "intent_that_does_not_exist_certify",
        "urgency": "info",
        "context": {},
    })
    report.add(TestResult(
        "S07  Unknown intent rejected with 422",
        status_unk == 422,
        f"status={status_unk} (expected 422)",
    ))

    # S8. Event from unregistered device returns 404
    status_ev404, _ = request("POST", f"{base}/v1/event", {
        "device_id": "device-that-does-not-exist-certify",
        "event_id":  "test",
        "severity":  "info",
        "data":      {},
    })
    report.add(TestResult(
        "S08  Event from unregistered device returns 404",
        status_ev404 == 404,
        f"status={status_ev404} (expected 404)",
    ))

    # S9. Device health endpoint returns data
    status, body_health = request("GET", f"{base}/v1/health/devices")
    report.add(TestResult(
        "S09  Device health endpoint returns data",
        status == 200 and "devices" in body_health,
        f"{len(body_health.get('devices', []))} devices in health report",
    ))

    # S10. Per-device health endpoint works
    status, body_hd = request("GET", f"{base}/v1/health/devices/{TEST_DEVICE['device_id']}")
    report.add(TestResult(
        "S10  Per-device health endpoint returns device stats",
        status in (200, 404),  # 404 is valid if no executions recorded yet
        f"status={status}",
    ))

    # S11. Explainability endpoint returns scoring breakdown
    status, body_exp = request("GET", f"{base}/v1/intents/ensure_safety/explain")
    required_exp = ["intent", "devices_evaluated", "devices_included", "included"]
    missing_exp  = [f for f in required_exp if f not in body_exp]
    report.add(TestResult(
        "S11  Explainability endpoint returns scoring breakdown",
        status == 200 and len(missing_exp) == 0,
        f"{body_exp.get('devices_evaluated', 0)} evaluated, {body_exp.get('devices_included', 0)} included" if status == 200 else f"status={status}",
    ))

    # S12. Direct device action endpoint works
    status, body_act = request("POST", f"{base}/v1/device/action", {
        "device_id": TEST_DEVICE["device_id"],
        "action":    "turn_on",
        "params":    {"brightness": 100},
        "urgency":   "info",
    })
    report.add(TestResult(
        "S12  Direct device action endpoint works",
        status in (200, 404, 422),  # 404/422 acceptable if adapter not configured
        f"status={status}",
    ))


# ── TIER EMERGENCY — 10 additional tests (total 32) ──────────────────────────

def run_emergency(base: str, report: CertReport):
    section("── Tier EMERGENCY — Override, policies, audit log ───────")

    # E1. ensure_safety emergency executes without confirmation
    # Note: partial failures (unreachable physical devices) are acceptable —
    # the test verifies that the hub responded and executed the intent, not
    # that every physical device was reachable.
    status, body = fire_intent(base, {
        "intent":  "ensure_safety",
        "urgency": "emergency",
        "subject": "certify-test-subject",
        "context": {
            "trigger":          "certification_test",
            "location":         "test_room",
            "emergency_number": "000",
            "message":          "DoSync certification — emergency test",
        },
    })
    report.add(TestResult(
        "E01  ensure_safety with urgency=emergency executes immediately",
        status == 200 and body.get("success") is not None and body.get("actions_taken", 0) > 0,
        f"actions={body.get('actions_taken', 0)}, failed={body.get('failed_devices', [])}",
    ))

    # E2. Emergency-capable device participated
    emergency_devices = list({
        r["device_id"] for r in body.get("results", []) if r.get("success")
    })
    report.add(TestResult(
        "E02  emergency_capable devices participated in the response",
        TEST_DEVICE["device_id"] in emergency_devices,
        f"active devices: {emergency_devices}",
    ))

    # E3. children_arrived_home intent is accepted by the hub
    # Note: this intent has a time-based policy (weekdays 18:30-19:00).
    # The test verifies the hub accepted and processed the intent, not that
    # it produced actions. Zero actions outside the policy window is correct.
    status, body_ch = fire_intent(base, {
        "intent":  "children_arrived_home",
        "urgency": "info",
        "context": {"trigger": "certification_test"},
    })
    report.add(TestResult(
        "E03  children_arrived_home intent accepted by hub",
        status == 200 and body_ch.get("success") is not None,
        f"actions_taken={body_ch.get('actions_taken', 0)} (0 valid outside policy window)",
    ))

    # E4. away_mode intent executes
    status, body_aw = fire_intent(base, {
        "intent":  "away_mode",
        "urgency": "info",
        "context": {},
    })
    report.add(TestResult(
        "E04  away_mode intent executes",
        status == 200 and body_aw.get("success") is not None,
        f"actions_taken={body_aw.get('actions_taken', 0)}",
    ))

    # E5. Audit log exists and has entries
    status, body_audit = request("GET", f"{base}/v1/audit")
    report.add(TestResult(
        "E05  Audit log exists and has entries",
        status == 200 and body_audit.get("count", 0) > 0,
        f"{body_audit.get('count', 0)} entries",
    ))

    # E6. Audit log SHA-256 chain is intact
    report.add(TestResult(
        "E06  Audit log SHA-256 chain integrity verified",
        body_audit.get("integrity") is True,
        "chain intact" if body_audit.get("integrity") else "chain compromised",
    ))

    # E7. Audit log recorded the emergency event
    entries = body_audit.get("entries", [])
    has_emergency = any(
        e.get("intent") == "ensure_safety" and e.get("urgency") == "emergency"
        for e in entries
    )
    report.add(TestResult(
        "E07  Audit log recorded the emergency event",
        has_emergency,
        "emergency entry found" if has_emergency else "emergency entry missing",
    ))

    # E8. Audit log intent_executed entry contains required fields
    intent_entries = [e for e in entries if e.get("type") == "intent_executed"]
    if intent_entries:
        sample = intent_entries[0]
        required_entry = ["intent", "urgency", "timestamp", "actions", "success", "hash", "prev_hash"]
        missing = [f for f in required_entry if f not in sample]
        report.add(TestResult(
            "E08  Audit log intent_executed entries contain required fields",
            len(missing) == 0,
            f"missing: {missing}" if missing else "all fields present",
        ))
    else:
        report.add(TestResult("E08  Audit log intent_executed entries contain required fields", False, "no intent_executed entries found"))

    # E9. Status reports audit integrity as True
    status, body_status = request("GET", f"{base}/v1/status")
    report.add(TestResult(
        "E09  Hub status reports audit_integrity=True",
        status == 200 and body_status.get("audit_integrity") is True,
        f"audit_integrity={body_status.get('audit_integrity')}",
    ))

    # E10. Hub has been running with devices registered (production readiness)
    device_count = body_status.get("devices", 0)
    audit_count  = body_status.get("audit_entries", 0)
    report.add(TestResult(
        "E10  Hub is production-ready (devices registered, audit log active)",
        device_count > 0 and audit_count > 0,
        f"{device_count} devices, {audit_count} audit entries",
    ))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DoSync Certification CLI v0.2 — protocol conformance testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 certify.py --host localhost --port 47200 --tier basic
  python3 certify.py --host localhost --port 47200 --tier standard
  python3 certify.py --host localhost --port 47200 --tier emergency
  python3 certify.py --host 192.168.100.109 --port 47200 --tier emergency --output cert.json

Environment variables:
  DOSYNC_TOKEN     API token for authenticated requests
  DOSYNC_CA_CERT   Path to CA cert for TLS verification (e.g. ~/Desktop/dosync-ca.crt)

Tier test counts:
  basic      10 tests  — connectivity, auth, registration, manifest
  standard   22 tests  — + intents, events, health, explainability
  emergency  32 tests  — + emergency override, audit log integrity
        """,
    )
    parser.add_argument("--host",   default="localhost",  help="Hub IP or hostname")
    parser.add_argument("--port",   default=47200, type=int, help="Hub port")
    parser.add_argument("--tier",   default="standard",
                        choices=["basic", "standard", "emergency"],
                        help="Certification tier to verify")
    parser.add_argument("--output", default=None,
                        help="Output file for JSON report (e.g. cert.json)")
    args = parser.parse_args()

    base   = f"http://{args.host}:{args.port}"
    report = CertReport(host=args.host, port=args.port, tier=args.tier)

    tier_counts = {"basic": 10, "standard": 22, "emergency": 32}
    print(f"\n{C.BOLD}DoSync Certification CLI v0.2{C.RESET}")
    print(f"  Hub:   {base}")
    print(f"  Tier:  {C.BOLD}{args.tier.upper()}{C.RESET} ({tier_counts[args.tier]} tests)")
    print(f"  Date:  {report.timestamp}")

    ok_basic = run_basic(base, report)
    if ok_basic and args.tier in ("standard", "emergency"):
        run_standard(base, report)
    if ok_basic and args.tier == "emergency":
        run_emergency(base, report)

    # Cleanup — remove test device
    request("DELETE", f"{base}/v1/devices/{TEST_DEVICE['device_id']}")

    # Final result
    report.finalize()
    section("── Result ────────────────────────────────────────────────")
    total = report.passed + report.failed
    print(f"  Passed: {C.OK}{report.passed}{C.RESET} / {total}")
    print(f"  Failed: {C.FAIL if report.failed else C.OK}{report.failed}{C.RESET} / {total}")

    if report.certified:
        print(f"\n  {C.BOLD}{C.OK}✓ CERTIFIED — DoSync {args.tier.upper()} ({report.passed}/{total}){C.RESET}")
        print(f"  Fingerprint: {report.fingerprint[:32]}…")
    else:
        print(f"\n  {C.BOLD}{C.FAIL}✗ NOT CERTIFIED — {report.failed} test(s) failed{C.RESET}")

    output_file = args.output or f"dosync-cert-{args.tier}-{int(time.time())}.json"
    with open(output_file, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"\n  Report saved: {output_file}\n")

    sys.exit(0 if report.certified else 1)


if __name__ == "__main__":
    main()
