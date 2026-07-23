"""Unit tests for the integration suite's logic (C2).

DESIGN NOTE — why this test does NOT start a server (rewritten 2026-07-14):

The first version booted uvicorn in a thread and ran integration.py against it as
a subprocess. That was wrong in a way worth recording, because it failed for a
real reason rather than a flaky one:

It set DOSYNC_CERTIFY=1 inside the serving thread, expecting an in-memory DB. But
six other test files `import server` too, and Python caches modules — whichever
imports first builds the hub, with whatever env was set at THAT moment.
Alphabetically test_composition_kind_endpoint.py wins, so by the time this test
ran, server.hub was already bound to the real dosync.db. The suite then fired real
intents at it and wrote to its audit log, which is how CI surfaced
`I04 audit chain intact — audit_integrity=False`: concurrent writers on a shared
DB. The test could corrupt a developer's database — exactly the hazard behind the
project rule that DOSYNC_CERTIFY must use :memory:, never a real DB.

The fix is not to fight the import cache; it is to test the right thing here. This
file pins integration.py's LOGIC — outcome classification and report shape — which
is deterministic, fast, and touches nothing. Whether integration.py actually drives
devices is validated by running it against a live deployment, which is what the
tool is for; a CI runner has no devices, so simulating them proves nothing about
the real thing.
"""

import json

import integration


# ── Outcome classification ───────────────────────────────────────────────────

def test_classify_full_success():
    outcome, detail = integration._classify(
        {"status": "completed", "actions_taken": 12, "failed_devices": []})
    assert outcome == "executed"
    assert "12 action(s) executed" in detail


def test_classify_counts_failed_actions_not_devices():
    """The wording fix: failed_devices lists a device_id PER FAILED ACTION and is
    not deduplicated. A WiZ bulb exposes several actuators, so one powered-off
    bulb contributes several entries. Reporting len(failed) as 'devices failed'
    read as '11 dead bulbs' when it was 11 actions across ~4 bulbs."""
    poll = {
        "status": "completed",
        "actions_taken": 25,
        "failed_devices": ["wiz-a", "wiz-a", "wiz-a",
                           "wiz-b", "wiz-b", "wiz-b",
                           "wiz-c", "wiz-c", "wiz-c",
                           "wiz-d", "wiz-d"],
    }
    outcome, detail = integration._classify(poll)
    assert outcome == "partial"
    assert "11 action(s) failed" in detail, detail
    assert "across 4 device(s)" in detail, detail
    assert "11 device(s) failed" not in detail      # the misleading phrasing


def test_classify_no_op_when_nothing_executed():
    outcome, _ = integration._classify(
        {"status": "failed", "actions_taken": 0, "failed_devices": []})
    assert outcome == "no-op"


def test_classify_zero_actions_resolved():
    outcome, detail = integration._classify(
        {"status": "completed", "actions_taken": 0, "failed_devices": []})
    assert outcome == "no-op"
    assert "zero actions" in detail


def test_classify_timeout_is_error():
    outcome, _ = integration._classify(
        {"status": "timeout", "actions_taken": 0, "failed_devices": []})
    assert outcome == "error"


# ── Report shape ─────────────────────────────────────────────────────────────

def test_report_is_marked_as_physical_execution_not_a_cert():
    """An integration report must never be mistakable for a conformance cert."""
    report = integration.IntegrationReport("10.0.0.1", 47200)
    report.add(integration.IntegrationResult("I01 something", "executed", "3 actions"))
    report.add(integration.IntegrationResult("I02 other", "no-op", ""))
    doc = report.to_dict()

    assert doc["kind"] == "physical-execution"
    assert "dosync_integration_version" in doc
    assert "conformance" in doc["note"].lower()      # states what it is NOT
    assert doc["summary"] == {"executed": 1, "no-op": 1}
    assert len(doc["results"]) == 2
    json.dumps(doc)                                   # must be serializable


def test_report_summary_counts_every_outcome():
    report = integration.IntegrationReport("h", 1)
    for outcome in ("executed", "executed", "partial", "no-op", "error"):
        report.add(integration.IntegrationResult(f"t-{outcome}", outcome))
    assert report.to_dict()["summary"] == {
        "executed": 2, "partial": 1, "no-op": 1, "error": 1}


# ── Separation from conformance ──────────────────────────────────────────────

def test_integration_reuses_certify_plumbing_not_a_second_copy():
    """One source of truth for HTTP/reporting."""
    import certify
    assert integration.request is certify.request
    assert integration.fire_intent is certify.fire_intent


def test_certify_remains_conformance_only():
    """certify.py must not acquire polling execution: conformance stays
    deterministic and hardware-free (docs/CONFORMANCE-VS-INTEGRATION.md).

    Uses the AST rather than grep: a substring search matches the module
    docstring and the `def fire_intent` line, which are not calls. Asserting on
    real call nodes is the difference between testing the invariant and testing
    the text that happens to describe it.
    """
    import ast
    import pathlib
    # Implementation lives in the package; repo-root certify.py is a shim.
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "dosync" / "certify.py").read_text()
    tree = ast.parse(src)

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "fire_intent_conformance" in called, \
        "certify.py must fire intents in acceptance mode"
    assert "fire_intent" not in called, \
        "certify.py calls the polling helper — conformance must not wait on hardware"
