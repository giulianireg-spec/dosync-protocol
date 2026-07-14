#!/usr/bin/env python3
"""
DoSync — integration test suite (C2).
=====================================

This is the SIBLING of certify.py, and the distinction matters for anyone
running these in their own lab:

  * certify.py — PROTOCOL CONFORMANCE. Deterministic, hardware-free, green by
    design. It checks that the hub speaks the protocol correctly (accepts valid
    intents, rejects malformed ones, exposes the required endpoints, keeps the
    audit chain intact). It fires intents in ACCEPTANCE mode and never waits for
    a physical device to move. A conformant hub passes it every time.

  * integration.py (this file) — PHYSICAL EXECUTION. It fires real intents and
    POLLS to completion, then reports what actually happened on the devices.
    Its results depend on the deployment: how many devices are powered on,
    reachable, and responsive. A device being off is a real-world condition, not
    a protocol failure — so this suite REPORTS outcomes (executed / partial /
    no-op) rather than PASS/FAIL-ing the protocol. It answers "does my hardware
    actually do the thing", which conformance deliberately does not.

Why separate files: mixing them means a powered-off bulb turns a protocol
certification red, which is wrong. Conformance certifies the protocol; integration
exercises the deployment. Keep them apart so each answers exactly one question.

Usage:
    DOSYNC_TOKEN=<token> DOSYNC_CA_CERT=certs/ca.crt \\
        python3 integration.py --host 192.168.100.109 --port 47200

    # JSON report for the record:
    python3 integration.py --host <ip> --port 47200 --json integration-<date>.json

This suite reuses certify.py's helpers (request, fire_intent with polling,
CertReport, TestResult) — one source of truth for HTTP and reporting.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

# Reuse the conformance tool's plumbing — do not reimplement HTTP or reporting.
import certify
from certify import request, fire_intent, C


# ── Integration outcomes ──────────────────────────────────────────────────────
# Unlike conformance PASS/FAIL, integration records what physically happened.

class IntegrationResult:
    def __init__(self, name: str, outcome: str, detail: str = ""):
        self.name = name
        self.outcome = outcome        # executed | partial | no-op | error
        self.detail = detail


class IntegrationReport:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.results: list[IntegrationResult] = []

    def add(self, r: IntegrationResult):
        self.results.append(r)
        symbol = {"executed": C.OK + "✓" + C.RESET,
                  "partial":  C.WARN + "~" + C.RESET,
                  "no-op":    C.WARN + "○" + C.RESET,
                  "error":    C.FAIL + "✗" + C.RESET}.get(r.outcome, "?")
        line = f"  {symbol}  {r.name}"
        if r.detail:
            line += f" — {r.detail}"
        print(line)

    def to_dict(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.outcome] = counts.get(r.outcome, 0) + 1
        return {
            "dosync_integration_version": "0.1",
            "kind": "physical-execution",   # NOT a conformance certification
            "hub": f"{self.host}:{self.port}",
            "timestamp": self.timestamp,
            "summary": counts,
            "results": [{"name": r.name, "outcome": r.outcome, "detail": r.detail}
                        for r in self.results],
            "note": ("This report reflects PHYSICAL execution outcomes on a specific "
                     "deployment at a point in time. It is not a protocol conformance "
                     "certification (see certify.py). Outcomes depend on which devices "
                     "were powered and reachable."),
        }


def _classify(poll: dict) -> tuple[str, str]:
    """Turn a completed intent poll into an integration outcome + detail."""
    status = poll.get("status")
    taken = poll.get("actions_taken", 0)
    failed = poll.get("failed_devices", []) or []
    if status == "timeout":
        return "error", "intent did not complete within the poll window"
    if status == "failed" and taken == 0:
        return "no-op", "no device actions executed (devices off/unreachable?)"
    if failed:
        return "partial", f"{taken} action(s) executed, {len(failed)} device(s) failed"
    if taken > 0:
        return "executed", f"{taken} action(s) executed on real devices"
    return "no-op", "resolved to zero actions"


def run_integration(base: str, report: IntegrationReport) -> None:
    print(f"\n{C.BOLD}── Physical execution — real intents, polled to completion ──{C.RESET}")

    # I1 — ensure_safety at emergency actually drives devices
    status, poll = fire_intent(base, {
        "intent": "ensure_safety", "urgency": "emergency", "context": {}})
    if status != 200:
        report.add(IntegrationResult("I01  ensure_safety [emergency] executes", "error",
                                     f"hub returned status={status}"))
    else:
        outcome, detail = _classify(poll)
        report.add(IntegrationResult("I01  ensure_safety [emergency] executes", outcome, detail))

    # I2 — report_status reads sensors (read-only path, F4a)
    status, poll = fire_intent(base, {
        "intent": "report_status", "urgency": "info", "context": {}})
    if status != 200:
        report.add(IntegrationResult("I02  report_status [info] reads sensors", "error",
                                     f"hub returned status={status}"))
    else:
        taken = poll.get("actions_taken", 0)
        outcome = "executed" if taken > 0 else "no-op"
        report.add(IntegrationResult("I02  report_status [info] reads sensors", outcome,
                                     f"{taken} sensor read(s)"))

    # I3 — notify delivers (SMS/display), if a notifier is present & reachable
    status, poll = fire_intent(base, {
        "intent": "notify", "urgency": "info",
        "context": {"message": "DoSync integration test"}})
    if status != 200:
        report.add(IntegrationResult("I03  notify [info] delivers", "error",
                                     f"hub returned status={status}"))
    else:
        outcome, detail = _classify(poll)
        report.add(IntegrationResult("I03  notify [info] delivers", outcome, detail))

    # I4 — the audit chain is still intact after real execution
    status, body = request("GET", f"{base}/v1/status")
    integ = body.get("audit_integrity") if status == 200 else None
    report.add(IntegrationResult(
        "I04  audit chain intact after execution",
        "executed" if integ else "error",
        f"audit_integrity={integ}"))


def main() -> int:
    ap = argparse.ArgumentParser(description="DoSync physical integration suite")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=47200)
    ap.add_argument("--json", default=None, help="write the integration report to this path")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"

    print(f"{C.BOLD}DoSync Integration (physical execution) v0.1{C.RESET}")
    print(f"  Hub:  {base}")
    print(f"  {C.WARN}Note{C.RESET}: outcomes depend on which devices are powered and reachable.")
    print(f"        This is NOT a protocol conformance run — use certify.py for that.")

    report = IntegrationReport(args.host, args.port)
    try:
        run_integration(base, report)
    except Exception as e:
        print(f"{C.FAIL}Integration run error: {e}{C.RESET}")
        return 2

    counts = {}
    for r in report.results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    print(f"\n{C.BOLD}── Outcomes ──{C.RESET}")
    print("  " + " · ".join(f"{k}: {v}" for k, v in sorted(counts.items())))

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\n  Report saved: {args.json}")

    # Integration never "fails" the protocol; it exits 0 unless the hub was
    # unreachable/errored (outcome 'error' on the structural checks).
    hard_errors = sum(1 for r in report.results
                      if r.outcome == "error" and ("audit" in r.name or "status=" in r.detail))
    return 1 if hard_errors else 0


if __name__ == "__main__":
    sys.exit(main())
