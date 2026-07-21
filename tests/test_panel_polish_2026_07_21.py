"""Polish items from the parada técnica 2026-07-21.

#1 heartbeat report size limit (Sosa — abuse surface)
#7 progress_cb failure counter surfaced (Paredes — swallowed != invisible)
"""
import asyncio

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActionResult, ActuatorSpec, CapabilityManifest,
                           CertTier, DeviceCategory, Intent, IntentClass, Urgency)


# ── #1 heartbeat report bounds ───────────────────────────────────────────────

def test_heartbeat_report_within_limits_accepted():
    from server import HeartbeatRequest
    hb = HeartbeatRequest(device_id="d", report={"battery_pct": 82, "rssi": -67,
                                                 "firmware": "2.1.4"})
    assert hb.report["battery_pct"] == 82


def test_heartbeat_report_too_many_keys_rejected():
    from server import HeartbeatRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        HeartbeatRequest(device_id="d", report={f"k{i}": i for i in range(40)})


def test_heartbeat_report_too_large_rejected():
    from server import HeartbeatRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        HeartbeatRequest(device_id="d", report={"blob": "x" * 5000})


def test_heartbeat_empty_report_is_fine():
    from server import HeartbeatRequest
    hb = HeartbeatRequest(device_id="d")
    assert hb.report == {}


# ── #7 progress_cb failure counter ───────────────────────────────────────────

def _hub_with_actuators():
    hub = DoSyncHub(db_path=":memory:")
    for did in ("a", "b"):
        hub.registry.register(CapabilityManifest(
            device_id=did, device_name=did, manufacturer="t", model="t",
            firmware="1", category=DeviceCategory.ACTUATOR,
            tags=["alarm", "emergency"], sensors=[], events=[],
            actuators=[ActuatorSpec(id="alarm", type="alarm", description="")],
            emergency_capable=True, cert_tier=CertTier.STANDARD))
    return hub


class _OkExecutor:
    async def execute(self, action, urgency):
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True)


def test_progress_cb_failures_start_at_zero():
    assert _hub_with_actuators().progress_cb_failures == 0


def test_failing_callback_is_counted_not_hidden():
    hub = _hub_with_actuators()
    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    def boom(r): raise RuntimeError("cb bug")
    result = asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _OkExecutor(), progress_cb=boom))
    # execution unaffected...
    assert len(result.results) == 2
    # ...but the failures are visible, not swallowed into nothing
    assert hub.progress_cb_failures == 2


def test_working_callback_leaves_counter_at_zero():
    hub = _hub_with_actuators()
    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    seen = []
    asyncio.new_event_loop().run_until_complete(
        hub.execute_intent(intent, _OkExecutor(), progress_cb=lambda r: seen.append(r)))
    assert hub.progress_cb_failures == 0
    assert len(seen) == 2
