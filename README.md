# DoSync Protocol

> Open AI-native smart home protocol — semantic intent over any transport

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.1-green.svg)](spec/DOSYNC-SPEC-v0.1.md)
[![Certification](https://img.shields.io/badge/certification-Basic%20%7C%20Standard%20%7C%20Emergency-orange.svg)](certify.py)

## What is DoSync?

DoSync is an open communication protocol that lets AI systems interact with physical home devices using **semantic intent** — expressing *what they want to achieve*, not *how to achieve it*.

Unlike Matter, Zigbee, or Z-Wave, DoSync introduces a semantic layer where the AI says `"ensure grandmother's safety"` and every device figures out its own role automatically.

## The problem it solves

Today's smart home protocols speak the language of commands: `ON`, `OFF`, `SET_TEMP`. But AI assistants think in goals. DoSync bridges that gap.

**Example — fall detection emergency:**

```
Camera detects fall → AI submits intent: ensure_safety / emergency
DoSync resolves:
  → Front door lock  : unlock (for emergency responders)
  → Phone hub        : call 911
  → Alarm            : activate emergency pattern
  → Family phones    : push notification
All actions execute in parallel. All logged with tamper-evident SHA-256 chain.
```

**Example — appliance failure:**

```
Fridge compressor stops → device emits: malfunction / warning
DoSync resolves:
  → Family phones : "Your fridge stopped cooling (18.5°C, 45 min). Move perishables."
```

## Protocol stack

| Layer | Name | Description |
|-------|------|-------------|
| 5 | Intent | AI expresses semantic goals in natural language or JSON |
| 4 | Semantic | Resolves intent → device actions via capability matching |
| 3 | Registry | Devices self-declare capabilities on network join |
| 2 | Secure channel | mTLS, local PKI, zero-trust — no internet required |
| 1 | Transport (HAL) | WiFi · BLE · Zigbee · Z-Wave · Thread · Ethernet |

## Quick start

```bash
# Install dependencies
python3 -m venv venv && source venv/bin/activate
pip install fastapi uvicorn

# Start the hub
PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 47200 --reload

# Open the interactive API docs
open http://localhost:47200/docs

# Run certification tests
python3 certify.py --host localhost --port 47200 --tier emergency
```

## Certification

DoSync uses a self-certification model. Three tiers:

| Tier | Requirements |
|------|-------------|
| **Basic** | Connects, authenticates, publishes capability manifest |
| **Standard** | Responds to intents, sends events |
| **Emergency** | Emergency override + tamper-evident audit log |

```bash
python3 certify.py --host <device-ip> --port 47200 --tier standard
```

## Comparison

| Feature | DoSync | Matter | Zigbee | Z-Wave |
|---------|--------|--------|--------|--------|
| Semantic intent layer | ✅ | ❌ | ❌ | ❌ |
| Transport agnostic | ✅ | Partial | ❌ | ❌ |
| Local-only by default | ✅ | ❌ | ✅ | ✅ |
| AI-native design | ✅ | ❌ | ❌ | ❌ |
| Emergency override | ✅ | ❌ | ❌ | ❌ |
| Open + self-certifiable | ✅ | Partial | ✅ | ❌ |

## Repository structure

```
dosync-protocol/
├── dosync/           # Python reference SDK
│   ├── models.py     # Core data models (Layers 3–5)
│   ├── hub.py        # Capability registry + semantic resolver
│   └── executor.py   # Device executor abstraction
├── examples/
│   └── demo.py       # Working demo — fall emergency + fridge failure
├── server.py         # REST API hub (FastAPI)
├── certify.py        # Certification CLI
└── spec/
    └── DOSYNC-SPEC-v0.1.md  # Full protocol specification
```

## Specification

Full protocol specification: [spec/DOSYNC-SPEC-v0.1.md](spec/DOSYNC-SPEC-v0.1.md)

## License

Apache 2.0 — free to implement, free to extend.

---

*DoSync Protocol v0.1 — © 2025 Rodrigo Giuliani*
