# DoSync Protocol

> The semantic protocol for AI agents and physical systems.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.1-green.svg)](spec/DOSYNC-SPEC-v0.1.md)
[![Certification](https://img.shields.io/badge/certification-Basic%20%7C%20Standard%20%7C%20Emergency-orange.svg)](certify.py)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](dosync/mcp_server.py)
[![Website](https://img.shields.io/badge/website-dosync.dev-black.svg)](https://dosync.dev)

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

DoSync is an open communication protocol (Apache 2.0) that lets AI systems interact with physical devices using **semantic intent** — expressing *what they want to achieve*, not *how to achieve it*.

When the hub receives `"ensure_safety / emergency"`, every registered device figures out its own role automatically based on its declared capabilities — no hardcoded rules, no manual configuration.

---

## Demo

[![DoSync Demo](https://img.shields.io/badge/▶_Watch_Demo-YouTube-red?style=for-the-badge)](https://youtu.be/2czAqoIrd08)

**What you'll see:** A natural language conversation with Claude AI triggers a physical emergency protocol in real time — 10 Philips WiZ bulbs at full brightness, SMS notification sent, audit log updated. No commands. No rules. No cloud.

---

## How it works

```
User / AI says:  "there is an emergency at home"
                          │
                    DoSync Hub
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

The **Semantic Resolver** matches the intent against every device's **Capability Manifest** — what it can sense, what it can do, whether it's emergency-capable. No rules to write. Add a new device and it participates automatically.

---

## Protocol architecture

![DoSync 5-layer architecture](docs/architecture.svg)

| Layer | Name | Role |
|-------|------|------|
| 5 | **Intent** | AI expresses semantic goals |
| 4 | **Semantic** | Resolver maps intent → device actions |
| 3 | **Registry** | Devices self-declare capabilities on join |
| 2 | **Secure channel** | mTLS, local PKI — no internet required |
| 1 | **Transport (HAL)** | WiFi · BLE · Zigbee · Z-Wave · Thread |

---

## Quick start

### Option A — Docker (recommended, no setup required)

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
docker compose up
```

That's it. The hub starts, 8 simulated devices register automatically, and the dashboard is live at **http://localhost:47200**. No hardware required.

### Option B — Local Python

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start the hub
PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 47200 --reload

# In a second terminal — register simulated devices
python3 demo_seed.py

# Open the dashboard
open http://localhost:47200

# Run the full demo (7 scenarios)
PYTHONPATH=. python3 examples/demo_full.py

# Run certification suite
python3 certify.py --host localhost --port 47200 --tier emergency
```

---

## Getting the most out of DoSync

**The single most important configuration step** after running the demo is tagging your devices correctly. The resolver matches intents to devices using semantic tags — missing or incorrect tags are the most common reason why devices don't respond to intents.

> 📖 **[DEPLOYMENT-TAGS-GUIDE.md](docs/DEPLOYMENT-TAGS-GUIDE.md)** — how to configure tags for production deployments, with examples for every device type and intent class. Read this before connecting real hardware.

**Registering domain-specific intent classes:**

DoSync ships with 5 universal intent classes (`ensure_safety`, `alert_anomaly`, `control_access`, `report_status`, `notify`). For residential, healthcare, industrial, or hospitality deployments, register your own:

```bash
# Get your API token: python3 manage.py keys list
# On first run, the hub prints the token automatically
POST /v1/intent-classes   (Authorization: Bearer <your-token>)
{
  "name": "morning_routine",
  "urgency": "info",
  "resolution_tags": ["light", "blinds", "climate"],
  "resolution_actuators": ["set_brightness", "set_position", "set_temperature"],
  "description": "Prepare the space for the day",
  "domain": "residential"
}
```

> 🔑 **Token:** the hub prints your API token on first run. Retrieve it anytime with `python3 manage.py keys list`.

> 📖 **[INTENT-CLASSES-GUIDE.md](docs/INTENT-CLASSES-GUIDE.md)** — naming conventions, urgency guide, and domain package examples (healthcare, industrial, residential, hospitality).

---

## What's built today

| Component | Status |
|-----------|--------|
| REST API (12+ endpoints) | ✅ |
| WebSocket real-time events | ✅ |
| Web dashboard | ✅ |
| API key authentication + SHA-256 audit log | ✅ |
| Semantic resolver — open intent classes, runtime registration | ✅ |
| Certification CLI — 32/32 tests (Basic · Standard · Emergency) | ✅ |
| Philips WiZ adapter (UDP local) | ✅ |
| Home Assistant bridge (10 domains) | ✅ |
| Native MCP server (Claude, ChatGPT, any LLM) | ✅ |
| GPIO adapter — Raspberry Pi 5 (PIR + DHT22) | ✅ |
| SMS notifications via Twilio | ✅ |
| Device discovery (UDP broadcast) | ✅ |
| SQLite persistence (survives restarts) | ✅ |
| Open resolver interface — BaseResolver + StateAwareResolver | ✅ |
| StateAwareResolver — redundancy elimination | ✅ |
| Policy engine (ALLOW · BLOCK · CONFIRM · MODIFY) | ✅ |
| Resolver benchmark — p99 < 0.11ms resolver · p99 < 9s concurrent load | ✅ |

---

## Performance

The semantic resolver is designed to be fast enough to never be the bottleneck.

| Resolver | Mean | p95 | p99 | Avg actions/intent |
|---|---|---|---|---|
| `CapabilityMatchingResolver` | 0.053ms | 0.074ms | 0.107ms | 52.3 |
| `StateAwareResolver` | 0.053ms | 0.084ms | 0.109ms | **33.7** |

Measured on the production registry (38 real devices, 500 iterations, seed 42).  
`StateAwareResolver` eliminates **35% of redundant actions** at the same latency cost.

**Scalability** (CapabilityMatchingResolver, O(n)):

| Devices | Mean | p99 | Within 500ms spec |
|---|---|---|---|
| 100 | 0.096ms | 0.196ms | ✓ |
| 1000 | 1.013ms | 3.044ms | ✓ |
| 5000 | 5.300ms | 11.392ms | ✓ |

**Semantic overhead vs direct command:** 0.051ms absolute — less than 1% of total execution time when accounting for real network latency (WiZ UDP: ~5–15ms, HA HTTP: ~20–80ms).

→ Full benchmark methodology and results: [docs/BENCHMARK-RESULTS.md](docs/BENCHMARK-RESULTS.md)  
→ Reproducible script: [benchmark_resolver.py](benchmark_resolver.py)

---

## MCP integration

DoSync ships a native MCP server. Any LLM with MCP support can control the hub directly — no extra configuration.

```json
{
  "mcpServers": {
    "dosync": {
      "command": "python3",
      "args": ["/path/to/dosync/dosync/mcp_server.py"],
      "env": {
        "DOSYNC_HUB_URL": "http://localhost:47200",
        "DOSYNC_TOKEN": "<your-token>"
      }
    }
  }
}
```

Once connected, you can say:

> *"Turn off all the lights"*  
> *"There is an emergency at home"*  
> *"Nobody is home — save energy"*

And the hub executes the full protocol in real time.

---

## Hardware demo

Tested with:
- **Raspberry Pi 5** — runs the hub autonomously 24/7
- **Philips WiZ** — 10 bulbs controlled via UDP local network
- **PIR HC-SR501** — motion detection → emergency intent
- **DHT22** — temperature/humidity sensor → anomaly detection

```
Motion detected (PIR on Raspberry Pi)
    → ensure_safety [emergency] fired
        → 10 WiZ bulbs at full brightness
        → SMS sent to family
        → Audit log updated
All in under 100ms. No internet. No cloud.
```

---

## Adapters

DoSync uses a pluggable adapter system. Adding support for a new device requires implementing a single method:

```python
class MyAdapter(DoSyncAdapter):
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        # translate DoSync action to device-native protocol
        ...

    @property
    def adapter_name(self) -> str:
        return "myadapter"
```

**Available today:** `wiz` · `homeassistant` · `gpio` · `simulated`  
**Planned:** `shelly` · `matter` · `ble` · `zigbee2mqtt`

---

## Certification

Three tiers — self-certifiable with the CLI:

| Tier | Requirements |
|------|-------------|
| **Basic** | Connects, authenticates, publishes capability manifest |
| **Standard** | Responds to intents, sends events |
| **Emergency** | Emergency override + tamper-evident SHA-256 audit log |

```bash
python3 certify.py --host <device-ip> --port 47200 --tier emergency
# Output: dosync-cert.json — signed certification report
```

---

## Repository structure

```
dosync-protocol/
├── dosync/
│   ├── models.py              # Core types — Intent, Manifest, ActionPlan
│   ├── hub.py                 # Semantic resolver + capability registry
│   ├── executor.py            # Device executor abstraction
│   ├── db.py                  # SQLite persistence
│   ├── auth.py                # API key authentication
│   ├── discovery.py           # UDP device discovery
│   ├── mcp_server.py          # Native MCP server
│   └── adapters/
│       ├── wiz.py             # Philips WiZ (UDP)
│       ├── homeassistant.py   # Home Assistant bridge
│       └── notifications.py   # SMS via Twilio
├── server.py                  # FastAPI hub — REST + WebSocket
├── dashboard.html             # Real-time web dashboard
├── certify.py                 # Certification CLI
├── discover.py                # Device discovery CLI
├── ha_bridge.py               # Home Assistant import CLI
├── gpio_adapter.py            # Raspberry Pi GPIO adapter
├── manage.py                  # Key and DB management CLI
├── benchmark_resolver.py      # Resolver performance benchmark
├── examples/
│   └── demo_full.py           # 7-scenario demo
├── spec/
│   ├── DOSYNC-SPEC-v0.1.md    # Full protocol specification
│   └── RESOLVER-SPEC-v0.2.md  # Resolver interface specification
└── docs/
    ├── architecture.svg
    └── BENCHMARK-RESULTS.md   # Benchmark results and methodology
```

---

## Not just for the home

DoSync's architecture is domain-agnostic. The same 5-layer stack works anywhere an AI needs to act on physical systems:

- **Hospital** — "prepare OR 3 for emergency" → equipment, lighting, access coordinated
- **Hotel** — "guest in 412 has arrived" → room configured to saved preferences
- **Factory** — "line B failure" → safe shutdown, notifications, audit trail
- **Smart building** — energy, security, access — orchestrated by intent

The protocol is the infrastructure. The domain is up to you.

---

## Specification

Full protocol specification: [spec/DOSYNC-SPEC-v0.1.md](spec/DOSYNC-SPEC-v0.1.md)
Design principles: [DESIGN-PRINCIPLES.md](DESIGN-PRINCIPLES.md)
Intent classes guide: [docs/INTENT-CLASSES-GUIDE.md](docs/INTENT-CLASSES-GUIDE.md)
Deployment tags guide: [docs/DEPLOYMENT-TAGS-GUIDE.md](docs/DEPLOYMENT-TAGS-GUIDE.md)
Website: [dosync.dev](https://dosync.dev)

---

## License

Apache 2.0 — free to implement, free to extend, no royalties.

---

*DoSync Protocol v0.4 · © 2026 Rodrigo Giuliani · [rgiuliani@dosync.dev](mailto:rgiuliani@dosync.dev) · [dosync.dev](https://dosync.dev)*
