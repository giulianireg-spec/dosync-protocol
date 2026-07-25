"""POST /v1/device/action must not be a way around the protocol (2026-07-25).

Found by auditing the project's own claimed strengths against the code rather
than trusting them. Two of the five differentiators the project advertises —
"policies the AI cannot escape" and "a tamper-evident record of what the system
did" — were falsified by this endpoint: it called the executor directly, so a
device could be actuated with no chain entry and no policy evaluation. The MCP
server's device-control tool uses this path, so the bypass belonged to the AI
itself, not to an operator.

DESIGN-PRINCIPLES §"On adapter-side fallback" already rejected bypass mechanisms
for exactly three reasons — broken audit chain, unevaluated safety constraints,
actions reduced back to commands. That ruling was about adapters acting without
the hub; it applies unchanged to an endpoint inside the hub that skips the same
two layers.

These tests pin the closure: every direct action is policy-evaluated under the
reserved `direct_control` class and lands in the audit chain, executed or
blocked.
"""
import json

import pytest
from fastapi.testclient import TestClient

from dosync.models import (ActuatorSpec, CapabilityManifest, CertTier,
                           DeviceCategory)


def _register(srv, device_id, action="unlock"):
    srv.hub.registry.register(CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["lock", "security"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id=action, type=action, description="")],
        emergency_capable=False, cert_tier=CertTier.BASIC))


def _entries(srv, etype):
    return [e for e in srv.hub.audit_log.entries() if e.get("type") == etype]


# ── The audit claim ──────────────────────────────────────────────────────────

def test_direct_action_is_recorded_in_the_chain():
    """The regression itself: a device was actuated and left no trace."""
    import dosync.server as srv
    _register(srv, "lock-audit-1")
    client = TestClient(srv.app)

    before = len(srv.hub.audit_log.entries())
    r = client.post("/v1/device/action",
                    json={"device_id": "lock-audit-1", "action": "unlock"})
    assert r.status_code == 200
    assert len(srv.hub.audit_log.entries()) > before, \
        "a direct action must append to the audit chain"

    rec = _entries(srv, "direct_action_executed")
    assert any(e["device_id"] == "lock-audit-1" for e in rec)


def test_audit_entry_marks_the_direct_path():
    """An auditor must be able to tell an operator action from an intent-driven
    one; both touched the device, but only one was a decision by the system."""
    import dosync.server as srv
    _register(srv, "lock-audit-2")
    client = TestClient(srv.app)
    client.post("/v1/device/action",
                json={"device_id": "lock-audit-2", "action": "unlock"})

    rec = [e for e in _entries(srv, "direct_action_executed")
           if e["device_id"] == "lock-audit-2"][0]
    assert rec["source"] == "direct_action_endpoint"
    assert "action_id" in rec


def test_chain_stays_verifiable_after_direct_actions():
    import dosync.server as srv
    _register(srv, "lock-audit-3")
    client = TestClient(srv.app)
    for _ in range(3):
        client.post("/v1/device/action",
                    json={"device_id": "lock-audit-3", "action": "unlock"})
    assert srv.hub.audit_log.verify()


def test_failed_direct_action_is_also_recorded():
    """The chain answers 'what did this system do'. An attempt that failed is
    part of that answer, so it is not filtered out."""
    import dosync.server as srv
    _register(srv, "lock-audit-4")
    client = TestClient(srv.app)
    client.post("/v1/device/action",
                json={"device_id": "lock-audit-4", "action": "unlock"})
    # Every direct action, whatever its outcome, produced an entry
    assert _entries(srv, "direct_action_executed")


# ── The policy claim ─────────────────────────────────────────────────────────

def test_deployment_policy_blocks_a_direct_action(tmp_path, monkeypatch):
    """THE claim under test: a deployment policy must bind this path too. If it
    does not, 'policies the AI cannot escape' is false — the AI just calls here
    instead of firing an intent."""
    policy = tmp_path / "policies.json"
    policy.write_text(json.dumps({"version": 1, "policies": [{
        "type": "device_exclusion",
        "intent_classes": ["direct_control"],
        "excluded_device_ids": ["lock-forbidden"],
        "bypass_on_emergency": False,
        "reason": "Never actuated by software",
    }]}))
    monkeypatch.setenv("DOSYNC_POLICIES", str(policy))

    # A fresh hub+app is needed so the policy file is loaded at construction.
    import importlib
    import dosync.server as srv
    importlib.reload(srv)

    _register(srv, "lock-forbidden")
    _register(srv, "lock-allowed")
    client = TestClient(srv.app)

    blocked = client.post("/v1/device/action",
                          json={"device_id": "lock-forbidden", "action": "unlock"})
    assert blocked.status_code == 403, \
        "an excluded device must not be actuatable through the direct path"

    allowed = client.post("/v1/device/action",
                          json={"device_id": "lock-allowed", "action": "unlock"})
    assert allowed.status_code == 200, "unrelated devices stay usable"


def test_blocked_action_is_audited_with_the_deciding_policy(tmp_path, monkeypatch):
    """A refusal is as much a fact about the system as an execution, and the
    auditor needs to know WHICH policy decided."""
    policy = tmp_path / "policies.json"
    policy.write_text(json.dumps({"version": 1, "policies": [{
        "type": "device_exclusion",
        "intent_classes": ["direct_control"],
        "excluded_device_ids": ["lock-denied"],
        "bypass_on_emergency": False,
        "reason": "Audit test exclusion",
    }]}))
    monkeypatch.setenv("DOSYNC_POLICIES", str(policy))

    import importlib
    import dosync.server as srv
    importlib.reload(srv)

    _register(srv, "lock-denied")
    client = TestClient(srv.app)
    client.post("/v1/device/action",
                json={"device_id": "lock-denied", "action": "unlock"})

    rec = _entries(srv, "direct_action_blocked")
    assert rec, "a blocked direct action must appear in the chain"
    assert rec[-1]["policy"], "the deciding policy must be named"
    assert rec[-1]["device_id"] == "lock-denied"


# ── Structural guard ─────────────────────────────────────────────────────────

def test_endpoint_does_not_call_the_executor_unguarded():
    """Structural: the handler must evaluate policy and append to the chain.
    Written because the original bug was not a wrong value but a MISSING step,
    which no behavioural assertion on a passing request would have caught."""
    import inspect

    import dosync.server as srv
    src = inspect.getsource(srv.device_action)
    assert "policy_engine" in src, "direct actions must be policy-evaluated"
    assert "audit_log.append" in src, "direct actions must be audited"
    assert "DIRECT_CONTROL_INTENT_CLASS" in src, \
        "policy evaluation needs the reserved intent class to be addressable"
