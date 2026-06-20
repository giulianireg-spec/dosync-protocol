"""
DoSync — MAVLink adapter tests (command channel, Step 1).

Tests the command channel without a real vehicle: simulated-mode behavior (the
degradation path when pymavlink is absent), action validation, config resolution,
and the manifest helper. The live test against ArduPilot SITL is run separately on
a host with pymavlink and the simulator (see test_mavlink_sitl_live below, skipped
unless DOSYNC_SITL_LIVE=1).

Run: python3 tests/test_mavlink_adapter.py
"""

import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.adapters.mavlink import (
    MAVLinkAdapter, mavlink_manifest, SUPPORTED_ACTIONS, _MAVLINK_AVAILABLE,
)
from dosync.models import DeviceAction, Urgency


def _run(coro):
    return asyncio.run(coro)


# ── Action validation ────────────────────────────────────────────────────────

def test_unsupported_action_rejected():
    a = MAVLinkAdapter()
    action = DeviceAction(device_id="drone-01", action="teleport",
                          params={"adapter_config": {"connection": "udp:127.0.0.1:14550"}})
    res = _run(a.execute(action, Urgency.INFO))
    assert not res.success
    assert "no mapping" in res.error


def test_all_five_actions_supported():
    assert set(SUPPORTED_ACTIONS) == {"take_off", "go_to", "land", "return_home", "loiter"}


def test_missing_connection_rejected():
    a = MAVLinkAdapter()
    action = DeviceAction(device_id="drone-01", action="land", params={})
    res = _run(a.execute(action, Urgency.INFO))
    assert not res.success
    assert "connection" in res.error


# ── Simulated mode (degradation when pymavlink absent) ───────────────────────
# We force simulated mode by monkeypatching the module flag, so this path is
# covered on every host regardless of whether pymavlink is installed.

def test_simulated_mode_take_off():
    import dosync.adapters.mavlink as m
    saved = m._MAVLINK_AVAILABLE
    m._MAVLINK_AVAILABLE = False
    try:
        a = MAVLinkAdapter()
        action = DeviceAction(
            device_id="drone-01", action="take_off",
            params={"altitude": 15,
                    "adapter_config": {"connection": "udp:127.0.0.1:14550"}})
        res = _run(a.execute(action, Urgency.INFO))
        assert res.success
        assert res.response["status"] == "simulated"
        assert res.response["command"] == "take_off"
    finally:
        m._MAVLINK_AVAILABLE = saved


def test_simulated_mode_all_actions():
    import dosync.adapters.mavlink as m
    saved = m._MAVLINK_AVAILABLE
    m._MAVLINK_AVAILABLE = False
    try:
        a = MAVLinkAdapter()
        cfg = {"connection": "udp:127.0.0.1:14550"}
        for act in SUPPORTED_ACTIONS:
            params = {"adapter_config": cfg}
            if act == "go_to":
                params.update({"lat": -31.4, "lon": -64.2, "alt": 20})
            action = DeviceAction(device_id="drone-01", action=act, params=params)
            res = _run(a.execute(action, Urgency.INFO))
            assert res.success, f"{act} should succeed in simulated mode"
            assert res.response["status"] == "simulated"
    finally:
        m._MAVLINK_AVAILABLE = saved


# ── Config resolution ────────────────────────────────────────────────────────

def test_config_from_action_params():
    a = MAVLinkAdapter()
    action = DeviceAction(
        device_id="drone-01", action="land",
        params={"adapter_config": {"connection": "udp:1.2.3.4:14550"}})
    cfg = a._get_config(action)
    assert cfg["connection"] == "udp:1.2.3.4:14550"


def test_config_from_manifest_via_hub():
    class FakeRegistry:
        def get(self, device_id):
            class D:
                adapter_config = {"connection": "udp:5.6.7.8:14550"}
            return D()
    class FakeHub:
        registry = FakeRegistry()
    a = MAVLinkAdapter(hub=FakeHub())
    action = DeviceAction(device_id="drone-01", action="land", params={})
    cfg = a._get_config(action)
    assert cfg["connection"] == "udp:5.6.7.8:14550"


# ── go_to validation ─────────────────────────────────────────────────────────

def test_go_to_requires_coordinates():
    import dosync.adapters.mavlink as m
    saved = m._MAVLINK_AVAILABLE
    # Force real path so the lat/lon validation in _dispatch is reached. We stub
    # the connection so no real socket is opened.
    m._MAVLINK_AVAILABLE = True
    try:
        a = MAVLinkAdapter()
        a._get_connection = lambda conn_str: object()  # stub, never used (lat/lon missing first)
        action = DeviceAction(
            device_id="drone-01", action="go_to",
            params={"adapter_config": {"connection": "udp:127.0.0.1:14550"}})
        res = _run(a.execute(action, Urgency.INFO))
        assert not res.success
        assert "lat" in res.error and "lon" in res.error
    finally:
        m._MAVLINK_AVAILABLE = saved


# ── Manifest helper ──────────────────────────────────────────────────────────

def test_manifest_declares_long_running_telemetry_actuators():
    man = mavlink_manifest(
        device_id="drone-01", device_name="Test Drone",
        connection="udp:127.0.0.1:14550", default_alt=12.0)
    assert man.device_id == "drone-01"
    assert man.adapter_config["connection"] == "udp:127.0.0.1:14550"
    assert man.adapter_config["default_alt"] == 12.0
    # Every action is long_running + telemetry-capable (the aerial profile)
    assert len(man.actuators) == len(SUPPORTED_ACTIONS)
    for act in man.actuators:
        assert act.execution_model == "long_running"
        assert act.emits_telemetry is True
    action_types = {a.type for a in man.actuators}
    assert action_types == set(SUPPORTED_ACTIONS)


def test_manifest_adapter_name_matches():
    a = MAVLinkAdapter()
    assert a.adapter_name == "mavlink"


# ── Live SITL test (opt-in) ──────────────────────────────────────────────────

def test_mavlink_sitl_live():
    """Live test against a running ArduPilot SITL. Skipped unless DOSYNC_SITL_LIVE=1
    AND pymavlink is available. Sends a real take_off and asserts the vehicle
    accepts it. Run with the simulator up:
        cd ~/ardupilot/ArduCopter && sim_vehicle.py -v ArduCopter --console -w
        DOSYNC_SITL_LIVE=1 python3 tests/test_mavlink_adapter.py
    """
    if os.environ.get("DOSYNC_SITL_LIVE") != "1" or not _MAVLINK_AVAILABLE:
        print("    (skipped — set DOSYNC_SITL_LIVE=1 with pymavlink + SITL running)")
        return
    a = MAVLinkAdapter()
    cfg = {"connection": "udp:127.0.0.1:14550"}
    action = DeviceAction(device_id="drone-01", action="take_off",
                          params={"altitude": 10, "adapter_config": cfg})
    res = _run(a.execute(action, Urgency.INFO))
    assert res.success, f"SITL take_off should be accepted, got: {res.error}"
    assert res.response["dispatch"] == "accepted"
    print("    ✓ LIVE: SITL accepted take_off to 10m")


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
    print(f"\n{passed}/{passed + failed} MAVLink adapter tests passed.")
    sys.exit(1 if failed else 0)
