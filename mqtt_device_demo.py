#!/usr/bin/env python3
"""
DoSync MQTT Device Demo
=======================
Simulates an MQTT device that registers with the hub, fires events,
and responds to commands. Use this to test the MQTTAdapter without hardware.

Usage:
  # Start Mosquitto first:
  sudo systemctl start mosquitto

  # Terminal 1 — start the hub with MQTT enabled:
  DOSYNC_MQTT_BROKER=localhost uvicorn server:app --port 47200

  # Terminal 2 — run this demo device:
  python3 mqtt_device_demo.py

  # Terminal 3 — watch MQTT traffic:
  mosquitto_sub -t 'dosync/#' -v

What it does:
  1. Registers as 'mqtt-demo-01' with the hub
  2. Every 10s fires a motion_detected event
  3. Listens for commands and prints them to stdout
  4. Ctrl+C to stop

Environment variables:
  DOSYNC_MQTT_BROKER   localhost
  DOSYNC_MQTT_PORT     1883
  DOSYNC_MQTT_PREFIX   dosync
"""

import json
import os
import signal
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

BROKER  = os.environ.get("DOSYNC_MQTT_BROKER", "localhost")
PORT    = int(os.environ.get("DOSYNC_MQTT_PORT", "1883"))
PREFIX  = os.environ.get("DOSYNC_MQTT_PREFIX", "dosync")
DEVICE  = os.environ.get("DOSYNC_DEMO_DEVICE_ID", "mqtt-demo-01")

MANIFEST = {
    "device_id":   DEVICE,
    "device_name": "MQTT Demo Device",
    "manufacturer": "DoSync Demo",
    "model":       "SimNode-v1",
    "firmware":    "1.0.0",
    "category":    "hybrid",
    "tags":        ["light", "sensor", "motion", "demo", "mqtt"],
    "capabilities": {
        "sensors": [
            {"id": "motion", "type": "motion",      "description": "PIR motion sensor"},
            {"id": "temp",   "type": "temperature", "description": "Simulated temperature", "unit": "celsius"},
        ],
        "actuators": [
            {"id": "turn_on",  "type": "turn_on",  "description": "Turn on demo light"},
            {"id": "turn_off", "type": "turn_off", "description": "Turn off demo light"},
            {"id": "notify",   "type": "notify",   "description": "Print notification"},
        ],
        "events": [
            {"id": "motion_detected", "severity": "alert",  "description": "Motion detected"},
            {"id": "temp_anomaly",    "severity": "warning", "description": "Temperature out of range"},
        ],
        "context_signals": [],
    },
    "emergency_capable": False,
    "dosync_version": "0.1",
}

_running   = True
_light_on  = False
_msg_count = 0


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[{DEVICE}] Connected to broker {BROKER}:{PORT}")
        # Subscribe to commands and ack
        client.subscribe(f"{PREFIX}/devices/{DEVICE}/commands")
        client.subscribe(f"{PREFIX}/devices/{DEVICE}/ack")
        client.subscribe(f"{PREFIX}/hub/status")
        # Publish registration (retained — hub picks it up on reconnect too)
        client.publish(
            f"{PREFIX}/devices/{DEVICE}/register",
            json.dumps(MANIFEST),
            qos=1,
            retain=True,
        )
        print(f"[{DEVICE}] Registration sent")
    else:
        print(f"[{DEVICE}] Connection refused: {reason_code}")


def on_message(client, userdata, msg):
    global _light_on, _msg_count
    _msg_count += 1
    topic = msg.topic
    try:
        data = json.loads(msg.payload)
    except Exception:
        data = msg.payload.decode(errors="replace")

    if f"/{DEVICE}/commands" in topic:
        action  = data.get("action", "?")
        params  = data.get("params", {})
        urgency = data.get("urgency", "info")

        if action == "turn_on":
            _light_on = True
            print(f"[{DEVICE}] ← COMMAND turn_on | urgency={urgency} | brightness={params.get('brightness', 100)}%")
        elif action == "turn_off":
            _light_on = False
            print(f"[{DEVICE}] ← COMMAND turn_off | urgency={urgency}")
        elif action == "notify":
            print(f"[{DEVICE}] ← NOTIFY: {params.get('message', '(no message)')}")
        else:
            print(f"[{DEVICE}] ← COMMAND {action} | params={params}")

        # Publish status after command
        client.publish(
            f"{PREFIX}/devices/{DEVICE}/status",
            json.dumps({"light_on": _light_on, "timestamp": time.time()}),
            qos=0,
            retain=True,
        )

    elif f"/{DEVICE}/ack" in topic:
        print(f"[{DEVICE}] ← ACK from hub: registered successfully")

    elif "/hub/status" in topic:
        status = data.get("status", "?")
        print(f"[{DEVICE}] Hub status: {status}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    print(f"[{DEVICE}] Disconnected (reason_code={reason_code})")


def signal_handler(sig, frame):
    global _running
    print(f"\n[{DEVICE}] Stopping...")
    _running = False


def main():
    global _running

    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DEVICE}-demo")
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    # LWT — device goes offline
    client.will_set(
        f"{PREFIX}/devices/{DEVICE}/events",
        json.dumps({"event_id": "device_offline", "severity": "warning", "data": {}}),
        qos=1,
    )

    print(f"[{DEVICE}] Connecting to {BROKER}:{PORT}...")
    try:
        client.connect(BROKER, PORT, keepalive=60)
    except Exception as exc:
        print(f"[{DEVICE}] Could not connect to broker: {exc}")
        print(f"[{DEVICE}] Is Mosquitto running? Try: sudo systemctl start mosquitto")
        sys.exit(1)

    client.loop_start()

    # Main loop — fire events periodically
    last_event = 0
    event_interval = 15  # seconds between motion events

    print(f"[{DEVICE}] Running. Firing motion events every {event_interval}s. Ctrl+C to stop.")
    print(f"[{DEVICE}] Watch traffic: mosquitto_sub -t '{PREFIX}/#' -v")
    print()

    while _running:
        now = time.time()

        if now - last_event >= event_interval:
            # Fire a motion_detected event
            event = {
                "event_id":  "motion_detected",
                "severity":  "alert",
                "data": {
                    "zone":        "entrance",
                    "confidence":  0.95,
                    "simulated":   True,
                },
                "timestamp": now,
            }
            client.publish(
                f"{PREFIX}/devices/{DEVICE}/events",
                json.dumps(event),
                qos=1,
            )
            print(f"[{DEVICE}] → EVENT motion_detected (zone=entrance)")
            last_event = now

        time.sleep(0.5)

    client.loop_stop()
    client.disconnect()
    print(f"[{DEVICE}] Stopped. Received {_msg_count} commands.")


if __name__ == "__main__":
    main()
