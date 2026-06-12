# DoSync Protocol

> The semantic layer between AI agents and physical devices.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.1-green.svg)](spec/DoSync-SPEC-v0.1.md)
[![Version](https://img.shields.io/badge/hub-v0.3.0-blue.svg)](server.py)
[![CI](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml)
[![Certification](https://img.shields.io/badge/certification-32%2F32%20Standard-orange.svg)](certify.py)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](dosync/mcp_server.py)

---

## The problem

Today's smart home protocols speak the language of commands. AI speaks the language of goals.

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
| Certification CLI v0.3 — 32/32 Standard tests | ✅ |
| Philips WiZ adapter (UDP local) | ✅ |
| Home Assistant bridge (10 domains) | ✅ |
| Native MCP server (Claude, ChatGPT, any LLM) | ✅ |
| GPIO adapter — Raspberry Pi 5 (PIR + DHT22) | ✅ |
| SMS notifications via Twilio | ✅ |
| MQTT transport adapter (Mosquitto) | ✅ |
| External Resolver Protocol (HTTP wire format) | ✅ |
| SQLite persistence (survives restarts) | ✅ |
| CI pipeline (GitHub Actions) | ✅ |

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
| **Standard** | 32 | Protocol conformance, events, health, version headers |
| **Emergency** | 35 | Emergency override, policy engine, audit log integrity |

---

## Implementations

| Language | Location | Author | Certification |
|---|---|---|---|
| Python (reference) | `server.py` | this project | 32/32 Standard ✅ |
| Node.js (companion) | [giulianireg-spec/dosync-node](https://github.com/giulianireg-spec/dosync-node) | this project | 32/32 Standard ✅ |

The Node.js implementation is a **companion** port that validates the protocol
is implementable in a second language against the same certification suite —
both share the same author. A genuinely **independent** implementation
(different author or organization) is a tracked milestone for v1.0: a protocol
needs multiple independent implementations to become a standard. See the
[roadmap](ROADMAP.md).

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

*DoSync Protocol v0.3.0 · © 2026 Rodrigo Giuliani · giulianireg@gmail.com*
