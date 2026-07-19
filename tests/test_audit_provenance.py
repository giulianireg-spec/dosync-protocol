"""AUDIT-PROVENANCE + EMERGENCY-UNSAT-ESCALATION (2026-07-18, from external review).

Until today a policy MODIFY — the most common policy decision — left its trace in
the runtime log and NOWHERE in the tamper-evident chain, while BLOCK and CONFIRM
were chain-bound. And stacked absolute exclusions could empty an EMERGENCY plan,
which executed zero actions silently with status "completed".

The chain must bind the DECISION, not only the commands sent; and an
unsatisfiable emergency must be honored (the operator's judgment is final) but
never silent.
"""
import asyncio
import hashlib
import json

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, CertTier,
                           DeviceCategory, Intent, IntentClass, Urgency)
from dosync.policies import DeviceExclusionPolicy, PolicyEngine
from dosync.policy_config import (lint_emergency_satisfiability, load_into,
                                  load_policies)


def _hub_with(*device_specs):
    hub = DoSyncHub(db_path=":memory:")
    for did, tags, atype in device_specs:
        hub.registry.register(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t",
            firmware="1", category=DeviceCategory.ACTUATOR, tags=tags,
            sensors=[], events=[],
            actuators=[ActuatorSpec(id="a", type=atype, description="")],
            emergency_capable=True, cert_tier=CertTier.STANDARD))
    return hub


class _NullExecutor:
    async def execute(self, action, urgency):
        from dosync.models import ActionResult
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True)


def _entries(hub, etype):
    return [e for e in hub.audit_log.entries() if e.get("type") == etype]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── policy_modified is chain-bound ───────────────────────────────────────────

def test_modify_leaves_a_chain_entry_with_full_provenance(tmp_path):
    """THE fix: what was proposed, what was removed, which policy decided, and
    the fingerprint of the exact file that was loaded."""
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x"], "bypass_on_emergency": False}]}))

    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    engine = PolicyEngine()
    load_into(engine, pol, hub=hub)
    hub.policy_engine = engine

    intent = Intent(intent_id="t1", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    _run(hub.execute_intent(intent, _NullExecutor()))

    entries = _entries(hub, "policy_modified")
    assert len(entries) == 1, "a MODIFY must leave exactly one chain entry"
    e = entries[0]
    assert e["removed_devices"] == ["tv-x"]
    assert "tv-x" in e["pre_policy_devices"]
    assert "tv-x" not in e["post_policy_devices"]
    assert "siren-x" in e["post_policy_devices"]
    assert e["policy"] == "device_exclusion"
    # the fingerprint is the sha256 of the loaded bytes, verifiable independently
    assert e["policies_fingerprint"] == hashlib.sha256(
        pol.read_text().encode()).hexdigest()


def test_decision_is_reconstructible_from_the_chain_alone(tmp_path):
    """The backlog's validation clause: the care-facility scenario must be fully
    reconstructible from the chain — no journal, no explain endpoint."""
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["ward-lights"], "bypass_on_emergency": False,
         "reason": "photosensitive patients — never flash this wing"}]}))
    hub = _hub_with(("ward-lights", ["light", "emergency"], "turn_on"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    engine = PolicyEngine(); load_into(engine, pol, hub=hub)
    hub.policy_engine = engine
    _run(hub.execute_intent(
        Intent(intent_id="t2", intent=IntentClass("ensure_safety"),
               urgency=Urgency.EMERGENCY, context={}), _NullExecutor()))

    # reconstruct using ONLY the chain:
    chain = hub.audit_log.entries()
    mod = next(e for e in chain if e.get("type") == "policy_modified")
    executed = next(e for e in chain if e.get("type") == "intent_executed")
    assert mod["pre_policy_devices"] == ["siren-x", "ward-lights"]   # proposed
    assert mod["post_policy_devices"] == ["siren-x"]                 # decided
    assert "photosensitive" in mod["reason"]
    assert executed["actions"] == 1                                  # what ran


def test_fingerprint_is_none_without_a_policy_file():
    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    engine = PolicyEngine()
    engine.add(DeviceExclusionPolicy(intent_classes=["ensure_safety"],
                                     excluded_device_ids=["tv-x"],
                                     bypass_on_emergency=False))
    hub.policy_engine = engine
    _run(hub.execute_intent(
        Intent(intent_id="t3", intent=IntentClass("ensure_safety"),
               urgency=Urgency.EMERGENCY, context={}), _NullExecutor()))
    assert _entries(hub, "policy_modified")[0]["policies_fingerprint"] is None


def test_chain_stays_verifiable_with_new_entry_types(tmp_path):
    """New entry types must not break the hash chain."""
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x"], "bypass_on_emergency": False}]}))
    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    engine = PolicyEngine(); load_into(engine, pol, hub=hub)
    hub.policy_engine = engine
    _run(hub.execute_intent(
        Intent(intent_id="t4", intent=IntentClass("ensure_safety"),
               urgency=Urgency.EMERGENCY, context={}), _NullExecutor()))
    assert hub.audit_log.verify(), "chain must verify with policy_modified entries present"


# ── emergency_unsatisfiable is loud, not silent ──────────────────────────────

def test_emptied_emergency_leaves_a_dedicated_entry(tmp_path):
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x", "siren-x"],
         "bypass_on_emergency": False}]}))
    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    engine = PolicyEngine(); load_into(engine, pol, hub=hub)
    hub.policy_engine = engine
    _run(hub.execute_intent(
        Intent(intent_id="t5", intent=IntentClass("ensure_safety"),
               urgency=Urgency.EMERGENCY, context={}), _NullExecutor()))

    unsat = _entries(hub, "emergency_unsatisfiable")
    assert len(unsat) == 1, "an emptied EMERGENCY must be loud in the chain"
    assert unsat[0]["resolved_devices"] == ["siren-x", "tv-x"]
    # the rules were HONORED — nothing executed; refusing to obey is not the fix
    assert _entries(hub, "intent_executed")[0]["actions"] == 0


def test_emptied_non_emergency_stays_quiet(tmp_path):
    """Emptying an INFO-urgency plan is a normal preference, not an incident."""
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["notify"],
         "excluded_device_ids": ["tv-x"], "bypass_on_emergency": False}]}))
    hub = _hub_with(("tv-x", ["communication", "notification"], "notify"))
    engine = PolicyEngine(); load_into(engine, pol, hub=hub)
    hub.policy_engine = engine
    _run(hub.execute_intent(
        Intent(intent_id="t6", intent=IntentClass("notify"),
               urgency=Urgency.INFO, context={}), _NullExecutor()))
    assert _entries(hub, "emergency_unsatisfiable") == []


# ── the config-load lint ─────────────────────────────────────────────────────

def test_lint_warns_when_rules_empty_an_emergency(tmp_path):
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x", "siren-x"],
         "bypass_on_emergency": False}]}))
    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    policies = load_policies(pol, hub=hub)
    warnings = lint_emergency_satisfiability(hub, policies)
    assert len(warnings) == 1
    assert "UNSATISFIABLE" in warnings[0]


def test_lint_quiet_when_rules_are_survivable(tmp_path):
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x"], "bypass_on_emergency": False}]}))
    hub = _hub_with(("tv-x", ["communication", "emergency"], "notify"),
                    ("siren-x", ["alarm", "emergency"], "alarm"))
    policies = load_policies(pol, hub=hub)
    assert lint_emergency_satisfiability(hub, policies) == []


def test_lint_quiet_on_empty_registry(tmp_path):
    """An empty registry is not the policies' fault."""
    pol = tmp_path / "pol.json"
    pol.write_text(json.dumps({"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["x"], "bypass_on_emergency": False}]}))
    hub = DoSyncHub(db_path=":memory:")
    policies = load_policies(pol, hub=hub)
    assert lint_emergency_satisfiability(hub, policies) == []
