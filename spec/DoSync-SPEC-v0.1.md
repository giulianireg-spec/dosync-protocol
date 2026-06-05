# DoSync Protocol — Specification v0.1

**Status:** Draft  
**Authors:** DoSync Initiative  
**License:** Apache 2.0  
**Repository:** github.com/dosync/protocol  

---

## Abstract

DoSync is an open communication protocol that enables AI systems to interact with physical devices (gadgets) in a home environment using semantic intent rather than direct commands. Unlike existing smart home protocols (Matter, Zigbee, Z-Wave), DoSync introduces a semantic layer that allows an AI to express *what it wants to achieve*, while each device resolves its own contribution to that goal.

DoSync is transport-agnostic: the same protocol operates over WiFi, Bluetooth LE, Zigbee, Z-Wave, Thread, and Ethernet through a Hardware Abstraction Layer (HAL).

---

## 1. Design Principles

1. **Intent over commands.** The AI expresses goals ("ensure grandmother's safety"), not instructions ("turn on camera 3").
2. **Privacy first.** All communication is local-network only by default. No cloud dependency.
3. **Transport agnostic.** Devices connect via any physical medium; the protocol is identical.
4. **Zero-config discovery.** Devices announce capabilities automatically on joining the network.
5. **Graceful degradation.** Partial device failure must never block the resolution of an intent.
6. **Open and certifiable.** Any manufacturer can implement DoSync and self-certify via the public test suite.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────┐
│           Layer 5 — Intent              │  AI expresses semantic goals
├─────────────────────────────────────────┤
│           Layer 4 — Semantic            │  Intent → device action mapping
├─────────────────────────────────────────┤
│       Layer 3 — Capability Registry     │  Device self-declaration
├─────────────────────────────────────────┤
│        Layer 2 — Secure Channel         │  mTLS, local PKI, zero-trust
├─────────────────────────────────────────┤
│      Layer 1 — Transport (HAL)          │  WiFi / BLE / Zigbee / Z-Wave
└─────────────────────────────────────────┘
```

---

## 3. Layer 1 — Transport (HAL)

The Hardware Abstraction Layer normalizes all physical transports into a unified message bus. Each transport adapter implements the following interface:

```
send(device_id: str, payload: bytes) -> Ack
receive() -> Iterator[Message]
discover() -> Iterator[DeviceAnnouncement]
```

### 3.1 Supported transports (v0.1)

| Transport | Port / Channel | Notes |
|-----------|---------------|-------|
| WiFi / TCP | 47200 (DoSync default) | Primary for fixed appliances |
| WiFi / UDP | 47201 | Discovery broadcasts |
| Bluetooth LE | GATT service UUID `DS01` | Battery-powered sensors |
| Zigbee | Cluster `0xFC00` | Low-power mesh |
| Z-Wave | Command class `0x9F` | Legacy interop |
| Ethernet | Same as WiFi/TCP | Wired appliances |

### 3.2 Message framing

All messages use length-prefixed binary framing:

```
[4 bytes: total length] [1 byte: version=1] [1 byte: msg_type] [payload: JSON-UTF8]
```

---

## 4. Layer 2 — Secure Channel

All DoSync communication is encrypted and mutually authenticated. No unencrypted fallback exists.

### 4.1 Local PKI

The DoSync Hub acts as a local Certificate Authority (CA). On first boot:

1. Hub generates a self-signed root CA (`dosync-root.crt`).
2. Each joining device receives a signed device certificate.
3. All channel encryption uses TLS 1.3 with mutual authentication (mTLS).
4. Certificates are rotated annually or on manual revocation.

### 4.2 Device onboarding

```
Device                    Hub
  │──── HELLO (device_id, pub_key) ──────►│
  │◄─── CHALLENGE (nonce) ────────────────│
  │──── RESPONSE (signed_nonce) ──────────►│
  │◄─── CERT (signed_device_cert) ────────│
  │──── ACK ───────────────────────────────►│
  │         [TLS 1.3 channel established]  │
```

### 4.3 Permission model

Each device has a permission scope defined at onboarding:

```json
{
  "device_id": "lock-frontdoor-01",
  "permissions": {
    "actuate": ["lock", "unlock"],
    "sense": ["state"],
    "emergency_override": true
  }
}
```

`emergency_override: true` allows the AI to actuate the device during a declared emergency event, bypassing normal family permission requirements.

---

## 5. Layer 3 — Capability Registry

Every DoSync device broadcasts a **Capability Manifest** upon joining the network and whenever its capabilities change.

### 5.1 Capability Manifest schema

```json
{
  "dosync_version": "0.1",
  "device_id": "fridge-kitchen-01",
  "device_name": "Kitchen refrigerator",
  "manufacturer": "AcmeCorp",
  "model": "CoolMaster 3000",
  "firmware": "2.1.4",
  "capabilities": {
    "sensors": [
      {
        "id": "temp_internal",
        "type": "temperature",
        "unit": "celsius",
        "range": [-30, 10],
        "poll_interval_ms": 30000
      },
      {
        "id": "compressor_status",
        "type": "boolean",
        "description": "Compressor running state"
      },
      {
        "id": "door_open",
        "type": "boolean",
        "description": "Door open/closed"
      }
    ],
    "actuators": [],
    "events": [
      {
        "id": "malfunction",
        "severity": "warning",
        "description": "Compressor failure or abnormal temperature detected"
      },
      {
        "id": "door_open_extended",
        "severity": "info",
        "description": "Door open for more than 2 minutes"
      }
    ]
  },
  "tags": ["kitchen", "appliance", "food-safety"],
  "emergency_capable": false
}
```

### 5.2 Device categories (taxonomy v0.1)

| Category | Description | Example tags |
|----------|-------------|--------------|
| `sensor` | Reads environment, no actuation | `temperature`, `motion`, `camera` |
| `actuator` | Takes physical action | `lock`, `light`, `robot` |
| `hybrid` | Both senses and actuates | `thermostat`, `smart-fridge` |
| `communication` | Sends messages externally | `phone`, `intercom` |
| `emergency` | Critical safety devices | `alarm`, `door-lock`, `camera` |

---

## 6. Layer 4 — Semantic Layer

The semantic layer is the core differentiator of DoSync. It maps high-level AI intents to concrete device actions by matching intent requirements against registered device capabilities.

### 6.1 Intent object

```json
{
  "intent_id": "int-20240915-001",
  "intent": "ensure_safety",
  "subject": "grandmother",
  "context": {
    "location": "bedroom",
    "trigger": "fall_detected",
    "urgency": "emergency"
  },
  "constraints": {
    "timeout_ms": 5000,
    "require_confirmation": false
  }
}
```

### 6.2 Intent resolution

The semantic resolver follows this algorithm:

```
1. Parse intent → extract (action_class, subject, context, urgency)
2. Query capability registry for devices matching action_class
3. For each matching device, compute relevance_score:
     relevance = tag_overlap(device.tags, intent.context) 
               + location_match(device.location, intent.context.location)
               + urgency_capable(device, intent.urgency)
4. Sort devices by relevance_score descending
5. Build ActionPlan: list of (device, action, params) tuples
6. Execute ActionPlan in parallel where possible, sequential where dependent
7. Collect results → emit IntentResult
```

### 6.3 Urgency levels

Every intent carries an urgency level that controls execution behavior across the protocol stack — policy evaluation, emergency override, audit logging priority, and device inclusion.

| Level | Value | Behavior |
|---|---|---|
| `emergency` | `"emergency"` | Bypasses all policy constraints. Executes immediately without confirmation. Emergency-capable devices always included. All actions logged as critical with SHA-256 chain. |
| `alert` | `"alert"` | High priority. Confirmation policies may apply depending on hub configuration. Devices with `emergency_capable: true` score higher. |
| `warning` | `"warning"` | Elevated priority. A condition that warrants attention but does not require immediate action. All policies apply. Used for anomalies that are notable but not urgent (e.g. high temperature, unusual sensor reading). |
| `info` | `"info"` | Normal priority. All policies apply. Default for routine operations, status updates, and scheduled events. |

**The urgency hierarchy:** `emergency > alert > warning > info`

Only `emergency` triggers the emergency override path — bypassing policy constraints and confirmation requirements. The other three levels are subject to full policy evaluation.

**Usage guidance:**
- Use `emergency` only for genuine safety threats where milliseconds matter and bypassing policies is acceptable
- Use `alert` for conditions that may require human decision before acting  
- Use `warning` for anomalies that should be logged and monitored but not acted upon immediately
- Use `info` for all routine operations

### 6.4 Built-in intent classes (v0.1)

| Intent class | Description | Typical devices triggered |
|---|---|---|
| `ensure_safety` | Emergency response | Camera, door-lock, alarm, phone |
| `notify_family` | Send alert to family members | Phone, intercom, display |
| `report_status` | Read and report device state | Any sensor |
| `set_environment` | Adjust ambient conditions | Lights, thermostat, blinds |
| `control_access` | Lock/unlock entry points | Door locks, gates |
| `monitor_health` | Ongoing observation | Camera, motion sensor, wearable |

### 6.5 Emergency escalation

When `urgency = "emergency"` and `emergency_override = true` on a device:

```
1. Skip normal permission checks
2. Execute immediately (no confirmation required)
3. Log all actions with tamper-evident timestamp
4. Notify all family members simultaneously
5. Allow external communication (call emergency services)
```

---

## 7. Layer 5 — Intent Layer

The intent layer is the interface between AI systems and the DoSync protocol. AI agents communicate with the hub using structured JSON intents submitted directly via the REST API or the native MCP server.

**Architecture note:** DoSync does not include an NLP parser. The translation from natural language to structured intents is the responsibility of the AI agent — this is by design. DoSync is protocol infrastructure; the intelligence layer belongs to the AI system using it. Any LLM capable of calling HTTP endpoints or using MCP can act as the intent-generation layer.

### 7.1 AI agent integration patterns

**Pattern A — MCP server (recommended)**

DoSync ships a native MCP (Model Context Protocol) server. Any LLM with MCP support (Claude, ChatGPT, and others) connects directly and fires intents using natural language:

```
User: "There's an emergency at home"
AI agent (via MCP): calls dosync_fire_intent("ensure_safety", "emergency", {})
Hub: resolves intent → 10 WiZ bulbs at full brightness, SMS sent, alarm activated
```

MCP configuration:
```json
{
  "mcpServers": {
    "dosync": {
      "command": "python3",
      "args": ["/path/to/dosync/mcp_server.py"],
      "env": {
        "DOSYNC_HUB_URL": "http://localhost:47200",
        "DOSYNC_TOKEN": "<your-token>"
      }
    }
  }
}
```

**Pattern B — Direct REST API**

For programmatic use, any system can submit structured intents directly to the DoSync Hub REST API:

### 7.2 Structured intent (direct REST API)

```
POST /v1/intent/async
Authorization: Bearer <hub_token>
Content-Type: application/json

{
  "intent": "notify_family",
  "context": {
    "trigger": "fridge_malfunction",
    "device_id": "fridge-kitchen-01",
    "message": "The refrigerator has detected a malfunction. Food may be at risk."
  },
  "urgency": "warning"
}
```

> **Note:** `/v1/intent` (without `/async`) is also accepted and redirects via HTTP 308 to `/v1/intent/async`. Clients that do not follow redirects should use `/v1/intent/async` directly.

### 7.3 Event subscription (device → AI)

Devices can push events to the AI without being polled:

```
POST /v1/event  (device → Hub → AI)

{
  "device_id": "fridge-kitchen-01",
  "event_id": "malfunction",
  "timestamp": "2024-09-15T14:32:00Z",
  "data": {
    "temp_internal": 18.5,
    "compressor_status": false,
    "duration_minutes": 45
  }
}
```

---

## 8. Certification

A device or hub implementation is **DoSync Certified** if it passes the official certification CLI (`certify.py`) included in the reference repository.

The certification suite separates two distinct test categories following ISO/IEC 9646-1 conformance testing methodology:

- **Conformance tests** — verify that the implementation correctly processes protocol messages, independent of physical device execution. These tests use `fire_intent_conformance()` which checks acceptance only (HTTP 200 + correct response structure). Fast, deterministic, no physical devices required.
- **Integration tests** — verify execution outcomes against real or simulated devices. These tests use `fire_intent()` with polling.

### 8.1 Certification tiers

| Tier | Requirements |
|------|-------------|
| **DoSync Basic** | Layers 1–3: connects, authenticates, publishes capability manifest |
| **DoSync Standard** | Layers 1–4: responds to intents, sends events |
| **DoSync Emergency** | All layers + `emergency_override` support + tamper-evident logging |

### 8.2 Self-certification process

**Production mode** — against a live hub with physical devices:

```bash
# 1. Clone the reference implementation
git clone https://github.com/giulianireg-spec/dosync-protocol

# 2. Run certification against your hub
DOSYNC_TOKEN=<token> python3 certify.py --host <hub-ip> --port 47200 --tier emergency

# 3. Output: dosync-cert.json — signed certification report with fingerprint
```

**Certify mode** — without physical devices (CI/CD, development, third-party implementors):

```bash
# 1. Start hub with SimulatedExecutor
DOSYNC_CERTIFY=true uvicorn server:app --host 0.0.0.0 --port 47200

# 2. Run full certification suite — completes in <30 seconds
DOSYNC_TOKEN=<token> python3 certify.py --host localhost --port 47200 --tier emergency
```

The hub status endpoint (`GET /v1/status`) exposes `"certify_mode": true` when running in certify mode. The CLI detects this automatically and displays a warning banner.

> **Important:** `DOSYNC_CERTIFY=true` must never be set in production deployments. The hub logs a `WARNING` at startup when this mode is active.
4. Manufacturer publishes the report alongside their device SDK

No manual approval by the DoSync Initiative is required for Basic and Standard tiers. Emergency tier requires human review.

---

## 9. Privacy and Security Considerations

- **No cloud required.** The entire protocol operates on the local network. External communication (e.g., calling emergency services) is initiated by a device with explicit `communication` capability, not by the Hub itself.
- **Data minimization.** Devices transmit only what is declared in their Capability Manifest.
- **Audit log.** All intent executions and emergency events are logged locally with tamper-evident hashing (SHA-256 chained log).
- **Family consent model.** Non-emergency intents require at least one adult family member's authorization token.
- **No persistent audio/video.** Camera and microphone devices may not store data; they may only stream in response to an active intent or event.

---

## 10. Versioning and Compatibility

DoSync uses semantic versioning (`MAJOR.MINOR.PATCH`). Devices must declare the maximum protocol version they support. The Hub negotiates the highest mutually supported version on each connection.

---

## Appendix A — Example scenarios

### A.1 Fall detection emergency

```
[Camera detects fall] → event: ensure_safety / emergency
[Hub receives event]
[Semantic resolver activates]:
  → Phone: call emergency services (911 / local equivalent)
  → Front door lock: unlock
  → All lights: maximum brightness
  → Family phones: push notification with location + camera snapshot
  → Intercom: announce "Emergency detected. Help is on the way."
[All actions logged with timestamp]
```

### A.2 Refrigerator malfunction

```
[Fridge sensor: temp > 8°C for 30 min, compressor off]
→ event: malfunction / warning
[Hub receives event]
[Semantic resolver activates]:
  → Family phones: push notification
    "Your refrigerator has stopped cooling (18.5°C, 45 min).
     Consider moving perishables. Technician contact: [saved contact]"
  → Home display: show alert
[No emergency override needed]
```

---

## Appendix B — Comparison with existing protocols

| Feature | DoSync | Matter | Zigbee | Z-Wave | Home Assistant |
|---------|--------|--------|--------|--------|----------------|
| Semantic intent layer | ✅ | ❌ | ❌ | ❌ | ❌ |
| Transport agnostic | ✅ | Partial | ❌ | ❌ | Partial |
| Local-only by default | ✅ | ❌ | ✅ | ✅ | ✅ |
| AI-native design | ✅ | ❌ | ❌ | ❌ | ❌ |
| Emergency override | ✅ | ❌ | ❌ | ❌ | ❌ |
| Open + self-certifiable | ✅ | Partial | ✅ | ❌ | N/A |
| Generational memory | ✅* | ❌ | ❌ | ❌ | ❌ |

*Via integration with FamilyOS

---

*DoSync Protocol Specification v0.1 — DoSync Initiative — Apache 2.0 License*
