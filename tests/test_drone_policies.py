"""
DoSync — Drone safety policy tests (GeofencePolicy + ManualControlActivePolicy).

Both policies are pure logic: they receive an intent and a plan (and the manual-
control one consults the hub for operation state), and return a decision. Tested
entirely offline — no drone, no socket — by injecting plans with coordinates
inside/outside the perimeter and operations in various states.

Run: PYTHONPATH=. python3 tests/test_drone_policies.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.policies import (
    GeofencePolicy, ManualControlActivePolicy, PolicyDecision,
)
from dosync.models import Intent, IntentClass, ActionPlan, DeviceAction, Urgency


# Geofence centered on Córdoba, Argentina. Radius 1000m, ceiling 120m
# (120m / 400ft is the common legal VLOS ceiling in many jurisdictions).
CENTER_LAT = -31.4201
CENTER_LON = -64.1888
RADIUS_M = 1000.0
CEILING_M = 120.0

# A point ~300m from center (inside) and ~5km away (outside).
INSIDE_LAT, INSIDE_LON = -31.4225, -64.1888      # ~270m south
OUTSIDE_LAT, OUTSIDE_LON = -31.4650, -64.1888    # ~5km south


def _intent(urgency=Urgency.INFO):
    return Intent(intent=IntentClass.NOTIFY, context={}, urgency=urgency)


def _plan(actions, urgency=Urgency.INFO):
    return ActionPlan(intent_id="test-int", actions=actions, urgency=urgency)


def _go_to(device_id, lat, lon, alt=None):
    params = {"lat": lat, "lon": lon}
    if alt is not None:
        params["alt"] = alt
    return DeviceAction(device_id=device_id, action="go_to", params=params)


# ── GeofencePolicy ────────────────────────────────────────────────────────────

def _geofence():
    return GeofencePolicy(CENTER_LAT, CENTER_LON, RADIUS_M, max_altitude_m=CEILING_M)


def test_geofence_allows_inside():
    p = _geofence()
    plan = _plan([_go_to("drone-01", INSIDE_LAT, INSIDE_LON, alt=50)])
    assert p.evaluate(_intent(), plan) is None  # abstain = allowed


def test_geofence_blocks_outside():
    p = _geofence()
    plan = _plan([_go_to("drone-01", OUTSIDE_LAT, OUTSIDE_LON, alt=50)])
    result = p.evaluate(_intent(), plan)
    assert result is not None
    assert result.decision == PolicyDecision.BLOCK
    assert "geofence" in result.reason.lower() or "outside" in result.reason.lower()


def test_geofence_blocks_altitude_exceeded():
    p = _geofence()
    # Inside horizontally, but above the ceiling.
    plan = _plan([_go_to("drone-01", INSIDE_LAT, INSIDE_LON, alt=200)])
    result = p.evaluate(_intent(), plan)
    assert result is not None
    assert result.decision == PolicyDecision.BLOCK
    assert "altitude" in result.reason.lower() or "ceiling" in result.reason.lower()


def test_geofence_allows_altitude_within_ceiling():
    p = _geofence()
    plan = _plan([_go_to("drone-01", INSIDE_LAT, INSIDE_LON, alt=100)])
    assert p.evaluate(_intent(), plan) is None


def test_geofence_ignores_non_goto_actions():
    p = _geofence()
    # take_off / land / return_home / loiter carry no destination — not checked.
    plan = _plan([
        DeviceAction(device_id="drone-01", action="take_off", params={"altitude": 10}),
        DeviceAction(device_id="drone-01", action="return_home", params={}),
        DeviceAction(device_id="drone-01", action="land", params={}),
    ])
    assert p.evaluate(_intent(), plan) is None


def test_geofence_goto_without_coords_abstains():
    p = _geofence()
    # Malformed go_to (no lat/lon) — the adapter rejects it; geofence abstains.
    plan = _plan([DeviceAction(device_id="drone-01", action="go_to", params={})])
    assert p.evaluate(_intent(), plan) is None


def test_geofence_no_ceiling_allows_any_altitude():
    p = GeofencePolicy(CENTER_LAT, CENTER_LON, RADIUS_M, max_altitude_m=None)
    plan = _plan([_go_to("drone-01", INSIDE_LAT, INSIDE_LON, alt=5000)])
    assert p.evaluate(_intent(), plan) is None


def test_geofence_device_scoping():
    # A geofence that applies only to drone-01; drone-02 is not governed by it.
    p = GeofencePolicy(CENTER_LAT, CENTER_LON, RADIUS_M,
                       applies_to_devices=["drone-01"])
    plan = _plan([_go_to("drone-02", OUTSIDE_LAT, OUTSIDE_LON)])
    assert p.evaluate(_intent(), plan) is None  # drone-02 not governed
    plan2 = _plan([_go_to("drone-01", OUTSIDE_LAT, OUTSIDE_LON)])
    assert p.evaluate(_intent(), plan2).decision == PolicyDecision.BLOCK


def test_geofence_absolute_not_bypassed_by_emergency():
    p = _geofence()
    assert p.bypass_on_emergency is False


def test_geofence_priority_is_early():
    p = _geofence()
    assert p.priority <= 20  # evaluated before conveniences


def test_geofence_haversine_accuracy():
    # Sanity: a known ~111km per degree of latitude. 0.01 deg ~ 1.11 km.
    d = GeofencePolicy._haversine_m(-31.42, -64.18, -31.43, -64.18)
    assert 1000 < d < 1200, f"expected ~1.1km, got {d:.0f}m"


# ── ManualControlActivePolicy ─────────────────────────────────────────────────

class FakeDB:
    def __init__(self, active_ops):
        self._active = active_ops
    def get_active_operations(self):
        return self._active


class FakeHub:
    def __init__(self, active_ops):
        self.db = FakeDB(active_ops)


def test_manual_control_blocks_interrupted_device():
    hub = FakeHub([{"device_id": "drone-01", "state": "interrupted"}])
    p = ManualControlActivePolicy(hub)
    plan = _plan([DeviceAction(device_id="drone-01", action="go_to",
                               params={"lat": INSIDE_LAT, "lon": INSIDE_LON})])
    result = p.evaluate(_intent(), plan)
    assert result is not None
    assert result.decision == PolicyDecision.BLOCK
    assert "manual" in result.reason.lower() or "control" in result.reason.lower()


def test_manual_control_allows_when_no_interruption():
    hub = FakeHub([{"device_id": "drone-01", "state": "in_progress"}])
    p = ManualControlActivePolicy(hub)
    plan = _plan([DeviceAction(device_id="drone-01", action="go_to",
                               params={"lat": INSIDE_LAT, "lon": INSIDE_LON})])
    assert p.evaluate(_intent(), plan) is None


def test_manual_control_allows_when_no_active_ops():
    hub = FakeHub([])
    p = ManualControlActivePolicy(hub)
    plan = _plan([DeviceAction(device_id="drone-01", action="land", params={})])
    assert p.evaluate(_intent(), plan) is None


def test_manual_control_only_blocks_affected_device():
    # drone-01 is under manual control; a plan targeting drone-02 is unaffected.
    hub = FakeHub([{"device_id": "drone-01", "state": "interrupted"}])
    p = ManualControlActivePolicy(hub)
    plan = _plan([DeviceAction(device_id="drone-02", action="land", params={})])
    assert p.evaluate(_intent(), plan) is None


def test_manual_control_blocks_mixed_plan():
    # Plan touches both drones; drone-01 is under manual control → block.
    hub = FakeHub([{"device_id": "drone-01", "state": "interrupted"}])
    p = ManualControlActivePolicy(hub)
    plan = _plan([
        DeviceAction(device_id="drone-01", action="go_to",
                     params={"lat": INSIDE_LAT, "lon": INSIDE_LON}),
        DeviceAction(device_id="drone-02", action="land", params={}),
    ])
    result = p.evaluate(_intent(), plan)
    assert result is not None
    assert result.decision == PolicyDecision.BLOCK
    assert "drone-01" in result.reason


def test_manual_control_absolute_not_bypassed_by_emergency():
    hub = FakeHub([])
    p = ManualControlActivePolicy(hub)
    assert p.bypass_on_emergency is False


def test_manual_control_handles_db_error_gracefully():
    class BrokenHub:
        class db:
            @staticmethod
            def get_active_operations():
                raise RuntimeError("db down")
    p = ManualControlActivePolicy(BrokenHub())
    plan = _plan([DeviceAction(device_id="drone-01", action="land", params={})])
    # A DB error must not crash the policy engine — abstain (allow) safely.
    assert p.evaluate(_intent(), plan) is None


def test_manual_control_priority_is_early():
    hub = FakeHub([])
    p = ManualControlActivePolicy(hub)
    assert p.priority <= 10


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} drone policy tests passed.")
    sys.exit(1 if failed else 0)
