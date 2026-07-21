"""MCP-V13 — partial progress before the global timeout (2026-07-21).

Before this, a poll of a still-executing intent returned an opaque
{status: pending} and the MCP tool, on timeout, said only "still processing —
check the audit log". If 8 of 10 emergency actions had already fired, that
information was lost. Now the hub publishes progress as each action completes,
the poll exposes it, and the MCP reports what already happened.
"""
import asyncio
import os

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActionResult, ActuatorSpec, CapabilityManifest,
                           CertTier, DeviceCategory, Intent, IntentClass, Urgency)


def _hub(*device_ids):
    hub = DoSyncHub(db_path=":memory:")
    for did in device_ids:
        hub.registry.register(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t",
            firmware="1", category=DeviceCategory.ACTUATOR,
            tags=["alarm", "emergency"], sensors=[], events=[],
            actuators=[ActuatorSpec(id="alarm", type="alarm", description="")],
            emergency_capable=True, cert_tier=CertTier.STANDARD))
    return hub


class _MixedExecutor:
    """fast-* return immediately; slow-* hang past the intent timeout."""
    async def execute(self, action, urgency):
        if "slow" in action.device_id:
            await asyncio.sleep(10)
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True)


def test_progress_cb_fires_per_completed_action():
    hub = _hub("fast-1", "fast-2", "fast-3")
    seen = []
    intent = Intent(intent_id="t1", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _MixedExecutor(), progress_cb=lambda r: seen.append(r.device_id)))
    assert sorted(seen) == ["fast-1", "fast-2", "fast-3"]


def test_fast_actions_captured_before_a_slow_one_times_out():
    """THE scenario: fast devices are recorded as partial progress even while a
    slow device is still hanging — the information that used to be lost."""
    os.environ["DOSYNC_INTENT_TIMEOUT"] = "2"
    hub = _hub("fast-1", "fast-2", "slow-1")
    seen = []
    intent = Intent(intent_id="t2", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    result = asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _MixedExecutor(), progress_cb=lambda r: seen.append(r.device_id)))
    # both fast devices captured; slow-1 timed out (recorded as a failed result)
    assert "fast-1" in seen and "fast-2" in seen
    assert result.status in ("partial", "partial_abort")


def test_callback_failure_never_breaks_execution():
    """An observer cannot break the observed: a raising progress_cb is swallowed
    and execution completes normally."""
    hub = _hub("fast-1", "fast-2")
    def boom(r): raise RuntimeError("observer blew up")
    intent = Intent(intent_id="t3", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    result = asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _MixedExecutor(), progress_cb=boom))
    assert len(result.results) == 2   # execution unaffected


def test_no_callback_is_the_default_path():
    """execute_intent without progress_cb behaves exactly as before."""
    hub = _hub("fast-1", "fast-2")
    intent = Intent(intent_id="t4", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    result = asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _MixedExecutor()))
    assert len(result.results) == 2


def test_poll_exposes_partial_over_http():
    """End to end: a pending poll carries a 'partial' block with completed actions."""
    from fastapi.testclient import TestClient
    import server as srv

    os.environ["DOSYNC_INTENT_TIMEOUT"] = "2"
    for did in ("fast-A", "slow-B"):
        srv.hub.registry.register(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t",
            firmware="1", category=DeviceCategory.ACTUATOR,
            tags=["alarm", "emergency"], sensors=[], events=[],
            actuators=[ActuatorSpec(id="alarm", type="alarm", description="")],
            emergency_capable=True, cert_tier=CertTier.STANDARD))

    # swap in the mixed executor for this test
    srv.executor = _MixedExecutor()
    client = TestClient(srv.app)

    fire = client.post("/v1/intent/async",
                       json={"intent": "ensure_safety", "urgency": "emergency", "context": {}})
    assert fire.status_code == 200
    iid = fire.json()["intent_id"]

    # poll while slow-B is still hanging
    import time as _t
    _t.sleep(1.0)
    poll = client.get(f"/v1/intent/{iid}")
    body = poll.json()
    if body["status"] == "pending":
        assert "partial" in body
        assert "actions_completed" in body["partial"]
