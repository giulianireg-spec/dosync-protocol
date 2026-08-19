# Make a device speak DoSync — the full sense-and-act loop

> **Who this is for.** You want to build a device that participates in the
> protocol: registers what it can do, acts on what the hub sends, and fires
> goals when it senses something. You will write code and run a broker.
>
> **You need Docker, Python 3, and `paho-mqtt` before step 1.**
>
> **Looking for something else?** To install a hub and see devices, the
> [README](../README.md) takes about five minutes and needs none of the above —
> discovery and adoption happen in the dashboard, without a terminal. This file
> was called `TUTORIAL.md` and sat in the repository root, where its name
> promised the shortest path and its first step asked for Docker.


This tutorial walks you through building a device that **participates** in DoSync:
it registers its capabilities, it **acts** on commands the hub sends it, and when it
**senses** something it fires a semantic goal the hub resolves back into actions —
with a tamper-evident audit trail of the whole thing.

No hub-side code. The device talks **MQTT** (to register and to receive commands) and
**HTTP** (to fire an intent). Both are universal, so the device can be written in any
language — we show ~50 lines of Python here, but an ESP32 in C or a Node service works
the same way.

**Time:** ~20 minutes. **You need:** Docker, Python 3, and `pip install paho-mqtt requests`.

---

## The loop you're building

```
   your device                     DoSync hub
   ───────────                     ──────────
   1. self-register  ──MQTT──────►  capability registry
   2. (senses something)
   3. fire ensure_safety ──HTTP──►  resolver + policy engine
                                        │ builds an ActionPlan
   4. act on command  ◄──MQTT─────  dispatches "turn_on" to you
                                        │ writes the audit log
```

The device never says *"turn on light X."* It says *"there's an emergency"* — and the
hub decides which devices act, including your device, based on the capabilities your
device declared.

---

## Step 1 — Start the hub with an MQTT broker

DoSync's MQTT transport is opt-in. Start a broker and point the hub at it.

```bash
# 1a. an MQTT broker (Mosquitto)
docker run -d --name dosync-mqtt -p 1883:1883 eclipse-mosquitto:2 \
  sh -c 'printf "listener 1883\nallow_anonymous true\n" > /mosquitto/config/mosquitto.conf && exec mosquitto -c /mosquitto/config/mosquitto.conf'

# 1b. the hub, told to use that broker
git clone https://github.com/giulianireg-spec/dosync-protocol && cd dosync-protocol
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt paho-mqtt
DOSYNC_MQTT_BROKER=localhost DOSYNC_AUTH=false \
  PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 47200
```

You should see `MQTTAdapter: connected to localhost:1883` in the hub log. (We set
`DOSYNC_AUTH=false` to keep the tutorial short; in production you'd pass a Bearer token.)

---

## Step 2 — The device: register and listen

Create `my_device.py`. This part connects to MQTT, subscribes to the command topic,
and self-registers by publishing a capability manifest.

```python
import json, time, threading, requests
import paho.mqtt.client as mqtt

HUB_HTTP   = "http://localhost:47200"
BROKER     = "localhost"
DEVICE_ID  = "tutorial-lamp-01"
PREFIX     = "dosync"                       # DOSYNC_MQTT_PREFIX default

CMD_TOPIC  = f"{PREFIX}/devices/{DEVICE_ID}/commands"
REG_TOPIC  = f"{PREFIX}/devices/{DEVICE_ID}/register"

# What this device can do — the hub uses this to decide when to involve it.
MANIFEST = {
    "device_name": "Tutorial Lamp",
    "manufacturer": "DIY",
    "model": "tut-1",
    "firmware": "1.0",
    "category": "light",
    "tags": ["light", "emergency", "living-room"],   # tags drive resolution
    "actuators": [{"id": "power", "type": "turn_on", "description": "turn the lamp on"}],
    "sensors":   [{"id": "pir", "type": "motion", "description": "motion sensor"}],
    "events":    [],
    "emergency_capable": True,                         # may act during emergencies
    "cert_tier": "basic",
}

def on_connect(client, *_):
    client.subscribe(CMD_TOPIC, qos=1)               # ACT: listen for commands
    client.publish(REG_TOPIC, json.dumps(MANIFEST), qos=1)   # self-register
    print(f"[{DEVICE_ID}] connected, registered, listening on {CMD_TOPIC}")

def on_message(client, _u, msg):                     # ACT: a command arrived
    cmd = json.loads(msg.payload)
    if cmd["action"] == "turn_on":
        b = cmd.get("params", {}).get("brightness", 100)
        print(f"💡 [{DEVICE_ID}] ACTING: turn_on @ brightness {b}  (urgency={cmd.get('urgency')})")
    else:
        print(f"[{DEVICE_ID}] got action '{cmd['action']}' {cmd.get('params', {})}")

client = mqtt.Client(client_id=DEVICE_ID)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883, 60)
threading.Thread(target=client.loop_forever, daemon=True).start()
time.sleep(1)   # give registration a moment
```

Run it (`python3 my_device.py` — but keep reading, we add the sense side next). The hub
log will show `MQTTAdapter: auto-registered device 'tutorial-lamp-01'`.

---

## Step 3 — The sense side: fire a goal, not a command

When your device senses something (here, we simulate "motion" by pressing Enter), it
fires a **semantic intent** over HTTP. It does *not* command any specific device.

Append to `my_device.py`:

```python
def sense_and_fire():
    while True:
        input("\n[press Enter to simulate MOTION → fire ensure_safety]\n")
        r = requests.post(f"{HUB_HTTP}/v1/intent/async", json={
            "intent": "ensure_safety",
            "urgency": "emergency",
            "context": {"trigger": "motion_detected", "source": DEVICE_ID},
        })
        print(f"[{DEVICE_ID}] fired ensure_safety → {r.json().get('status')}")

sense_and_fire()
```

Now run the whole thing:

```bash
python3 my_device.py
```

Press Enter. Watch the order of events:

1. Your device fires `ensure_safety [emergency]` over HTTP.
2. The hub resolves it — your lamp matches (it's `emergency_capable`, tagged `emergency`,
   and has a `turn_on` actuator).
3. The hub publishes `turn_on` to `dosync/devices/tutorial-lamp-01/commands`.
4. Your `on_message` prints `💡 ACTING: turn_on @ brightness 100`.

That's the full loop: **your device sensed, expressed a goal, and the hub told it (and
any other relevant device) how to act** — no per-device wiring anywhere.

---

## Step 4 — See the audit trail

Every step is recorded, tamper-evident:

```bash
curl -s http://localhost:47200/v1/audit | python3 -m json.tool | tail -40
```

You'll see the `intent_executed` entry for `ensure_safety`, with the device actions it
produced. This is the point of DoSync: not just that the lamp turned on, but a
verifiable record of *what goal was expressed, what the system decided, and what it did.*

---

## What just happened (and what's normative)

- **Registration, commands, and events travel over any transport.** We used MQTT; the
  reference hub also speaks HTTP/WebSocket directly. The protocol is transport-agnostic
  (see the spec, §3).
- **Your device declared capabilities; the hub did the reasoning.** Add a second device
  with the `emergency` tag and it participates in the next `ensure_safety` automatically —
  no code change.
- **The device expressed a goal, not a command.** That's the whole idea — the semantic
  intent layer between an AI (or a sensor) and the physical devices.

## Next steps

- **Make it real:** replace the `print` in `on_message` with GPIO / an HTTP call to your
  actual hardware, and replace the `input()` with a real sensor read.
- **Add a policy:** the hub's Policy Engine can require confirmation, block actions after
  hours, or guarantee an emergency is device-final. See `spec/CONSISTENCY-MODEL.md`.
- **Certify it:** run `python3 certify.py --host localhost --port 47200 --tier standard`
  to check your setup against the protocol's conformance suite.

**Questions or a use case you're not sure about?** Open an issue on the repo — describe
what you're trying to coordinate and we'll tell you honestly whether DoSync fits.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
