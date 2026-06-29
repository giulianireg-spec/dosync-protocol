# DoSync Protocol

> The semantic layer between AI agents and physical devices.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.3-green.svg)](spec/DoSync-SPEC-v0.1.md)
[![Version](https://img.shields.io/badge/hub-v0.3.0-blue.svg)](server.py)
[![CI](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml)
[![Certification](https://img.shields.io/badge/certification-33%2F33%20Standard-orange.svg)](certify.py)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](dosync/mcp_server.py)

---

## The problem

Today's IoT protocols speak the language of commands. AI speaks the language of goals.

```python
# Existing protocols
lock.unlock()
light.set_brightness(100)
thermostat.set_temperature(21)

# What an AI actually expresses
"there is an emergency at home"
"nobody is home — save energy"
"good morning"
```

Someone has to translate. Today, that translation is custom code written per-device, per-platform, per-scenario. It breaks when you add a new device. It completely fails in emergencies where milliseconds matter.

**DoSync is the bridge.**

---

## What it does

DoSync is an open protocol (Apache 2.0) that lets AI systems interact with physical devices using **semantic intent** — expressing *what they want to achieve*, not *how to achieve it*.

When the hub receives `"ensure_safety / emergency"`, every registered device figures out its own role automatically based on its declared capabilities — no hardcoded rules, no manual configuration.

---

## Scope and safety boundaries

DoSync coordinates **non-safety-critical systems** — lighting, access, climate, notifications, logging — and produces a tamper-evident record of every action. It is infrastructure for coordination and auditability, not a certified safety system.

DoSync is **not** certified to IEC 61508 / IEC 62304 / ISO 13849 and must not be the sole or primary mechanism for:

- Primary control of medical devices or life-support systems
- Fire suppression, gas detection, or emergency shutdown of SIL-rated machinery
- Any function where a failure could cause injury or loss of life

In regulated or industrial environments, DoSync **complements** the certified safety systems already in place — coordinating the peripherals around them and recording what happened — but never replaces them. The certified safety system remains in charge of safety.

See [Protocol Specification §12.3](spec/DoSync-SPEC-v0.1.md) for the full operational boundaries.

---

## Demo

[![DoSync Demo](https://img.shields.io/badge/▶_Watch_Demo-YouTube-red?style=for-the-badge)](https://youtu.be/2czAqoIrd08)

**What you'll see:** Claude AI triggers a physical emergency protocol in real time — 10 Philips WiZ bulbs at full brightness, SMS notification sent, audit log updated. No commands. No rules. No cloud.

---

## How it works

```
User / AI says:  "there is an emergency at home"
                          │
                    DoSync Hub v0.3.0
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   💡 All lights      📱 SMS sent     🚨 Alarm
   at maximum       to family        activated
   (10 WiZ bulbs)   immediately
          │               │               │
          └───────────────┴───────────────┘
                          │
                  Audit log updated
              (SHA-256 tamper-evident)
```

The **Capability-based Resolver** matches the intent against every device's **Capability Manifest** — what it can sense, what it can do, whether it's emergency-capable. No rules to write. Add a new device and it participates automatically.

Benchmark (Raspberry Pi 5, Python 3.11.2):

| Devices | Mean | p99 | Within 500ms limit |
|---|---|---|---|
| 38 (production) | 0.076ms | 0.097ms | ✓ |
| 1000 | 1.336ms | 5.690ms | ✓ |
| 5000 | 9.163ms | 24.541ms | ✓ (20× margin) |

---

## Protocol architecture

| Layer | Name | Role |
|---|---|---|
| 5 | **Intent** | AI expresses semantic goals |
| 4 | **Semantic** | Resolver maps intent → device actions |
| 3 | **Registry** | Devices self-declare capabilities on join |
| 2 | **Secure channel** | mTLS, local PKI — no internet required |
| 1 | **Transport (HAL)** | WiFi · BLE · MQTT · Zigbee · Z-Wave · Thread |

---

## Quick start

### Option A — Docker (no setup required)

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
docker compose up
```

Dashboard live at **http://localhost:47200**. No hardware required.

### Option B — Local Python

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 47200 --reload
```

---

## What's built today

| Component | Status |
|---|---|
| REST API (12+ endpoints) | ✅ |
| WebSocket real-time events | ✅ |
| Web dashboard | ✅ |
| API key authentication + SHA-256 audit log | ✅ |
| Capability-based resolver | ✅ |
| Certification CLI v0.3 — 33/33 Standard tests | ✅ |
| Philips WiZ adapter (UDP local) | ✅ |
| Home Assistant bridge (10 domains) | ✅ |
| Native MCP server (Claude, ChatGPT, any LLM) | ✅ |
| GPIO adapter — Raspberry Pi 5 (PIR + DHT22) | ✅ |
| SMS notifications via Twilio | ✅ |
| MQTT transport adapter (Mosquitto) | ✅ |
| Shelly adapter (HTTP local, Gen1 + Gen2) | ✅ code, not hardware-tested |
| Matter adapter (via HA bridge / python-matter-server) | ✅ code, not hardware-tested |
| External Resolver Protocol (HTTP wire format) | ✅ |
| SQLite persistence (survives restarts) | ✅ |
| CI pipeline (GitHub Actions) | ✅ |
| Multi-hub assisted failover (Phase A — operator-in-the-loop) | ✅ |
| Long-running operations + telemetry reconciliation (state machine) | ✅ |
| Drone / MAVLink adapter — full AI→intent→mission loop in ArduPilot SITL | ✅ software (physical flight pending) |

---

## MQTT transport

DoSync supports MQTT as a Layer 1 transport for devices that can't use HTTP. Requires Mosquitto and proper authentication. See [config/mosquitto-secure.conf](config/mosquitto-secure.conf) for secure setup.

```bash
# Enable MQTT in the hub service
Environment="DOSYNC_MQTT_BROKER=localhost"
Environment="DOSYNC_MQTT_USER=dosync-hub"
Environment="DOSYNC_MQTT_PASSWORD=<password>"
Environment="DOSYNC_MQTT_SECRET=<registration-secret>"
```

---

## Certification

Self-certifiable with the CLI:

```bash
python3 certify.py --host <hub-ip> --port 47200 --tier standard
# Output: dosync-cert-standard-*.json
```

| Tier | Tests | What it validates |
|---|---|---|
| **Basic** | 10 | Connectivity, auth, device manifest |
| **Standard** | 33 | Protocol conformance, events, health, version headers |
| **Emergency** | 36 | Emergency override, policy engine, audit log integrity |

---

## Implementations

| Language | Location | Author | Certification |
|---|---|---|---|
| Python (reference) | `server.py` | this project | 33/33 Standard ✅ |
| Node.js (companion) | [giulianireg-spec/dosync-node](https://github.com/giulianireg-spec/dosync-node) | this project | 33/33 Standard ✅ |

The Node.js implementation is a **companion** port that validates the protocol
is implementable in a second language against the same certification suite —
both share the same author. A genuinely **independent** implementation
(different author or organization) is a tracked milestone for v1.0: a protocol
needs multiple independent implementations to become a standard. See the
[roadmap](ROADMAP.md).

---

## Works with Home Assistant — a layer on top, not a replacement

Home Assistant already solved the hardest problem: talking to thousands of devices, and since 2025 it ships an MCP server so an AI can control them directly. DoSync doesn't reinvent that — it reads devices from HA through a bridge already in the repo and adds **one thing**: it turns a semantic goal (`ensure_safety`, `away_mode`) into a coordinated, **auditable** set of actions across *any* source (HA, WiZ, GPIO, MQTT, BLE).

The honest version: for everyday automation ("porch light when I get home") you **don't need DoSync** — HA's automations and its MCP cover that completely. DoSync earns its place only when **coordination and traceability matter at once** — e.g. a fall-response that unlocks the door, lights the house, and messages family, with a tamper-evident record of exactly what fired and when. Full reasoning: [Home Assistant Already Talks to Your Devices. So What Would DoSync Add?](https://dev.to/giulianiregspec/home-assistant-already-talks-to-your-devices-so-what-would-dosync-add-1iei)

---

## Beyond the home

Nothing in DoSync assumes a house — the same 5-layer stack coordinates physical systems anywhere an AI needs to act: retail cold-chain, hotels, factory peripherals (alongside certified safety systems, never replacing them).

The proof: we took it to the hardest device, an **autonomous drone**. From a single plain-language sentence, an AI model (Claude Haiku, via DoSync's MCP server) fired an `inspect_area` intent and the drone flew the full mission in ArduPilot SITL — every step confirmed by real telemetry. When the AI guessed coordinates 11,000 km away, the supervisor didn't fake success: it waited for a confirmed arrival, none came, and it aborted with a clear diagnosis. **The AI can be wrong; the protocol doesn't have to be.** [Full build log](https://dev.to/giulianiregspec/i-gave-an-ai-one-sentence-a-drone-flew-the-mission-and-when-the-ai-guessed-wrong-the-system-2h3m) · *(validated in SITL; physical-hardware flight is the next step, not a claim made today.)*

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow, including the CI pipeline that runs on every push.

---

## Specification

- [spec/DoSync-SPEC-v0.1.md](spec/DoSync-SPEC-v0.1.md) — full protocol specification
- [spec/RESOLVER-SPEC-v0.3.md](spec/RESOLVER-SPEC-v0.3.md) — resolver interface + external resolver protocol
- [DESIGN-PRINCIPLES.md](DESIGN-PRINCIPLES.md) — architectural decisions and rationale
- [COMPATIBILITY.md](COMPATIBILITY.md) — backward compatibility guarantees

---

## License

Apache 2.0 — free to implement, free to extend, no royalties.

---

*DoSync Protocol v0.3.0 · © 2026 Rodrigo Giuliani · rgiuliani@dosync.dev*
