"""Device health (2026-07-14): hub-owned passive reachability + execution stats.

Regression context: health logic lived in StateAwareResolver, but production runs
ExternalResolver (no mark_unreachable) and record_execution was never called — so
BOTH health systems were silently empty in production. These tests pin that the
hub owns health and the execution chokepoint populates both.
"""
import time
import pytest

from dosync.hub import DeviceHealth, DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           DeviceAction, Intent, IntentClass, Urgency)


def test_mark_reachable_and_unreachable():
    hub = DoSyncHub(db_path=":memory:")
    h = hub.health
    h.mark_reachable("d1")
    assert h.is_unreachable("d1") is False
    snap = h.snapshot("d1")
    assert snap["reachable"] is True and snap["last_seen"] is not None

    h.mark_unreachable("d2", ttl_seconds=100)
    assert h.is_unreachable("d2") is True
    snap = h.snapshot("d2")
    assert snap["reachable"] is False and snap["unreachable_since"] is not None


def test_unreachable_ttl_expires():
    hub = DoSyncHub(db_path=":memory:")
    hub.health.mark_unreachable("d3", ttl_seconds=0)
    time.sleep(0.01)
    assert hub.health.is_unreachable("d3") is False   # TTL lapsed -> unknown


def test_snapshot_never_asserts_powered_off():
    hub = DoSyncHub(db_path=":memory:")
    hub.health.mark_unreachable("d4", ttl_seconds=100)
    note = hub.health.snapshot("d4")["note"].lower()
    assert "powered off" in note and "network" in note   # both possibilities, no assertion
    # a device never interacted with is unknown, not offline
    unknown = hub.health.snapshot("never-seen")
    assert unknown["reachable"] is None


def test_health_persists_across_reload(tmp_path):
    db = str(tmp_path / "h.db")
    hub = DoSyncHub(db_path=db)
    hub.health.mark_unreachable("d5", ttl_seconds=9999)
    hub2 = DoSyncHub(db_path=db)   # fresh hub, same db
    assert hub2.health.is_unreachable("d5") is True


@pytest.mark.asyncio
async def test_execution_chokepoint_populates_both_health_systems():
    """The _TimedExecutor wrapper must feed BOTH db.device_health (stats) and
    hub.health (reachability) on a real execution — the wiring that was missing."""
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id="lamp-h", device_name="L", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light", "emergency"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id="p", type="turn_on", description="on")],
        emergency_capable=True, cert_tier="emergency"))

    class OKExecutor:
        async def execute(self, action, urgency):
            from dosync.models import ActionResult
            return ActionResult(device_id=action.device_id, action=action.action, success=True)

    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    await hub.execute_intent(intent, OKExecutor())

    # execution stats populated
    stats = hub.db.get_device_health("lamp-h")
    assert stats["total"] >= 1 and stats["success"] >= 1
    # reachability populated
    assert hub.health.snapshot("lamp-h")["reachable"] is True
