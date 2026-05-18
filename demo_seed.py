#!/usr/bin/env python3
"""
DoSync — Demo Seed
==================
Registers simulated devices in the hub for demo purposes.
Run this after the hub is up:

    python3 demo_seed.py --hub http://localhost:47200
"""
import argparse
import json
import time
import urllib.request
import urllib.error

DEVICES = [
    {
        "device_id": "sim-lock-frontdoor",
        "device_name": "Front Door Lock",
        "manufacturer": "DoSync",
        "model": "simulated-lock",
        "firmware": "1.0.0",
        "category": "actuator",
        "tags": ["door-lock", "entrance", "emergency", "access"],
        "capabilities": {
            "sensors": [{"id": "locked", "type": "boolean", "description": "Lock state", "unit": None, "range": None, "poll_interval_ms": 5000}],
            "actuators": [
                {"id": "unlock", "type": "unlock", "description": "Unlock door", "params_schema": {"duration_seconds": "int"}},
                {"id": "lock", "type": "lock", "description": "Lock door", "params_schema": {}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "emergency",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-alarm-main",
        "device_name": "Main Alarm",
        "manufacturer": "DoSync",
        "model": "simulated-alarm",
        "firmware": "1.0.0",
        "category": "actuator",
        "tags": ["emergency", "alarm", "security"],
        "capabilities": {
            "sensors": [],
            "actuators": [
                {"id": "alarm", "type": "alarm", "description": "Activate alarm", "params_schema": {"pattern": "str"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "emergency",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-light-living-01",
        "device_name": "Living Room Light 1",
        "manufacturer": "DoSync",
        "model": "simulated-light",
        "firmware": "1.0.0",
        "category": "hybrid",
        "tags": ["light", "living", "climate"],
        "capabilities": {
            "sensors": [{"id": "state", "type": "boolean", "description": "On/off", "unit": None, "range": None, "poll_interval_ms": 30000}],
            "actuators": [
                {"id": "turn_on", "type": "turn_on", "description": "Turn on", "params_schema": {}},
                {"id": "turn_off", "type": "turn_off", "description": "Turn off", "params_schema": {}},
                {"id": "set_brightness", "type": "set_brightness", "description": "Set brightness 0-100", "params_schema": {"brightness": "int 0-100"}},
                {"id": "set_color", "type": "set_color", "description": "Set RGB color", "params_schema": {"r": "int", "g": "int", "b": "int"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "standard",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-light-living-02",
        "device_name": "Living Room Light 2",
        "manufacturer": "DoSync",
        "model": "simulated-light",
        "firmware": "1.0.0",
        "category": "hybrid",
        "tags": ["light", "living", "climate"],
        "capabilities": {
            "sensors": [{"id": "state", "type": "boolean", "description": "On/off", "unit": None, "range": None, "poll_interval_ms": 30000}],
            "actuators": [
                {"id": "turn_on", "type": "turn_on", "description": "Turn on", "params_schema": {}},
                {"id": "turn_off", "type": "turn_off", "description": "Turn off", "params_schema": {}},
                {"id": "set_brightness", "type": "set_brightness", "description": "Set brightness 0-100", "params_schema": {"brightness": "int 0-100"}},
                {"id": "set_color", "type": "set_color", "description": "Set RGB color", "params_schema": {"r": "int", "g": "int", "b": "int"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "standard",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-light-bedroom",
        "device_name": "Bedroom Light",
        "manufacturer": "DoSync",
        "model": "simulated-light",
        "firmware": "1.0.0",
        "category": "hybrid",
        "tags": ["light", "bedroom", "climate"],
        "capabilities": {
            "sensors": [{"id": "state", "type": "boolean", "description": "On/off", "unit": None, "range": None, "poll_interval_ms": 30000}],
            "actuators": [
                {"id": "turn_on", "type": "turn_on", "description": "Turn on", "params_schema": {}},
                {"id": "turn_off", "type": "turn_off", "description": "Turn off", "params_schema": {}},
                {"id": "set_brightness", "type": "set_brightness", "description": "Set brightness 0-100", "params_schema": {"brightness": "int 0-100"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "standard",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-thermostat-main",
        "device_name": "Main Thermostat",
        "manufacturer": "DoSync",
        "model": "simulated-thermostat",
        "firmware": "1.0.0",
        "category": "hybrid",
        "tags": ["climate", "thermostat"],
        "capabilities": {
            "sensors": [
                {"id": "temperature", "type": "float", "description": "Current temperature", "unit": "°C", "range": None, "poll_interval_ms": 30000},
            ],
            "actuators": [
                {"id": "set_temperature", "type": "set_temperature", "description": "Set target temperature", "params_schema": {"celsius": "float"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": False,
        "cert_tier": "standard",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-motion-entrance",
        "device_name": "Motion Sensor — Entrance",
        "manufacturer": "DoSync",
        "model": "simulated-pir",
        "firmware": "1.0.0",
        "category": "sensor",
        "tags": ["sensor", "motion", "entrance", "security", "emergency"],
        "capabilities": {
            "sensors": [{"id": "motion", "type": "boolean", "description": "Motion detected", "unit": None, "range": None, "poll_interval_ms": 1000}],
            "actuators": [],
            "events": [{"id": "motion_detected", "description": "Motion detected at entrance", "severity": "info"}],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "standard",
        "adapter": "simulated",
        "adapter_config": {},
    },
    {
        "device_id": "sim-phone-family",
        "device_name": "Family Phone Hub",
        "manufacturer": "DoSync",
        "model": "simulated-phone",
        "firmware": "1.0.0",
        "category": "actuator",
        "tags": ["communication", "phone", "emergency"],
        "capabilities": {
            "sensors": [],
            "actuators": [
                {"id": "call", "type": "call", "description": "Make a call", "params_schema": {"number": "str", "message": "str"}},
                {"id": "notify", "type": "notify", "description": "Send notification", "params_schema": {"message": "str"}},
            ],
            "events": [],
            "context_signals": [],
        },
        "emergency_capable": True,
        "cert_tier": "emergency",
        "adapter": "simulated",
        "adapter_config": {},
    },
]


def register_device(hub_url: str, device: dict) -> bool:
    url = f"{hub_url}/v1/devices/register"
    data = json.dumps(device).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
            print(f"  ✓ {device['device_id']} — {device['device_name']}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ✗ {device['device_id']} — HTTP {e.code}: {body[:100]}")
        return False
    except Exception as e:
        print(f"  ✗ {device['device_id']} — {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="DoSync Demo Seed")
    parser.add_argument("--hub", default="http://localhost:47200", help="Hub URL")
    parser.add_argument("--wait", type=int, default=0, help="Seconds to wait before registering")
    args = parser.parse_args()

    if args.wait:
        print(f"Waiting {args.wait}s for hub to start...")
        time.sleep(args.wait)

    print(f"\nDoSync Demo Seed — registering {len(DEVICES)} simulated devices")
    print(f"Hub: {args.hub}\n")

    ok = 0
    for device in DEVICES:
        if register_device(args.hub, device):
            ok += 1

    print(f"\n{ok}/{len(DEVICES)} devices registered.")
    if ok == len(DEVICES):
        print("✓ Demo ready — open http://localhost:47200")
    else:
        print("⚠ Some devices failed to register.")


if __name__ == "__main__":
    main()
