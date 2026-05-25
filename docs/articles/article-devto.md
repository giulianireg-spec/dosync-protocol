# The Missing Layer Between AI Agents and Physical Systems

*Published on dev.to — May 2026*

---

There's a fundamental mismatch at the heart of every smart home today, and most people building in this space haven't fully articulated what it is.

It's not a hardware problem. The sensors, locks, cameras, and thermostats we have today are genuinely capable. It's not a connectivity problem — Matter, Zigbee, and Z-Wave do a perfectly good job of letting apps talk to devices. The problem is architectural, and it only becomes visible when you try to add AI to the picture.

Let me show you what I mean.

---

## The command gap

Every smart home protocol in existence was designed around a fundamental assumption: **a human decides what to do, an app translates that decision into a command, and a device executes it.**

```
Human decision → App → Command → Device
lock.unlock()
light.set_brightness(100)
thermostat.set_temperature(21)
```

This works perfectly for app-controlled scenarios. Matter is an excellent solution to the problem of letting different brands' apps control different brands' devices. Zigbee and Z-Wave solve real problems in mesh networking and low-power communication.

But AI systems don't think in commands. They think in goals.

When an AI detects a fall, it doesn't know it needs to call `lock.unlock()` and `phone.call("911")` and `alarm.activate("emergency")`. It knows there's an emergency. The translation from "there's an emergency" to the specific sequence of device commands has to be written by a human, device by device, scenario by scenario, in advance.

That translation layer is fragile. It breaks when you add a new device. It fails to anticipate scenarios. And in emergencies — where the system should be most reliable — it's exactly the layer most likely to have a gap.

---

## Why this matters now

For the last decade, this wasn't a real problem. Smart home automation was primarily rule-based: "if motion sensor triggers after 10pm, turn on the hallway light." Rules are commands. The mapping was trivial.

But the emergence of capable AI agents changes the equation entirely. We now have systems that can:

- Detect that an elderly person has fallen from camera footage
- Infer from sensor data that a fridge compressor has failed
- Understand from context that "nobody is home" without a button press
- React to a smoke detector with a phased evacuation response

These are **goal-oriented** events, not command-oriented ones. And every existing protocol forces you to pre-write the translation between "goal" and "command" yourself. There is no standard interface for saying "ensure her safety" and having the home figure out the rest.

This problem isn't limited to the home. Consider a manufacturing line: a temperature sensor detects an abnormal reading on a critical component. The AI system knows there's an anomaly — but it doesn't know whether to stop the line, alert the supervisor, activate cooling, trigger a maintenance request, or all of the above. With existing protocols, an engineer had to anticipate this scenario and write that rule in advance. Miss one edge case and the system does nothing. With a semantic protocol, each device declares its capabilities and the resolver determines the appropriate response based on urgency and context — without any pre-written rules for that specific failure mode.

The same architectural gap exists in hospitals, hotels, smart buildings, and anywhere an AI needs to coordinate physical systems in response to real-world events.

---

## What a semantic protocol actually needs

I've been working on this problem for several months, and the design space is clearer than it looks. A protocol that can bridge AI goals and physical devices needs a few specific things:

### 1. Capability-based device registration

Instead of a device waiting to receive commands, it announces what it can do when it joins the network:

```json
{
  "device_id": "lock-frontdoor-01",
  "tags": ["door-lock", "entrance", "emergency"],
  "actuators": [
    { "type": "unlock", "emergency_capable": true },
    { "type": "lock" }
  ],
  "emergency_capable": true
}
```

This manifest lets the system discover what's available without any pre-configuration. Add a new device and it participates automatically in every relevant scenario.

### 2. Semantic intent, not commands

The AI layer submits an intent — what it wants to achieve — not a specific sequence of commands:

```json
{
  "intent": "ensure_safety",
  "urgency": "emergency",
  "context": {
    "trigger": "fall_detected",
    "location": "bedroom"
  }
}
```

A semantic resolver then matches this intent against the registered device manifests, determines which devices are relevant, and builds an action plan. The AI doesn't need to know what devices exist.

### 3. Emergency override without confirmation

For urgent situations, the system must be able to act immediately. This means `emergency_capable` devices bypass the normal confirmation flow. Every action is logged with a tamper-evident chain — but the action executes without waiting.

### 4. Transport agnosticism

A semantic protocol should be independent of the physical layer. Whether devices communicate over WiFi, Zigbee, Z-Wave, BLE, or Thread should be an implementation detail, not an architectural constraint.

---

## DoSync: a working implementation

I built a reference implementation of these ideas called **DoSync Protocol** (Apache 2.0). It's not a theoretical spec — there's a running hub, a web dashboard, a certification CLI, and hardware adapters.

Here's what happens when the system receives an emergency intent:

```
PIR sensor detects motion → gpio_adapter fires ensure_safety [emergency]
    → Semantic resolver scores all 23 registered devices
        → 10 Philips WiZ bulbs: turn_on at full brightness (WiZ UDP adapter)
        → HA lock: unlock for 5 minutes (Home Assistant adapter)  
        → Alarm: activate emergency pattern (simulated)
        → SMS: sent to emergency contact (Twilio)
        → Audit log: SHA-256 chain updated
All in under 100ms. No rules written. No pre-configuration.
```

The current hardware setup runs autonomously on a Raspberry Pi 5 with a PIR motion sensor and DHT22 temperature sensor. The hub survives reboots via systemd. Any LLM with MCP support (Claude, ChatGPT, etc.) can control the hub through a native MCP server.

---

## What this doesn't solve

I want to be honest about the limitations, because I think intellectual honesty is what makes a protocol worth taking seriously.

**DoSync is a protocol of application layer, not transport.** It runs over HTTP/WebSocket today. It doesn't replace Zigbee or Z-Wave at the radio level — it sits above them and abstracts them.

**The semantic resolver is simple — and I know exactly what it needs.** Today it's a capability matching algorithm that scores devices by tags, urgency, and `emergency_capable` flag. It works well for the scenarios we have, but a production-grade resolver needs to go further.

The current algorithm asks: *which devices have the right tags and capabilities for this intent?* A mature resolver would also ask:

- **Device state awareness** — don't unlock a door that's already unlocked, don't turn on a light that's already at full brightness
- **Competing priorities** — if two intents fire simultaneously (morning routine + motion detected), which takes precedence and how do they compose?
- **Contextual weighting** — a motion sensor in a bedroom at 3am carries different weight than the same sensor at 3pm
- **Learned patterns** — over time, the resolver should know that in *this* home, "save energy" means turning off the living room lights but never the hallway

The resolver interface is already decoupled from the rest of the protocol — you can implement a more sophisticated one without touching the adapter layer or the audit system. The spec defines what a resolver must do, not how. That's intentional. I plan to document this interface formally in v0.2 so that third-party resolver implementations can be dropped in. A production deployment might use a small local LLM as the resolver itself — the protocol is designed to support that path.

**There is no hardware certification process yet.** The CLI generates a signed JSON report, but there's no third-party review. That's the right long-term model — it's just not there yet.

**It's one person's implementation.** A protocol needs multiple independent implementations to become a standard. That's the next milestone.

---

## The broader point

Smart home AI is arriving whether the protocol layer is ready or not. Home Assistant added MCP support in 2025. Claude and ChatGPT can already control devices via tool calls. The question isn't whether AI will be integrated into physical environments — it's whether the integration will be done well or badly.

Done badly: AI systems that call device APIs directly, with hardcoded rules, fragile integrations, no audit trail, and no standard for what "emergency capable" even means.

Done well: a semantic layer where devices declare what they can do, AI expresses what it wants to achieve, and the home figures out the rest — with full auditability and a clear certification model for safety-critical scenarios.

The hardware is ready. The AI is ready. The missing piece is a protocol designed from first principles for this use case.

---

## Try it

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn websockets pywizlight aiohttp

PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 47200 --reload
# Open http://localhost:47200
```

The dashboard shows registered devices, live events, and the audit log in real time. The certification CLI runs 16 tests across three tiers. The MCP server lets any compatible LLM control the hub directly.

Feedback, contributions, and adapter implementations are welcome. The RFC process is open.

**GitHub:** github.com/giulianireg-spec/dosync-protocol
**License:** Apache 2.0
**Contact:** giulianireg@gmail.com

---

*DoSync Protocol v0.1 — building the semantic layer between AI and physical systems.*
