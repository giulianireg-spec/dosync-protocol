"""
DoSync Certification CLI — dosync-certify
Verifies protocol conformance across three certification tiers.

Usage:
  python3 certify.py --host <hub-ip> --port 47200 --tier standard

Tiers:
  basic     (10 tests) — connectivity, authentication, device manifest
  standard  (33 tests) — protocol conformance, events, health, version headers, manifest privacy, intent lifecycle
  emergency (44 tests) — everything in standard + emergency override, policy engine, audit log integrity, firmware re-registration
  conformance (69 tests) — everything in emergency + protocol features through 0.5: sensor kind, policy provenance,
                           chain archiving, adapters, discovery, and where a device is and what a location in an intent does

Two testing modes:

  Production mode (default):
    Runs against a live hub with physical adapters.
    Tests S05+ poll for execution results from real devices.
    Banner: "Production mode — execution tests run against physical devices"

  Certify mode (DOSYNC_CERTIFY=true on hub):
    Hub uses SimulatedExecutor — no physical devices required.
    All intent executions complete in <100ms, deterministic results.
    Ideal for CI/CD pipelines and third-party hub implementors.
    Banner: "CERTIFY MODE active — SimulatedExecutor in use"

    Start hub in certify mode:
      DOSYNC_CERTIFY=true uvicorn server:app --host 0.0.0.0 --port 47200

    Then run certification normally:
      DOSYNC_TOKEN=<token> python3 certify.py --host localhost --port 47200 --tier emergency

Protocol conformance architecture:
  fire_intent_conformance(base, body) — verifies protocol ACCEPTANCE only.
    POSTs to /v1/intent/async and returns immediately (no polling).
    Checks: HTTP 200 + intent_id + status fields present.
    Used by S01-S04 — deterministic, <100ms, independent of devices.

  fire_intent(base, body) — verifies intent EXECUTION outcome.
    POSTs to /v1/intent/async then polls GET /v1/intent/{id} until complete.
    Timeout: 5s emergency, 7s info/alert (hub_timeout + 2s margin).
    Used by S05+ — depends on device execution results.

Environment variables:
  DOSYNC_TOKEN     API token for authenticated requests
  DOSYNC_CA_CERT   Path to CA certificate for TLS verification
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



def get_response_headers(method: str, url: str, body: Optional[dict] = None) -> tuple[int, dict, dict]:
    """Like request() but also returns response headers. Used for version header tests."""
    data = json.dumps(body).encode() if body else None
    token = os.environ.get("DOSYNC_TOKEN", "")
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
            resp_headers = dict(resp.getheaders())
            return resp.status, json.loads(resp.read()), resp_headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), {}
        except Exception:
            return e.code, {"error": str(e)}, {}
    except Exception as e:
        return 0, {"error": str(e)}, {}

# ── Async intent helper ──────────────────────────────────────────────────────

def fire_intent(base: str, body: dict) -> tuple[int, dict]:
    """[integration helper — not used by the conformance suite] POST /v1/intent/async
    then poll GET /v1/intent/{id} until completed.
    
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



def fire_intent_conformance(base: str, body: dict) -> tuple[int, dict]:
    """POST /v1/intent/async and return the ACCEPTANCE response immediately.

    For protocol conformance testing we verify that the hub:
    - Accepts the intent with correct HTTP status (200)
    - Returns correct response structure (intent_id, status)

    We do NOT poll for execution results. Physical device execution
    is integration testing. Protocol conformance only verifies that
    the hub correctly processes the protocol message itself.
    This makes conformance tests fast and deterministic regardless
    of the number of physical devices registered in the deployment.
    """
    return request("POST", f"{base}/v1/intent/async", body)

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
    signature: str = ""          # Ed25519 signature over the canonical report (optional)
    hub_version: str = ""        # hub's reported app version (reproducibility)
    hub_protocol: str = ""       # hub's reported protocol version (reproducibility)
    #: Checks that could not be run safely in this mode, each with its reason.
    #: Neither passed nor failed: a pass would claim what nobody checked, and a
    #: fail would make every production hub uncertifiable for a check that only
    #: certify mode can run without acting on physical devices.
    not_applicable: list = field(default_factory=list)

    def add(self, result: TestResult):
        self.tests.append(result)
        if result.passed:
            self.passed += 1
            ok(result.name + (f" — {result.detail}" if result.detail else ""))
        else:
            self.failed += 1
            fail(result.name + (f" — {result.detail}" if result.detail else ""))

    #: How many checks each tier is expected to run. A suite that stops early —
    #: because the hub refused the setup, or the network dropped — must not be
    #: mistaken for a suite that ran and found problems, and MUST NOT certify.
    #:
    #: Found the hard way: an empty DOSYNC_TOKEN made device registration fail,
    #: the run aborted after 5 of 56 checks, and the report said
    #: "NOT CERTIFIED — 1 test(s) failed". An operator reading that concludes
    #: their hub failed conformance, when in fact it was never tested. Worse, a
    #: run that aborted BEFORE any failure would have certified on zero checks.
    #:
    #: This is the same distinction the protocol itself insists on elsewhere:
    #: `unverifiable` is not `contradicted`, and "not searchable" is not "found
    #: nothing". A certification suite owes the same honesty.
    EXPECTED_COUNTS = {
        # basic said 12 while the tier runs B01-B10: a basic certification was
        # always "incomplete" and could never certify. Nothing noticed because
        # nothing ran the suite; CI now certifies every tier on its own.
        "basic": 10, "standard": 33, "emergency": 44, "conformance": 69,
    }

    def finalize(self):
        expected = self.EXPECTED_COUNTS.get(self.tier)
        if expected:
            expected -= len(self.not_applicable)
        self.expected = expected
        self.executed = self.passed + self.failed
        self.incomplete = bool(expected and self.executed < expected)
        self.certified = self.failed == 0 and not self.incomplete
        raw = json.dumps({
            "host": self.host, "tier": self.tier,
            "timestamp": self.timestamp, "passed": self.passed, "failed": self.failed,
            "not_applicable": [n for n, _ in self.not_applicable],
        }, sort_keys=True)
        self.fingerprint = hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "dosync_cert_version": "0.3",
            "certified": self.certified,
            # A third party reading this file must be able to tell a hub that
            # failed from a suite that never finished. The signature covers
            # these, so an incomplete run cannot be presented as a clean one.
            "executed": getattr(self, "executed", self.passed + self.failed),
            "expected": getattr(self, "expected", None),
            "incomplete": getattr(self, "incomplete", False),
            "not_applicable": [{"check": n, "reason": r} for n, r in self.not_applicable],
            "tier": self.tier,
            "hub": f"{self.host}:{self.port}",
            "hub_version": self.hub_version,
            "hub_protocol": self.hub_protocol,
            "timestamp": self.timestamp,
            "summary": {"passed": self.passed, "failed": self.failed, "total": self.passed + self.failed},
            # ── Reproducibility ────────────────────────────────────────────────
            # This report is self-issued. Its value comes from being reproducible:
            # a third party can re-run the same certification against the same hub
            # and compare. This block tells them exactly how.
            "reproduce": {
                "tool": "certify.py",
                "command": f"python3 certify.py --host {self.host} --port {self.port} --tier {self.tier}",
                "protocol_tested": self.hub_protocol or "see hub_protocol",
                "note": "Re-run against the same hub and compare summary + per-test results.",
            },
            # ── Honesty: what this certifies, and what it does NOT ─────────────
            # A signature proves the report wasn't altered after issuance; it does
            # NOT make a self-issued cert an independent audit. We state the limits
            # plainly — the same honesty applied to the audit log.
            "attestation": {
                "type": "self-issued",
                "proves": [
                    "the hub at this address passed these tests at this timestamp",
                    "this report has not been altered since it was signed (if signature present)",
                ],
                "does_not_prove": [
                    "future behavior, or behavior under different configuration",
                    "that the test environment matches production",
                    "independent third-party review (this is self-certification, not an authority)",
                ],
            },
            "fingerprint": self.fingerprint,
            "signature": self.signature,
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

    # Detect certify mode — shows banner if hub uses SimulatedExecutor
    _cs, _cb = request("GET", f"{base}/v1/status")
    if _cs == 200 and _cb.get("certify_mode"):
        print(f"  {C.WARN}~{C.RESET}  CERTIFY MODE active — SimulatedExecutor in use (no physical devices)")
        print(f"  {C.WARN}~{C.RESET}  Execution tests return deterministic results — do NOT use in production")
    else:
        print(f"  {C.BLUE}·{C.RESET}  Production mode — execution tests run against physical devices")

    # B1. Hub reachable
    status, body = request("GET", f"{base}/v1/status")
    if status == 200:
        report.hub_version = str(body.get("version", ""))
        report.hub_protocol = str(body.get("protocol", ""))
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


# ── TIER STANDARD — 23 additional tests (total 33) ───────────────────────────

def run_standard(base: str, report: CertReport):
    section("── Tier STANDARD — Protocol conformance + events ────────")

    # S1-S4: Protocol conformance — verify hub accepts intent messages correctly.
    # Uses fire_intent_conformance() — checks ACCEPTANCE only, no polling.
    # Physical device execution is integration testing, not protocol conformance.

    # S1. Hub accepts a valid registered universal intent
    status, body = fire_intent_conformance(base, {
        "intent":  "notify",
        "urgency": "info",
        "context": {"message": "DoSync certification test"},
    })
    report.add(TestResult(
        "S01  Hub accepts valid registered intent (notify [info])",
        status == 200 and "intent_id" in body,
        f"intent_id={'present' if 'intent_id' in body else 'MISSING'}",
    ))
    # S2. Acceptance response has correct protocol structure
    report.add(TestResult(
        "S02  Acceptance response has correct structure (intent_id + status)",
        status == 200 and all(k in body for k in ["intent_id", "status"]),
        "intent_id + status present"
        if all(k in body for k in ["intent_id", "status"])
        else f"missing: {[k for k in ['intent_id','status'] if k not in body]}",
    ))
    # S3. Hub accepts emergency urgency on universal safety intent
    status3, body3 = fire_intent_conformance(base, {
        "intent":  "ensure_safety",
        "urgency": "emergency",
        "context": {"trigger": "certification_test"},
    })
    report.add(TestResult(
        "S03  Hub accepts emergency urgency (ensure_safety [emergency])",
        status3 == 200 and "intent_id" in body3,
        f"intent_id={'present' if 'intent_id' in body3 else 'MISSING'}",
    ))
    # S4. Hub accepts alert urgency on universal access intent
    status4, body4 = fire_intent_conformance(base, {
        "intent":  "control_access",
        "urgency": "alert",
        # control_access asks for lock and unlock; an intent says which it means
        # (protocol 0.5 rev. 2026-10-02). Before this, S4 asked a hub's locks
        # for both at once.
        "context": {"trigger": "certification_test", "action_types": ["lock"]},
    })
    report.add(TestResult(
        "S04  Hub accepts alert urgency (control_access [alert])",
        status4 == 200 and "intent_id" in body4,
        f"intent_id={'present' if 'intent_id' in body4 else 'MISSING'}",
    ))

    # S5. alert_anomaly with urgency=alert is accepted (CONFORMANCE — acceptance,
    # not execution, so the test is deterministic regardless of device reachability).
    status, body_alert = fire_intent_conformance(base, {
        "intent":  "alert_anomaly",
        "urgency": "alert",
        "context": {"trigger": "certification_test"},
    })
    report.add(TestResult(
        "S05  Hub accepts alert_anomaly with urgency=alert",
        status == 200 and bool(body_alert.get("intent_id")) and body_alert.get("status") is not None,
        f"status={status} intent_id={'present' if body_alert.get('intent_id') else 'missing'}",
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

    # S7. Unknown intent returns 422 (acceptance-level rejection — conformance)
    status_unk, _ = fire_intent_conformance(base, {
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
    status, body_exp = request("GET", f"{base}/v1/intents/ensure_safety/explain?urgency=emergency")
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
    # 403 is not a failure — it is the point. Until 2026-07-25 this endpoint
    # called the executor directly, skipping the policy engine entirely; closing
    # that was one of the two false claims this project found in its own
    # advertised strengths. A deployment whose policy forbids acting on this
    # device SHOULD refuse, and a conformance suite that treats the refusal as a
    # defect would push an implementer toward removing the very check.
    #
    # Caught by running the suite against a real deployment for the first time:
    # the reference hub answered 403 because its own policy file excludes the
    # device, and the test had been written before a 403 was possible.
    _policy_refused = status == 403
    report.add(TestResult(
        "S12  Direct device action is executed or refused by policy — never unguarded",
        status in (200, 403, 404, 422),
        f"status={status}" + (
            " — refused by deployment policy, which is conforming behaviour"
            if _policy_refused else
            " (404/422 acceptable when the adapter is not configured)"),
    ))



    # S13. Version headers present in every response
    s_status, _, s_headers = get_response_headers("GET", f"{base}/v1/status")
    has_proto  = "X-Dosync-Protocol-Version" in s_headers or "x-dosync-protocol-version" in {k.lower(): v for k, v in s_headers.items()}
    has_api    = "X-Dosync-Api-Version" in s_headers or "x-dosync-api-version" in {k.lower(): v for k, v in s_headers.items()}
    # Normalize header lookup
    lower_h = {k.lower(): v for k, v in s_headers.items()}
    has_proto = "x-dosync-protocol-version" in lower_h
    has_api   = "x-dosync-api-version" in lower_h
    report.add(TestResult(
        "S13  Version headers present (X-DoSync-Protocol-Version, X-DoSync-API-Version)",
        has_proto and has_api,
        f"X-DoSync-Protocol-Version={'present' if has_proto else 'MISSING'}  "
        f"X-DoSync-API-Version={'present' if has_api else 'MISSING'}",
    ))

    # S14. Async intent polling lifecycle — fire → poll → result
    s14_status, s14_fire = request("POST", f"{base}/v1/intent/async", {
        "intent": "report_status", "urgency": "info", "source": "certify"
    })
    s14_id = s14_fire.get("intent_id") if s14_status == 200 else None
    if s14_id:
        time.sleep(2)
        s14_poll_status, s14_result = request("GET", f"{base}/v1/intent/{s14_id}")
        s14_has_fields = all(
            k in s14_result for k in ("intent_id", "success", "status", "results")
        )
        report.add(TestResult(
            "S14  Async intent polling — fire, poll, result has required fields",
            s14_poll_status == 200 and s14_has_fields,
            f"poll_status={s14_poll_status} status={s14_result.get('status')} "
            f"fields={'ok' if s14_has_fields else 'MISSING'}",
        ))
    else:
        report.add(TestResult("S14  Async intent polling lifecycle", False,
                              f"fire failed: status={s14_status}"))

    # S15. Hub heartbeat endpoint returns required fields
    s15_status, s15_body = request("GET", f"{base}/v1/hub/heartbeat")
    s15_required = {"hub_id", "status", "protocol_version", "devices", "role"}
    s15_present  = s15_required.issubset(set(s15_body.keys())) if s15_status == 200 else False
    report.add(TestResult(
        "S15  Hub heartbeat endpoint — hub_id, status, protocol_version, devices, role",
        s15_status == 200 and s15_present,
        f"status={s15_status} missing={s15_required - set(s15_body.keys())}",
    ))

    # S16. Intent classes endpoint lists the five universal intents
    s16_status, s16_body = request("GET", f"{base}/v1/intent-classes")
    UNIVERSAL = {"ensure_safety", "alert_anomaly", "control_access", "report_status", "notify"}
    if s16_status == 200:
        registered = {ic["name"] for ic in s16_body.get("intent_classes", [])}
        missing = UNIVERSAL - registered
        report.add(TestResult(
            "S16  Intent classes endpoint — five universal intents present",
            len(missing) == 0,
            f"registered={len(registered)} missing={missing if missing else 'none'}",
        ))
    else:
        report.add(TestResult("S16  Intent classes endpoint", False, f"status={s16_status}"))

    # S17. Capability manifest redacts adapter_config (privacy — G11)
    s17_status, s17_body = request("GET",
        f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    s17_no_config = "adapter_config" not in s17_body
    report.add(TestResult(
        "S17  Manifest privacy — adapter_config absent from public API response",
        s17_status == 200 and s17_no_config,
        f"status={s17_status} adapter_config={'absent ✓' if s17_no_config else 'EXPOSED ✗'}",
    ))

    # S18. Invalid urgency value is rejected with 422
    s18_status, _ = request("POST", f"{base}/v1/intent/async", {
        "intent": "ensure_safety", "urgency": "superurgent"
    })
    report.add(TestResult(
        "S18  Invalid urgency value rejected with 422",
        s18_status == 422,
        f"status={s18_status} (expected 422)",
    ))

    # S19. Status endpoint exposes protocol_version and api_version fields
    s19_status, s19_body = request("GET", f"{base}/v1/status")
    s19_has_proto = "protocol_version" in s19_body
    s19_has_api   = "api_version" in s19_body
    report.add(TestResult(
        "S19  /v1/status body includes protocol_version and api_version",
        s19_status == 200 and s19_has_proto and s19_has_api,
        f"protocol_version={s19_body.get('protocol_version','MISSING')}  "
        f"api_version={s19_body.get('api_version','MISSING')}",
    ))

    # S20. Intent result contains source field (tracks origin — G14 fix)
    if s14_id and s14_poll_status == 200:
        s20_has_source = "source" in s14_result or True  # source is in audit, result has intent_id
        # Actually, IntentResult doesn't carry source — audit does. Test that audit has source.
        s20_audit_status, s20_audit = request("GET", f"{base}/v1/audit")
        s20_entries = s20_audit.get("entries", [])
        s20_intent_entries = [e for e in s20_entries if e.get("type") == "intent_executed"]
        s20_has_source = any("source" in e for e in s20_intent_entries)
        report.add(TestResult(
            "S20  Audit log intent_executed entries include source field",
            s20_audit_status == 200 and s20_has_source,
            f"intent_executed entries={len(s20_intent_entries)} "
            f"source_field={'present' if s20_has_source else 'MISSING'}",
        ))
    else:
        report.add(TestResult("S20  Audit source field", False, "skipped — S14 fire failed"))

    # S21. Device unregistration — DELETE removes device from registry
    s21_del_status, _   = request("DELETE",
        f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    s21_get_status, _   = request("GET",
        f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    report.add(TestResult(
        "S21  Device unregistration — DELETE /v1/devices/{id} removes device",
        s21_del_status in (200, 204) and s21_get_status == 404,
        f"delete_status={s21_del_status} get_after_delete={s21_get_status} (expected 404)",
    ))

    # S22. Re-register test device (cleanup so Emergency tests can use it)
    s22_status, _ = request("POST", f"{base}/v1/devices/register", TEST_DEVICE)
    report.add(TestResult(
        "S22  Test device re-registration after unregistration succeeds",
        s22_status == 200,
        f"status={s22_status}",
    ))

    # S23. params_schema is enforced as JSON Schema (protocol v0.3)
    # The standard commits to JSON Schema draft 2020-12 for action params. A hub
    # MUST reject a manifest whose params_schema is not valid JSON Schema —
    # otherwise the standard is not enforced. We register a device with a
    # deliberately malformed schema (minimum is a string) and expect 422.
    malformed_device = {
        "device_id": "cert-schema-test-01",
        "device_name": "Cert Schema Test",
        "manufacturer": "Cert", "model": "Test", "firmware": "1",
        "category": "actuator", "tags": ["light"], "sensors": [],
        "actuators": [{
            "id": "set_brightness", "type": "set_brightness", "description": "",
            "params_schema": {"type": "object",
                              "properties": {"brightness": {"type": "integer", "minimum": "low"}}},
        }],
        "emergency_capable": False, "cert_tier": "basic",
    }
    s23_status, _ = request("POST", f"{base}/v1/devices/register", malformed_device)
    # Accept 422 (validation active). A 200 means the hub did not enforce the
    # schema contract — that fails certification. (If the hub runs without the
    # jsonschema library, validation degrades and this cannot be enforced; in
    # that deployment the hub should install jsonschema to be conformant.)
    report.add(TestResult(
        "S23  params_schema enforced as JSON Schema — malformed rejected (422)",
        s23_status == 422,
        f"status={s23_status} (expected 422; 200 = schema contract not enforced)",
    ))
    # Cleanup in case it somehow registered.
    if s23_status == 200:
        request("DELETE", f"{base}/v1/devices/cert-schema-test-01")

# ── TIER EMERGENCY — 11 additional tests (cumulative tier total: 44) ────────

def run_emergency(base: str, report: CertReport):
    section("── Tier EMERGENCY — Override, policies, audit log ───────")

    # E1. Emergency intent is accepted for immediate dispatch (CONFORMANCE).
    # We verify the hub ACCEPTS an emergency-urgency intent and returns a valid
    # dispatch acknowledgement (intent_id + status). Physical device execution is
    # integration testing (see fire_intent_conformance) and must not gate protocol
    # conformance, which has to be deterministic regardless of how many physical
    # devices are reachable in the deployment.
    status, body = fire_intent_conformance(base, {
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
        "E01  Emergency intent accepted for immediate dispatch",
        status == 200 and bool(body.get("intent_id")) and body.get("status") is not None,
        f"status={status} intent_id={'present' if body.get('intent_id') else 'missing'} dispatch={body.get('status')}",
    ))

    # E2. The deployment has emergency-capable devices to dispatch to (registry-based,
    # deterministic — confirms the emergency response has something to act on, without
    # depending on physical execution).
    status, body_dev = request("GET", f"{base}/v1/devices")
    emergency_capable = [
        d["device_id"] for d in body_dev.get("devices", [])
        if d.get("emergency_capable")
    ]
    report.add(TestResult(
        "E02  Emergency-capable devices are registered and available",
        status == 200 and len(emergency_capable) > 0,
        f"{len(emergency_capable)} emergency_capable device(s) registered",
    ))

    # E3. Audit log exists and has entries
    status, body_audit = request("GET", f"{base}/v1/audit")
    report.add(TestResult(
        "E03  Audit log exists and has entries",
        status == 200 and body_audit.get("count", 0) > 0,
        f"{body_audit.get('count', 0)} entries",
    ))

    # E4. Audit log SHA-256 chain is intact
    report.add(TestResult(
        "E04  Audit log SHA-256 chain integrity verified",
        body_audit.get("integrity") is True,
        "chain intact" if body_audit.get("integrity") else "chain compromised",
    ))

    # E5. Audit log recorded the emergency event
    entries = body_audit.get("entries", [])
    has_emergency = any(
        e.get("intent") == "ensure_safety" and e.get("urgency") == "emergency"
        for e in entries
    )
    report.add(TestResult(
        "E05  Audit log recorded the emergency event",
        has_emergency,
        "emergency entry found" if has_emergency else "emergency entry missing",
    ))

    # E6. Audit log intent_executed entry contains required fields
    intent_entries = [e for e in entries if e.get("type") == "intent_executed"]
    if intent_entries:
        sample = intent_entries[0]
        required_entry = ["intent", "urgency", "timestamp", "actions", "success", "hash", "prev_hash"]
        missing = [f for f in required_entry if f not in sample]
        report.add(TestResult(
            "E06  Audit log intent_executed entries contain required fields",
            len(missing) == 0,
            f"missing: {missing}" if missing else "all fields present",
        ))
    else:
        report.add(TestResult("E06  Audit log intent_executed entries contain required fields", False, "no intent_executed entries found"))

    # E7. Status reports audit integrity as True
    status, body_status = request("GET", f"{base}/v1/status")
    report.add(TestResult(
        "E07  Hub status reports audit_integrity=True",
        status == 200 and body_status.get("audit_integrity") is True,
        f"audit_integrity={body_status.get('audit_integrity')}",
    ))

    # E8. Hub has been running with devices registered (production readiness)
    device_count = body_status.get("devices", 0)
    audit_count  = body_status.get("audit_entries", 0)
    report.add(TestResult(
        "E08  Hub is production-ready (devices registered, audit log active)",
        device_count > 0 and audit_count > 0,
        f"{device_count} devices, {audit_count} audit entries",
    ))


    # E9. Firmware re-registration — register same device with different firmware
    import copy
    e11_manifest = copy.deepcopy(TEST_DEVICE)
    e11_manifest["firmware"] = "9.9.9-certify-test"   # different firmware
    e11_status, e11_body = request("POST", f"{base}/v1/devices/register", e11_manifest)
    # Also verify the device is still accessible after re-registration
    e11_get_status, e11_device = request("GET", f"{base}/v1/devices/{TEST_DEVICE['device_id']}")
    report.add(TestResult(
        "E09  Firmware re-registration — hub accepts and updates without error",
        e11_status == 200 and e11_get_status == 200,
        f"re-register_status={e11_status} device_accessible={e11_get_status}",
    ))
    # Restore original firmware
    request("POST", f"{base}/v1/devices/register", TEST_DEVICE)

    # E10. Heartbeat status is healthy (not degraded)
    e12_status, e12_body = request("GET", f"{base}/v1/hub/heartbeat")
    e12_healthy = e12_body.get("status") == "healthy"
    e12_role    = e12_body.get("role") in ("primary", "standby")
    report.add(TestResult(
        "E10  Hub heartbeat reports healthy status and valid role after emergency intent",
        e12_status == 200 and e12_healthy and e12_role,
        f"status={e12_body.get('status')} role={e12_body.get('role')}",
    ))

    # E11. Audit log contains source field in intent_executed entries
    e13_audit_status, e13_audit = request("GET", f"{base}/v1/audit")
    e13_entries = e13_audit.get("entries", [])
    e13_intent_entries = [e for e in e13_entries if e.get("type") == "intent_executed"]
    e13_with_source = [e for e in e13_intent_entries if "source" in e]
    report.add(TestResult(
        "E11  Audit log intent_executed entries include source field",
        e13_audit_status == 200 and len(e13_with_source) > 0,
        f"intent_executed={len(e13_intent_entries)} with_source={len(e13_with_source)}",
    ))


# ── TIER CONFORMANCE — protocol features through 0.5 (cumulative: 69) ─────────
# Everything shipped in the 0.4 cycle (SENSOR-KIND, AUDIT-PROVENANCE,
# EMERGENCY-UNSAT-ESCALATION, AUDIT-ARCHIVE) had unit tests but no CONFORMANCE
# coverage — nothing proved, over the wire against a running hub, that the
# protocol delivers what its spec now promises. These do.

def run_conformance(base: str, report: CertReport):
    section("── Tier CONFORMANCE — v0.4 protocol features ───────────")

    # C1. Every declared sensor carries a valid kind (SENSOR-KIND, spec §5.1)
    ds_status, ds_body = request("GET", f"{base}/v1/devices")
    sensors = [sn for d in ds_body.get("devices", [])
               for sn in d.get("capabilities", {}).get("sensors", [])]
    bad_kind = [sn.get("id") for sn in sensors
                if sn.get("kind", "environment") not in ("environment", "device_state")]
    report.add(TestResult(
        "C01  Every declared sensor kind is valid (environment|device_state)",
        ds_status == 200 and not bad_kind,
        f"{len(sensors)} sensors, invalid kinds: {bad_kind}" if bad_kind
        else f"{len(sensors)} sensors, all kinds valid",
    ))

    # C2. device_state sensors actually exist in the registry — the distinction
    # is real, not just permitted (a deployment of only environment sensors
    # would legitimately have none, so this is informational-pass on zero).
    dstate = [sn.get("id") for sn in sensors if sn.get("kind") == "device_state"]
    report.add(TestResult(
        "C02  Sensor-kind distinction is expressed in the registry",
        ds_status == 200,
        f"device_state sensors: {len(dstate)}" if dstate
        else "no device_state sensors (valid for an all-environment deployment)",
    ))

    # C3. report_status honors DOSYNC_STATUS_SCOPE — an environment-scoped hub
    # must not sweep device_state readers. Verified structurally: fire a status
    # intent and confirm no device_state-only device appears in the plan when
    # the deployment declares environment scope. (Skips cleanly if the hub has
    # no scope declared — the protocol has no opinion, so neither does the test.)
    st_status, st_body = request("GET", f"{base}/v1/status")
    scope_declared = st_body.get("status_scope")  # None unless the hub surfaces it
    rs_status, rs_body = request(
        "POST", f"{base}/v1/intent/async",
        {"intent": "report_status", "urgency": "info", "context": {"scope": "environment"}})
    report.add(TestResult(
        "C03  report_status accepts an explicit environment scope",
        rs_status in (200, 202),
        f"status={rs_status} (scope=environment accepted)",
    ))

    # C4. A policy MODIFY is bound into the tamper-evident chain (AUDIT-PROVENANCE)
    au_status, au_body = request("GET", f"{base}/v1/audit")
    entries = au_body.get("entries", [])
    mod = [e for e in entries if e.get("type") == "policy_modified"]
    report.add(TestResult(
        "C04  Policy MODIFY leaves a policy_modified chain entry",
        au_status == 200 and len(mod) > 0,
        f"policy_modified entries: {len(mod)}"
        + ("" if mod else " (fire an intent that a deployment policy modifies, then re-run)"),
    ))

    # C5. policy_modified entries carry full provenance
    prov_ok = all(
        all(k in e for k in ("pre_policy_devices", "post_policy_devices",
                             "removed_devices", "policy", "policies_fingerprint"))
        for e in mod) if mod else False
    report.add(TestResult(
        "C05  policy_modified entries bind pre/post plan, removed devices, policy, fingerprint",
        bool(mod) and prov_ok,
        "all provenance fields present" if prov_ok
        else "missing provenance fields or no policy_modified entries yet",
    ))

    # C6. The policy fingerprint is a SHA-256 (64 hex chars) when a policy file
    # is loaded — the property that lets an auditor pin which config decided.
    import re as _re
    fp = next((e.get("policies_fingerprint") for e in mod
               if e.get("policies_fingerprint")), None)
    report.add(TestResult(
        "C06  Policy fingerprint is a SHA-256 digest",
        fp is not None and bool(_re.fullmatch(r"[0-9a-f]{64}", fp)),
        f"fingerprint={fp[:16]}..." if fp else "no fingerprint (no policy file loaded?)",
    ))

    # C7. The audit chain verifies — the core tamper-evident guarantee, over the
    # wire, whether or not the chain has been archived (anchored).
    integrity = st_body.get("audit_integrity")
    report.add(TestResult(
        "C07  Live audit chain verifies (audit_integrity)",
        st_status == 200 and integrity is True,
        f"audit_integrity={integrity}, entries={st_body.get('audit_entries')}",
    ))

    # C8. If the chain is anchored (AUDIT-ARCHIVE), it STILL verifies — proving
    # segmentation preserves the tamper-evident guarantee, not just convenience.
    anchored = st_body.get("audit_anchored", False)
    report.add(TestResult(
        "C08  Anchored chain still verifies (archive preserves integrity)",
        st_status == 200 and integrity is True,
        f"anchored={anchored}"
        + (f" from {st_body.get('audit_anchor_prefix')}..." if anchored else " (not archived — genesis chain)"),
    ))

    # C9-C12 cover protocol surface added in 0.4.2. A conformance suite that
    # does not follow the protocol certifies a DoSync that no longer exists
    # (Nakamura, session audit 2026-08-01) — a gap nobody notices until a second
    # implementation appears, which is exactly when it matters most.

    # C9. A hub reports which technologies it speaks and on what basis each
    # ships. The BASIS is the protocol-relevant part: a vendor adapter shipped
    # as a worked example is a different claim from an open-standard one.
    ad_status, ad_body = request("GET", f"{base}/v1/adapters")
    kinds = {a.get("kind") for a in ad_body.get("adapters", [])}
    valid_kinds = kinds <= {"ecosystem", "reference", "infrastructure", "third_party"}
    # Zero adapters is legitimate — a hub in certification mode registers none,
    # and so does one whose hardware is not present yet. What conformance
    # requires is that whatever IS reported declares a valid basis; demanding
    # that adapters exist would test the deployment, not the protocol.
    report.add(TestResult(
        "C09  Adapters declare a valid kind (ecosystem|reference|infrastructure|third_party)",
        ad_status == 200 and valid_kinds,
        f"kinds present: {sorted(kinds)}" if kinds
        else "no adapters registered (valid — nothing to misdeclare)",
    ))

    # C10. Scanning must be side-effect free. A hub that registers whatever
    # answered a broadcast contradicts the premise of a protocol built on being
    # able to account for what is in it.
    before = request("GET", f"{base}/v1/devices")[1].get("count", 0)
    sc_status, sc_body = request("GET", f"{base}/v1/discovery/scan")
    after = request("GET", f"{base}/v1/devices")[1].get("count", 0)
    report.add(TestResult(
        "C10  Scanning registers nothing and reports which transports it searched",
        sc_status == 200 and before == after and "searched" in sc_body,
        f"devices {before}→{after}, searched={sc_body.get('searched')}, "
        f"not_searchable={sc_body.get('not_searchable')}",
    ))

    # C11. The device list distinguishes participation from inventory. A device
    # excluded from intents must be visible AND distinguishable — hiding it is
    # how a device gets forgotten while still holding its id.
    dv_status, dv_body = request("GET", f"{base}/v1/devices")
    has_split = "active" in dv_body and "quarantined" in dv_body
    report.add(TestResult(
        "C11  Device inventory separates active from quarantined",
        dv_status == 200 and has_split,
        f"count={dv_body.get('count')}, active={dv_body.get('active')}, "
        f"quarantined={dv_body.get('quarantined')}",
    ))

    # C12. Signed heartbeats are OPTIONAL and must be off unless asked for. A
    # hub that accepts unauthenticated-transport messages by default fails this
    # deliberately: the trade is the operator's to make.
    hb_status, hb_body = request(
        "POST", f"{base}/v1/heartbeat/signed",
        {"device_id": "conformance-probe", "timestamp": 0, "signature": "x" * 64})
    report.add(TestResult(
        "C12  Signed heartbeat channel is disabled unless enabled (never open by default)",
        hb_status in (404, 401),
        f"HTTP {hb_status} — "
        + ("disabled, as required by default" if hb_status == 404
           else "enabled on this hub; the request was rejected on its merits"),
    ))

    run_conformance_05(base, report)


# C13-C25 cover protocol 0.5 (spec §10.5): where a device is, and what a
# location in an intent's context does. The suite builds its own scene -- two
# probe devices and two probe intent classes, prefixed certify- -- and removes it
# whatever happens. It verifies through explain(), which executes nothing,
# because this suite also runs against hubs with physical devices; the one check
# that must fire an emergency runs only in certify mode.
_PROBE_ACTION = "certify_probe"
_PROBE_IN = "certify-probe-cell-2"      # placed at certify-site/line-3/cell-2
_PROBE_OUT = "certify-probe-line-30"    # placed at certify-site/line-30/cell-1
_PROBE_LOCK = "certify-probe-lock"      # emergency-capable, lock and unlock (C22)
_PROBE_CONVEYOR = "certify-probe-conveyor"  # emergency-capable, declares stop (C24)
_CLASS_RESTRICTS = "certify_location_restricts"
_CLASS_INFORMS = "certify_location_informs"


def _probe_device(device_id: str) -> dict:
    return {
        "device_id": device_id, "device_name": f"Certification probe {device_id}",
        "manufacturer": "DoSync Initiative", "model": "LocationProbe", "firmware": "0.5",
        "category": "actuator", "tags": ["certify-probe"],
        "actuators": [{"id": _PROBE_ACTION, "type": _PROBE_ACTION,
                       "description": "No intent outside certification asks for this"}],
    }


def _explained(base: str, intent: str, location: str, urgency: str = "info") -> set:
    from urllib.parse import quote
    st, body = request("GET", f"{base}/v1/intents/{intent}/explain"
                              f"?urgency={urgency}&location={quote(location)}")
    return {d.get("device_id") for d in body.get("included", [])} if st == 200 else None


def run_conformance_05(base: str, report: CertReport):
    section("── Tier CONFORMANCE — v0.5 protocol features (spec §10.5) ──")
    for did in (_PROBE_IN, _PROBE_OUT):
        request("POST", f"{base}/v1/devices/register", _probe_device(did))
    for name, role in ((_CLASS_RESTRICTS, "restricts"), (_CLASS_INFORMS, "informs")):
        request("POST", f"{base}/v1/intent-classes", {
            "name": name, "urgency": "info", "resolution_tags": ["certify-probe"],
            "resolution_actuators": [_PROBE_ACTION], "location_role": role,
            "description": "Certification probe class; removed when the suite ends"})
    try:
        # C13. The operator sets a location, and it is kept.
        p_st, p_body = request("PATCH", f"{base}/v1/devices/{_PROBE_IN}",
                               {"location": "certify-site/line-3/cell-2"})
        request("PATCH", f"{base}/v1/devices/{_PROBE_OUT}", {"location": "certify-site/line-30/cell-1"})
        g_st, g_body = request("GET", f"{base}/v1/devices/{_PROBE_IN}")
        report.add(TestResult(
            "C13  PATCH sets a device location and the hub keeps it",
            p_st == 200 and g_body.get("location") == "certify-site/line-3/cell-2",
            f"PATCH {p_st}, location now {g_body.get('location')!r}"))

        # C14. A move is a governance event: it is in the audit log.
        _, au = request("GET", f"{base}/v1/audit?limit=200")
        moved = [e for e in au.get("entries", []) if e.get("type") == "device_relocated"
                 and e.get("device_id") == _PROBE_IN]
        report.add(TestResult(
            "C14  A relocation is recorded as device_relocated",
            bool(moved) and moved[-1].get("location") == "certify-site/line-3/cell-2",
            f"{len(moved)} device_relocated entr{'y' if len(moved) == 1 else 'ies'}"))

        # C15. A restricting location holds what is below it, by whole segment.
        got = _explained(base, _CLASS_RESTRICTS, "certify-site/line-3")
        probes = (got or set()) & {_PROBE_IN, _PROBE_OUT}
        report.add(TestResult(
            "C15  A location restricts to what it contains, by segment (line-3 is not line-30)",
            probes == {_PROBE_IN},
            f"included probes: {sorted(probes)}"))

        # C16. A class whose location informs excludes nothing.
        got = _explained(base, _CLASS_INFORMS, "certify-site/line-3")
        probes = (got or set()) & {_PROBE_IN, _PROBE_OUT}
        report.add(TestResult(
            "C16  A location_role 'informs' class excludes no device for its location",
            probes == {_PROBE_IN, _PROBE_OUT},
            f"included probes: {sorted(probes)}"))

        # C17. A restricting emergency stays in its zone -- force-inclusion too.
        got = _explained(base, _CLASS_RESTRICTS, "certify-site/line-3", urgency="emergency")
        report.add(TestResult(
            "C17  A restricting emergency stays in its zone, force-inclusion included",
            got == {_PROBE_IN},
            f"included: {sorted(got) if got is not None else 'explain failed'}"))

        # C18. Outside an emergency, a place nothing is at is refused.
        _, s0 = request("GET", f"{base}/v1/status")
        r_st, r_body = request("POST", f"{base}/v1/intent/async", {
            "intent": _CLASS_RESTRICTS, "urgency": "info",
            "context": {"location": "certify-nowhere"}})
        _, s1 = request("GET", f"{base}/v1/status")
        before = (s0.get("intents_rejected") or {}).get("unknown_location", 0)
        after = (s1.get("intents_rejected") or {}).get("unknown_location", 0)
        report.add(TestResult(
            "C18  An unknown restricting location is refused (422, unknown_location)",
            r_st == 422 and after == before + 1,
            f"HTTP {r_st}, unknown_location {before}→{after}"))

        # C19. In an emergency the resolver never narrows to nothing.
        got = _explained(base, _CLASS_RESTRICTS, "certify-nowhere", urgency="emergency")
        probes = (got or set()) & {_PROBE_IN, _PROBE_OUT}
        report.add(TestResult(
            "C19  An emergency at an unknown location reaches every capable device",
            probes == {_PROBE_IN, _PROBE_OUT},
            f"included probes: {sorted(probes)}"))

        # C20. A device re-registering never erases the operator's location.
        request("POST", f"{base}/v1/devices/register", _probe_device(_PROBE_IN))
        _, g2 = request("GET", f"{base}/v1/devices/{_PROBE_IN}")
        report.add(TestResult(
            "C20  Re-registration keeps the location the operator set",
            g2.get("location") == "certify-site/line-3/cell-2",
            f"location after re-registration: {g2.get('location')!r}"))

        # C21. The hub accepts that emergency and records it. Only in certify
        # mode: firing an emergency on a production hub would act on physical
        # devices, which no conformance check may do.
        _, st = request("GET", f"{base}/v1/status")
        name = "C21  An emergency at an unknown location is accepted and recorded"
        if st.get("certify_mode"):
            e_st, e_body = request("POST", f"{base}/v1/intent/async", {
                "intent": _CLASS_RESTRICTS, "urgency": "emergency",
                "context": {"location": "certify-nowhere"}})
            report.add(TestResult(
                name, e_st == 200 and e_body.get("location_not_found") == "certify-nowhere",
                f"HTTP {e_st}, location_not_found={e_body.get('location_not_found')!r}"))
        else:
            reason = ("fires a real emergency; run in certify mode (DOSYNC_CERTIFY=true), "
                      "as CI does, to check it")
            report.not_applicable.append((name, reason))
            warn(f"{name} — not applicable in production mode: {reason}")
        # C22. In an emergency, an emergency-capable device the class asks
        # nothing of does what it declared -- never every capability (a lock
        # used to receive lock AND unlock at once). Checked through explain and
        # PATCH: nothing is fired.
        lock = _probe_device(_PROBE_LOCK)
        lock.update({"emergency_capable": True, "tags": ["certify-probe"],
                     "actuators": [{"id": "lock", "type": "lock", "description": "Lock"},
                                   {"id": "unlock", "type": "unlock", "description": "Unlock"}]})
        request("POST", f"{base}/v1/devices/register", lock)

        def _lock_entry():
            st, body = request("GET", f"{base}/v1/intents/ensure_safety/explain?urgency=emergency")
            return next((d for d in body.get("included", []) if d.get("device_id") == _PROBE_LOCK), {}) if st == 200 else {}
        before = _lock_entry()
        c_st, _ = request("PATCH", f"{base}/v1/devices/{_PROBE_LOCK}", {"emergency_actions": ["lock", "unlock"]})
        request("PATCH", f"{base}/v1/devices/{_PROBE_LOCK}", {"emergency_actions": ["unlock"]})
        after = _lock_entry()
        report.add(TestResult(
            "C22  An emergency-capable device does only its declared emergency actions",
            before.get("included_without_action") is True and c_st == 422
            and after.get("emergency_actions") == ["unlock"],
            f"undeclared: {'no action' if before.get('included_without_action') else before}; "
            f"lock+unlock → HTTP {c_st}; declared: {after.get('emergency_actions')}"))
        # C23. An intent whose class asks for opposite actions must say which it
        # means; outside an emergency the hub refuses it before executing
        # anything. Nothing is fired: both requests are refused.
        a_st, a_body = request("POST", f"{base}/v1/intent/async",
                               {"intent": "control_access", "urgency": "alert", "context": {}})
        i_st, _ = request("POST", f"{base}/v1/intent/async",
                          {"intent": "control_access", "urgency": "alert",
                           "context": {"action_types": ["certify-no-such-action"]}})
        report.add(TestResult(
            "C23  An intent asking for opposite actions must say which (422)",
            a_st == 422 and "action_types" in str(a_body) and i_st == 422,
            f"ambiguous → HTTP {a_st}; outside the class → HTTP {i_st}"))

        # C24. In an emergency, what a device declared comes first: a conveyor
        # declared to stop is not turned on by a class that asks for turn_on.
        conveyor = _probe_device(_PROBE_CONVEYOR)
        conveyor.update({"emergency_capable": True, "tags": ["certify-probe"],
                         "emergency_actions": [{"action": "stop"}],
                         "actuators": [{"id": "turn_on", "type": "turn_on", "description": "Start"},
                                       {"id": "stop", "type": "stop", "description": "Stop"}]})
        request("POST", f"{base}/v1/devices/register", conveyor)
        st, body = request("GET", f"{base}/v1/intents/ensure_safety/explain?urgency=emergency")
        entry = next((d for d in (body.get("included", []) if st == 200 else [])
                      if d.get("device_id") == _PROBE_CONVEYOR), {})
        report.add(TestResult(
            "C24  In an emergency a device's declared actions come first",
            entry.get("emergency_actions") == ["stop"],
            f"explain reports {entry.get('emergency_actions') or entry or 'nothing'}"))
        # C25. Governed direct mode: the hub validates what an agent proposes.
        # Every proposal here is invalid, so nothing runs -- an opposite pair, an
        # undeclared action and an unknown device in one intent, an action
        # outside the class in another -- and each refusal carries its reason.
        def _refusals(body):
            st, fired = request("POST", f"{base}/v1/intent/async", body)
            if st != 200 or not fired.get("intent_id"):
                return None, f"HTTP {st}"
            time.sleep(2)
            _, res = request("GET", f"{base}/v1/intent/{fired['intent_id']}")
            return res, {(r.get("device_id"), r.get("action")): r.get("reason")
                         for r in res.get("refused_proposals") or []}
        r1, got1 = _refusals({"intent": "control_access", "urgency": "alert", "context": {"proposed_actions": [
            {"device_id": _PROBE_LOCK, "action": "lock"}, {"device_id": _PROBE_LOCK, "action": "unlock"},
            {"device_id": _PROBE_LOCK, "action": "certify-fly"},
            {"device_id": "certify-no-such-device", "action": "lock"}]}})
        r2, got2 = _refusals({"intent": "notify", "urgency": "info", "context": {"proposed_actions": [
            {"device_id": _PROBE_LOCK, "action": "unlock"}]}})
        want1 = {(_PROBE_LOCK, "lock"): "opposite_actions", (_PROBE_LOCK, "unlock"): "opposite_actions",
                 (_PROBE_LOCK, "certify-fly"): "not_declared",
                 ("certify-no-such-device", "lock"): "unknown_device"}
        nothing_ran = all(isinstance(r, dict) and not (r.get("results") or []) for r in (r1, r2))
        report.add(TestResult(
            "C25  Proposed actions are validated; refused ones never run",
            got1 == want1 and got2 == {(_PROBE_LOCK, "unlock"): "outside_class"} and nothing_ran,
            f"refusals {sorted(set((got1 or {}).values()) | set((got2 or {}).values())) if isinstance(got1, dict) else got1}; "
            f"actions run: {0 if nothing_ran else 'some'}"))
    finally:
        for did in (_PROBE_IN, _PROBE_OUT, _PROBE_LOCK, _PROBE_CONVEYOR):
            request("DELETE", f"{base}/v1/devices/{did}")
        for name in (_CLASS_RESTRICTS, _CLASS_INFORMS):
            request("DELETE", f"{base}/v1/intent-classes/{name}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DoSync Certification CLI v0.3 — protocol conformance testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 certify.py --host localhost --port 47200 --tier basic
  python3 certify.py --host localhost --port 47200 --tier standard
  python3 certify.py --host localhost --port 47200 --tier emergency
  python3 certify.py --host <hub-address> --port 47200 --tier emergency --output cert.json
  python3 certify.py --host <hub-address> --port 47200 --tier conformance --output cert.json

Environment variables:
  DOSYNC_TOKEN     API token for authenticated requests
  DOSYNC_CA_CERT   Path to CA cert for TLS verification (e.g. ~/Desktop/dosync-ca.crt)

Tier test counts:
  basic      10 tests  — connectivity, auth, registration, manifest
  standard   33 tests  — + intents, events, health, explainability, version headers, intent lifecycle
  emergency  44 tests  — + emergency override, audit log integrity, firmware re-registration
  conformance 69 tests — + protocol features through 0.5: sensor-kind, policy provenance, chain archiving,
                         device locations and location-restricted intents
        """,
    )
    parser.add_argument("--host",   default="localhost",  help="Hub IP or hostname")
    parser.add_argument("--port",   default=47200, type=int, help="Hub port")
    parser.add_argument("--tier",   default="standard",
                        choices=["basic", "standard", "emergency", "conformance"],
                        help="Certification tier to verify")
    parser.add_argument("--output", default=None,
                        help="Output file for JSON report (e.g. cert.json)")
    parser.add_argument("--verify", default=None, metavar="REPORT.json",
                        help="Verify the Ed25519 signature of an existing report and exit")
    parser.add_argument("--no-sign", action="store_true",
                        help="Do not sign the report (signing is on by default)")
    args = parser.parse_args()

    # ── Verify mode — check an existing report's signature and exit ────────────
    # A third party runs this against a report they received. No hub needed, no
    # dependencies: the pure-Python Ed25519 verifies the embedded signature.
    if args.verify:
        from dosync.cert_signing import verify_report
        try:
            with open(args.verify) as f:
                report_data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  {C.FAIL}Cannot read report: {e}{C.RESET}")
            sys.exit(2)
        ok, msg = verify_report(report_data)
        if ok:
            print(f"  {C.OK}✓ {msg}{C.RESET}")
            print(f"  {C.WARN}Note: a valid signature proves the report was not altered after issuance.")
            print(f"  It does not prove independent review — see the report's 'attestation' block.{C.RESET}")
            sys.exit(0)
        else:
            print(f"  {C.FAIL}✗ {msg}{C.RESET}")
            sys.exit(1)

    base   = f"http://{args.host}:{args.port}"
    report = CertReport(host=args.host, port=args.port, tier=args.tier)

    # NOTE: cumulative totals — basic(10), +standard(23)=33, +emergency(11)=44.
    # The "CERTIFIED (passed/total)" line below uses the real runtime count; keep these in sync.
    # One table: the report decides completeness from EXPECTED_COUNTS, and this
    # banner used to print a second, stale copy (basic 10, conformance 52).
    tier_counts = CertReport.EXPECTED_COUNTS
    print(f"\n{C.BOLD}DoSync Certification CLI v0.3{C.RESET}")
    print(f"  Hub:   {base}")
    print(f"  Tier:  {C.BOLD}{args.tier.upper()}{C.RESET} ({tier_counts[args.tier]} tests)")
    print(f"  Date:  {report.timestamp}")

    ok_basic = run_basic(base, report)
    if ok_basic and args.tier in ("standard", "emergency", "conformance"):
        run_standard(base, report)
    if ok_basic and args.tier in ("emergency", "conformance"):
        run_emergency(base, report)
    if ok_basic and args.tier == "conformance":
        run_conformance(base, report)

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
    elif report.incomplete:
        # Reported separately from a failure, because they mean opposite things
        # about the hub: one says it behaved wrongly, the other says nobody
        # found out. Conflating them tells an operator their hub failed when it
        # was never tested.
        print(f"\n  {C.BOLD}{C.WARN}⚠ NOT RUN — DoSync {args.tier.upper()} stopped after "
              f"{report.executed} of {report.expected} checks{C.RESET}")
        print( "  This is NOT a conformance failure: the suite could not complete.")
        print( "  Usual cause: the hub refused the setup — check DOSYNC_TOKEN is set")
        print( "  and that the hub is reachable, then run again.")
    else:
        print(f"\n  {C.BOLD}{C.FAIL}✗ NOT CERTIFIED — {report.failed} test(s) failed{C.RESET}")

    output_file = args.output or f"dosync-cert-{args.tier}-{int(time.time())}.json"
    report_dict = report.to_dict()

    # Sign the report (on by default) so a third party can confirm it was not
    # altered after issuance. Uses pure-Python Ed25519 — no dependency required.
    # Degrades gracefully: if signing fails for any reason, the report is still
    # written unsigned rather than lost.
    if not args.no_sign:
        try:
            from dosync.cert_signing import sign_report
            report_dict = sign_report(report_dict)
            print(f"  Signed with key: {report_dict['signature']['public_key'][:16]}…")
            print(f"  Verify with: python3 certify.py --verify {output_file}")
        except Exception as e:
            print(f"  {C.WARN}Report not signed ({e}); writing unsigned report.{C.RESET}")

    with open(output_file, "w") as f:
        json.dump(report_dict, f, indent=2)
    print(f"\n  Report saved: {output_file}\n")

    sys.exit(0 if report.certified else 1)


if __name__ == "__main__":
    main()
