"""
DoSync Universal BLE Adapter Validation

Tests the manifest-driven logic of the BLE adapter — the parts that don't need a
real Bluetooth radio: manifest construction, action→characteristic mapping
resolution, hex payload validation, and clear error reporting for misconfigured
devices.

TESTING PHILOSOPHY: the actual GATT write needs a Bluetooth adapter and a real
device, which no CI host has. So we test everything UP TO the radio: that the
adapter reads the right characteristic and bytes from the manifest, validates
them, and fails clearly when the mapping is wrong. The live write against a real
BLE device is the operator's hands-on test on the Pi, not a unit test — same
split as the multi-hub monitor (pure logic here, real hardware there).

Run: python3 tests/test_ble_adapter.py
"""

import sys, os, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.adapters.ble import BLEAdapter, ble_manifest
from dosync.models import DeviceAction, Urgency


def _run(coro):
    return asyncio.run(coro)


def _action(device_id, action_name, cfg):
    return DeviceAction(device_id=device_id, action=action_name,
                        params={"adapter_config": cfg})


SAMPLE_ACTIONS = {
    "turn_on":  {"char": "0000fff1-0000-1000-8000-00805f9b34fb", "write": "0F0D0300"},
    "turn_off": {"char": "0000fff1-0000-1000-8000-00805f9b34fb", "write": "0F0D0400"},
}


# ── Manifest construction ──────────────────────────────────────────────────────

def test_manifest_declares_ble_adapter():
    m = ble_manifest("ble-lamp-01", "BLE Lamp", "AA:BB:CC:DD:EE:FF", SAMPLE_ACTIONS)
    assert m.adapter == "ble"
    assert m.adapter_config["address"] == "AA:BB:CC:DD:EE:FF"


def test_manifest_derives_actuators_from_actions():
    """The actuator list must reflect exactly the mapped actions — so the
    resolver only ever sends actions this device actually supports."""
    m = ble_manifest("ble-lamp-01", "BLE Lamp", "AA:BB:CC:DD:EE:FF", SAMPLE_ACTIONS)
    actuator_types = sorted(a.type for a in m.actuators)
    assert actuator_types == ["turn_off", "turn_on"]


def test_manifest_carries_characteristic_mapping():
    m = ble_manifest("ble-lamp-01", "BLE Lamp", "AA:BB:CC:DD:EE:FF", SAMPLE_ACTIONS)
    mapping = m.adapter_config["actions"]["turn_on"]
    assert mapping["char"] == "0000fff1-0000-1000-8000-00805f9b34fb"
    assert mapping["write"] == "0F0D0300"


def test_manifest_arbitrary_actions_supported():
    """Universal: the adapter is not limited to on/off — any action name maps to
    a characteristic. An oven, a lock, a custom actuator all work the same way."""
    oven_actions = {
        "preheat":         {"char": "0000ab01-0000-1000-8000-00805f9b34fb", "write": "01"},
        "set_temperature": {"char": "0000ab02-0000-1000-8000-00805f9b34fb", "write": "00C8"},
        "stop":            {"char": "0000ab01-0000-1000-8000-00805f9b34fb", "write": "00"},
    }
    m = ble_manifest("ble-oven-01", "BLE Oven", "11:22:33:44:55:66", oven_actions)
    assert sorted(a.type for a in m.actuators) == ["preheat", "set_temperature", "stop"]


# ── Adapter identity ───────────────────────────────────────────────────────────

def test_adapter_name_is_ble():
    assert BLEAdapter().adapter_name == "ble"


# ── Error handling (no radio needed — these fail before any GATT call) ─────────

def test_missing_address_errors_clearly():
    adapter = BLEAdapter()
    r = _run(adapter.execute(_action("x", "turn_on", {"actions": SAMPLE_ACTIONS}), Urgency.INFO))
    assert r.success is False
    assert "address" in r.error.lower()


def test_unmapped_action_errors_clearly():
    adapter = BLEAdapter()
    cfg = {"address": "AA:BB:CC:DD:EE:FF", "actions": SAMPLE_ACTIONS}
    r = _run(adapter.execute(_action("x", "explode", cfg), Urgency.INFO))
    assert r.success is False
    assert "no mapping" in r.error.lower()


def test_incomplete_mapping_errors_clearly():
    """A mapping missing 'char' or 'write' must be rejected before any radio call."""
    adapter = BLEAdapter()
    cfg = {"address": "AA:BB:CC:DD:EE:FF", "actions": {"turn_on": {"char": "abc"}}}  # no write
    r = _run(adapter.execute(_action("x", "turn_on", cfg), Urgency.INFO))
    assert r.success is False
    assert "char" in r.error.lower() or "write" in r.error.lower()


def test_invalid_hex_payload_errors_clearly():
    """A 'write' value that isn't valid hex must be caught, not sent to the radio."""
    adapter = BLEAdapter()
    cfg = {"address": "AA:BB:CC:DD:EE:FF",
           "actions": {"turn_on": {"char": "abc", "write": "ZZZZ"}}}
    r = _run(adapter.execute(_action("x", "turn_on", cfg), Urgency.INFO))
    assert r.success is False
    assert "hex" in r.error.lower()


def test_config_resolved_from_hub_registry():
    """When config isn't in params, the adapter reads it from the hub registry —
    the same pattern as WiZAdapter. We fake a minimal hub."""
    m = ble_manifest("ble-lamp-01", "BLE Lamp", "AA:BB:CC:DD:EE:FF", SAMPLE_ACTIONS)

    class FakeRegistry:
        def get(self, device_id):
            return m if device_id == "ble-lamp-01" else None

    class FakeHub:
        registry = FakeRegistry()

    adapter = BLEAdapter(hub=FakeHub())
    # action.params has NO adapter_config — must be resolved from the registry
    action = DeviceAction(device_id="ble-lamp-01", action="explode", params={})
    r = _run(adapter.execute(action, Urgency.INFO))
    # 'explode' is unmapped, but the point is the adapter FOUND the config
    # (otherwise it would complain about missing address, not missing mapping)
    assert r.success is False
    assert "no mapping" in r.error.lower()


# ── Runner ────────────────────────────────────────────────────────────────────

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
    print(f"\n{passed}/{passed+failed} BLE adapter tests passed.")
    sys.exit(1 if failed else 0)
