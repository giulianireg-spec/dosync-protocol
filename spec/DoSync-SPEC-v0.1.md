# DoSync Protocol — Specification

*(Protocol version 0.4 — see the changelog / §10. The file keeps its original name to preserve external links.)*

**Status:** Draft  
**Authors:** DoSync Initiative  
**License:** Apache 2.0  
**Repository:** github.com/dosync/protocol  

---

## Abstract

DoSync is an open communication protocol that enables AI systems to interact with physical devices in any physical environment using semantic intent rather than direct commands. Unlike existing IoT protocols (Matter, Zigbee, Z-Wave), DoSync introduces a semantic layer that allows an AI to express *what it wants to achieve*, while each device resolves its own contribution to that goal.

DoSync is transport-agnostic: the same protocol operates over WiFi, Bluetooth LE, Zigbee, Z-Wave, Thread, and Ethernet through a Hardware Abstraction Layer (HAL).

---

## Protocol scope and reference implementation

This document defines the **DoSync protocol**: the wire format, data model, and behavioral contract that any conforming implementation must satisfy. The protocol is language-independent — a conforming hub may be implemented in any language.

**What this specification defines (the protocol):**
- The JSON wire format for Intent, CapabilityManifest, ActionPlan, and IntentResult (see `spec/schemas/`)
- The REST API surface that a conforming hub must expose
- The behavioral requirements for each layer (capability registry, policy engine, resolver interface, audit log, security)
- The certification model (Basic / Standard / Emergency tiers)

**What this specification does not define (implementation details):**
- How the hub stores device state internally
- Which programming language or runtime is used
- Which database engine backs the audit log
- How the capability-based resolver scores devices internally, beyond the interface contract

**Reference implementation:** The Python hub at `github.com/giulianireg-spec/dosync-protocol` is the canonical reference implementation. It demonstrates one correct implementation of this specification. A second independent implementation in Node.js lives at `implementations/dosync-node/`. Neither is normative — the JSON schemas and behavioral requirements in this document are.

A third-party implementation is conforming if it passes the certification CLI at the declared tier. The certification tests are transport-agnostic and test the protocol surface, not the implementation internals.

**Normative vs roadmap.** This specification describes the protocol as implemented and certified. Where it records intended-but-unimplemented capability — native radio bindings, a constrained-transport binary framing, native-transport onboarding, fine-grained permission scopes — that text is labeled *"roadmap — non-normative"* inline. Only normative text constrains conformance; roadmap items are direction, not requirements.

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
│      Layer 1 — Transport (HAL)          │  Transport-agnostic · ref: HTTP/WS
└─────────────────────────────────────────┘
```

---

## 3. Layer 1 — Transport (HAL)

**DoSync is transport-agnostic. This is a normative design principle:** the protocol does not mandate a physical medium or a single wire format at Layer 1. Any transport that can deliver a DoSync message between a device and the hub — WiFi, Ethernet, Bluetooth LE, Zigbee / Z-Wave / Thread / LoRa radio, or cellular — is a conforming transport. Layers 3–5 (registry, semantics, intent) are identical regardless of how the bytes move underneath.

Conceptually, a transport binding realizes a minimal interface:

```
send(device_id, payload) -> Ack
receive() -> Iterator[Message]
discover() -> Iterator[DeviceAnnouncement]
```

The reference implementation realizes this over **HTTP/1.1 + WebSocket with JSON payloads** (§7.2). Devices on radios the hub cannot address directly (Zigbee, Z-Wave, Thread, Matter) participate today through **adapters and bridges** rather than a native DoSync binding — most notably the Home Assistant bridge, which exposes thousands of such devices to the hub. This is deliberate layering: DoSync adds semantics on top of transports that already exist, rather than replacing them.

### 3.1 Transports

**Implemented — reference transport:**

| Transport | Port / Channel | Notes |
|-----------|---------------|-------|
| WiFi / Ethernet / TCP | 47200 (HTTP + WebSocket) | Primary transport for the hub API |
| WiFi / UDP | 47201 | Device discovery broadcasts |

**Reachable via adapter / bridge (implemented):**

| Path | Reaches | Mechanism |
|------|---------|-----------|
| WiZ adapter | Philips WiZ devices | UDP on the local network |
| MQTT adapter | MQTT-capable devices | broker (opt-in) |
| Home Assistant bridge | Zigbee, Z-Wave, Thread, Matter and 3000+ integrations | HA REST / WebSocket API |

**Planned native bindings — non-normative (roadmap):**

| Transport | Intended channel | Status |
|-----------|-----------------|--------|
| Bluetooth LE | native GATT service | planned — not implemented |
| Zigbee (native) | dedicated cluster | planned — not implemented |
| Z-Wave (native) | dedicated command class | planned — not implemented |
| Cellular (LTE / NB-IoT) | direct device → hub | planned — not implemented |

A *native* binding means a device speaks DoSync directly over that medium, without an intermediate bridge. These are genuine engineering efforts (MTU fragmentation, pairing, reconnection, power management) tracked on the roadmap — not promised here as available.

### 3.2 Message framing

The reference transport frames messages as standard **HTTP requests / responses with JSON bodies**, and real-time events as **JSON text frames over WebSocket** (§7.3). This is the normative wire format for the reference transport.

**Roadmap — optional binding for constrained transports (non-normative).** Media with small MTUs (BLE, LoRa) cannot carry HTTP/JSON efficiently. A future native binding may use a compact length-prefixed binary framing — e.g. `[4 bytes length][1 byte version][1 byte msg_type][JSON or CBOR payload]`. This is a planned optional binding for constrained native transports, not a requirement of the protocol.

---

## 4. Layer 2 — Secure Channel

All DoSync communication is encrypted and mutually authenticated. No unencrypted fallback exists.

### 4.1 Local PKI

The DoSync Hub acts as a local Certificate Authority (CA):

1. The hub provisions a local CA (`ca.crt` in the reference deployment).
2. Each joining device may present a certificate signed by that CA; the hub verifies the chain and records `cert_authenticated` on the device manifest.
3. Channel encryption uses TLS 1.3 with mutual authentication (mTLS) when certificates are configured.
4. Certificate rotation is performed manually or by operator script. *(Automated rotation is on the roadmap.)*

### 4.2 Device onboarding

In the reference implementation a device onboards over the REST API:

1. *(Optional)* The operator provisions a `device_id` and receives a one-time `device_token` (`POST /v1/devices/provision`).
2. The device registers via `POST /v1/devices/register`, presenting either its `device_token` or a `certificate_pem` signed by the local CA (verified against the CA; `cert_authenticated` is recorded on the manifest).
3. On success the device's Capability Manifest (§5) enters the registry and the device participates in intent resolution.

Devices reached through a bridge (e.g. Home Assistant) are onboarded by the bridge on their behalf.

**Roadmap — native-transport onboarding (non-normative).** For future native bindings over constrained media, a compact challenge/response handshake (device `HELLO` → hub `CHALLENGE` → device `RESPONSE` → hub `CERT`) is planned to establish an mTLS channel without REST round-trips. Not implemented today.

### 4.3 Permission and override model

Authorization in the reference implementation is expressed at two levels, not as a per-device permission object:

- **`emergency_capable`** (manifest flag): a device declares whether it may be actuated during a declared emergency, bypassing normal policy confirmation. This is the flag the resolver and Policy Engine honor for emergency intents.
- **Policy Engine** (§6.6): explicit, auditable policies (e.g. `NeverAfterHoursPolicy`, `RequireConfirmationPolicy`, `DeviceExclusionPolicy`) constrain what may be actuated, when, and by whom.

**Roadmap — fine-grained permission scopes (non-normative).** A richer per-device permission object (explicit `actuate` / `sense` scopes negotiated at onboarding) is planned but not implemented; today the `emergency_capable` flag plus the Policy Engine cover the model.

---

## 5. Layer 3 — Capability Registry

Every DoSync device broadcasts a **Capability Manifest** upon joining the network and whenever its capabilities change.

### 5.1 Capability Manifest schema

```json
{
  "dosync_version": "0.4",
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
        "description": "Compressor running state",
        "kind": "device_state"
      },
      {
        "id": "door_open",
        "type": "boolean",
        "description": "Door open/closed",
        "kind": "device_state"
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

**Sensor `kind`** (optional, since 0.3.1; default `"environment"`): distinguishes what a
sensor *measures*, not which device owns it. `"environment"` — measures the world
(temperature of a space, motion, humidity); `"device_state"` — reports the device's own
condition (a compressor's running state, a door's position, a lamp's brightness, a
setpoint). The grain is per sensor, deliberately: in the example above, `temp_internal`
measures the air in the compartment (environment, the default) while `compressor_status`
and `door_open` describe the appliance itself. Hubs MAY use the kind to scope status
queries ("read the environment" vs "read every device's self-state"); the protocol
distinguishes the two questions and takes no position on which one a deployment's bare
status query should mean. Manifests that omit the field remain valid: absent means
`"environment"`.

### 5.2 Device categories (taxonomy v0.1)

| Category | Description | Example tags |
|----------|-------------|--------------|
| `sensor` | Reads environment, no actuation | `temperature`, `motion`, `camera` |
| `actuator` | Takes physical action | `lock`, `light`, `robot` |
| `hybrid` | Both senses and actuates | `thermostat`, `smart-fridge` |
| `communication` | Sends messages externally | `phone`, `intercom` |
| `emergency` | Critical safety devices | `alarm`, `door-lock`, `camera` |

---

### 5.3 Device re-registration and firmware updates

A device may re-register with the hub at any time — after a firmware update, a reboot, or a network reconnect. The hub MUST classify the re-registration using the following deterministic rule based on the diff between the incoming manifest and the previously registered manifest:

| Condition | Classification | Hub action |
|---|---|---|
| No fields changed | `reconnect` | Silent upsert — no audit entry |
| `firmware` changed; capabilities stable | `firmware_upgrade_minor` | `device_firmware_updated` audit entry |
| `firmware` changed; capabilities also changed | `firmware_update` | `device_updated` audit entry with full diff |
| `firmware` unchanged; capabilities changed | `capability_anomaly` | `device_capability_anomaly` audit entry + `alert_anomaly [urgency=alert]` |

**Capability fields tracked:** `emergency_capable`, `tags`, actuator types.

**Design rationale:** The `firmware` field is the authoritative signal of intentional change. A capability change with a firmware version change is expected and trusted. A capability change without a firmware version change is anomalous — the same firmware produced different capabilities, which may indicate device compromise or hardware failure.

**On capability anomaly:** The hub MUST fire `alert_anomaly [urgency=alert]` with context `{trigger: "device_capability_anomaly", device_id, diff}`. This alert is non-blocking — registration completes regardless.

---

## 6. Layer 4 — Semantic Layer

The semantic layer is the core differentiator of DoSync. It maps high-level AI intents to concrete device actions by matching intent requirements against registered device capabilities.

Formal JSON Schemas for all wire format objects are in `spec/schemas/`. The schemas are the normative definition of each object's structure. The examples below are illustrative.

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

The capability-based resolver follows this algorithm:

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

### 6.4 Universal intent classes

Five intent classes are defined at the protocol level and seeded into every DoSync hub at initialization. They are valid in any physical environment regardless of domain:

| Intent class | Urgency | Description |
|---|---|---|
| `ensure_safety` | `emergency` | Safety emergency — protect people and property |
| `alert_anomaly` | `alert` | Unexpected condition detected — investigate |
| `control_access` | `alert` | Manage physical access to a space |
| `report_status` | `info` | Generate a status report of the environment |
| `notify` | `info` | Push information to any target |

#### 6.4.1 Resolution contract (normative)

A conforming hub MUST seed these five classes with exactly the resolution tags
and actuator types below. This is the contract that decides **which devices a
universal intent selects**, and until v0.4.4 it existed only inside the
reference implementation — so two hubs could both pass certification and
resolve the same intent differently, which is the one thing a protocol cannot
allow.

| Intent class | `resolution_tags` | `resolution_actuators` |
|---|---|---|
| `ensure_safety` | `emergency`, `alarm`, `communication`, `notification` | `alarm`, `notify`, `call`, `turn_on`, `set_brightness` |
| `alert_anomaly` | `communication`, `notification`, `sensor` | `notify`, `call` |
| `control_access` | `lock` | `lock`, `unlock` |
| `report_status` | *(none)* | *(none)* |
| `notify` | `communication`, `notification`, `display` | `notify`, `display`, `call` |

Two properties of this table are normative and easy to miss:

- **`report_status` declares nothing on purpose.** An empty resolution is not
  an omission: it selects every device that declares sensors and issues
  read-only actions (§6.6). A hub that gives it tags changes what a status
  query means.
- **Tags rank, actuators gate.** A device is selected on tag match; the
  actions built for it come from the actuator intersection. A device whose
  tags match but whose actuators do not is selected and contributes no action
  — except under `emergency`, where the full-capability fallback applies.

This table is generated from and verified against the reference seed
(`tests/test_universal_intent_contract.py`): if the implementation and this
section ever disagree, the test fails. A specification that restates a fact
the code also holds is how the two drift.

These five are protected — they cannot be deleted or overridden. Any additional intent classes (e.g. `morning_routine`, `away_mode`, `prepare_operating_room`) are registered per deployment. See `docs/INTENT-CLASSES-GUIDE.md` for the full two-layer model and domain package examples.

### 6.5 Emergency escalation

When `urgency = "emergency"` and `emergency_override = true` on a device:

```
1. Skip normal permission checks
2. Execute immediately (no confirmation required)
3. Log all actions with tamper-evident timestamp
4. Notify all registered contacts simultaneously
5. Allow external communication (call emergency services)
```

---

### 6.6 Policy Engine

The Policy Engine evaluates every intent before execution, regardless of origin. Policies are evaluated in priority order (lowest number first). The first `BLOCK` or `CONFIRM` result stops evaluation. `MODIFY` results are accumulated — multiple `MODIFY` policies can apply to the same intent.

Emergency intents (`urgency = "emergency"`) bypass policy evaluation by default. Each policy declares whether it participates in this bypass via a `bypass_on_emergency` flag (default: `true`). Policies that represent absolute operator constraints set `bypass_on_emergency = false` and are evaluated even for emergency intents. The built-in `BlockIntentPolicy` uses this mechanism: an operator-blocked intent class cannot be executed regardless of urgency. All other built-in policies (`NeverAfterHoursPolicy`, `RequireConfirmationPolicy`, etc.) default to `bypass_on_emergency = true` and are bypassed on emergency urgency.

```
PolicyEngine.evaluate(intent, action_plan) → PolicyResult
  decision: ALLOW | BLOCK | CONFIRM | MODIFY
  reason:   human-readable explanation
  policy_name: which policy produced this result
```

Built-in policies (all configurable):

| Policy | Priority | Behavior |
|---|---|---|
| `IntentRateLimitPolicy` | 0 | Limits intent frequency per source. **Required** in all compliant deployments. |
| `ConflictResolutionPolicy` | 1 | Blocks lower-priority intents when a higher-priority intent is active. |
| `ContextualWeightingPolicy` | 2 | Adjusts device scoring based on time-of-day and context signals. |
| `NeverAfterHoursPolicy` | 10 | Blocks specific actuator types outside defined time windows. |
| `RequireConfirmationPolicy` | 20 | Requires explicit confirmation before executing specified actuators. |

### 6.7 Intent frequency limits

Every DoSync-compliant hub implementation MUST enforce intent frequency limits. This is a required protocol component.

**Default minimum limits (per source, per 60-second window):**

| Urgency | Minimum limit | Emergency override |
|---|---|---|
| `info` | 60 / minute | N/A |
| `warning` | 60 / minute | N/A |
| `alert` | 20 / minute | N/A |
| `emergency` | **Unlimited** | Always executes |

A compliant implementation MUST:
- Apply limits independently per source (MCP client, REST API, GPIO, scheduler)
- Never rate-limit `urgency = "emergency"` intents
- Return a `BLOCK` decision with `policy_name = "intent_rate_limit"` when a limit is exceeded
- Include a `Retry-After` value (in seconds) in the block reason
- Log every blocked intent in the audit trail

A compliant implementation MAY configure stricter per-deployment limits. It MUST NOT raise the `emergency` limit.

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
  "intent": "notify",
  "context": {
    "trigger": "fridge_malfunction",
    "device_id": "fridge-kitchen-01",
    "message": "The refrigerator has detected a malfunction. Food may be at risk."
  },
  "urgency": "alert"
}
```

> **Note:** `/v1/intent` (without `/async`) is also accepted and redirects via HTTP 308 to `/v1/intent/async`. Clients that do not follow redirects should use `/v1/intent/async` directly.

**Polling with partial progress.** `POST /v1/intent/async` returns an `intent_id`
immediately; clients poll `GET /v1/intent/{intent_id}`. While the intent is still
executing, the poll returns `status: "pending"` **with a `partial` block** reporting the
actions that have ALREADY completed:

```
GET /v1/intent/{intent_id}   →   (while executing)

{
  "intent_id": "int-...",
  "status": "pending",
  "partial": {
    "actions_completed": 8,
    "actions_planned": null,
    "results": [ {"device_id": "siren-1", "action": "alarm", "success": true}, ... ]
  }
}
```

This exists so a caller whose own deadline expires before the hub finishes — an MCP client,
typically — can report what already happened ("8 actions succeeded, 2 devices still
pending") instead of an opaque "still processing". A slow or unreachable device never hides
the actions that already succeeded. Once execution finishes, the poll returns the terminal
`status` and the full result.

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

### 7.3.1 Direct device action

A caller that already knows the device and the action — an operator, or an agent
with no goal left to resolve — may act on one device without semantic
resolution:

```
POST /v1/device/action

{ "device_id": "lamp-hall", "action": "turn_on", "params": {}, "urgency": "info" }
```

Skipping RESOLUTION does not mean skipping the protocol. A direct action is
still governed:

- **Policy applies.** The hub evaluates it under the reserved intent class
  `direct_control`, so a deployment constrains direct control exactly as it
  constrains any intent — for example an exclusion policy listing
  `"intent_classes": ["direct_control"]`. An action a policy forbids returns
  **403** naming the deciding policy.
- **It is always audited.** Executed actions append `direct_action_executed`;
  blocked ones append `direct_action_blocked` with the deciding policy and its
  reason. Both carry `source: "direct_action_endpoint"`, so an auditor can
  distinguish an operator's action from one the system decided on its own.
- `urgency` is optional and defaults to `info`. A direct action carries no goal
  from which urgency could be inferred, and `info` never triggers the emergency
  bypasses some policies grant.

An endpoint that skipped policy and audit would be a "mode without the
protocol", which §"On adapter-side fallback" of DESIGN-PRINCIPLES rejects for
the protocol as a whole.

### 7.4 Device heartbeat (device → Hub)

The hub tracks device health from two independent signals. **Passive** health
comes from the execution path: an action that completes marks a device
reachable, an action that times out marks it unreachable (for
`DOSYNC_UNREACHABLE_TTL`). **Active pull** is an optional periodic lightweight
probe. Both require the hub to be able to *reach* the device.

A **heartbeat** is the **active push** signal, for devices the hub cannot poll —
behind NAT, sleeping on a schedule, or on networks that forbid inbound
connections to the device. Such a device asserts its own liveness by reaching
out:

```
POST /v1/heartbeat  (device → Hub)

{
  "device_id": "sensor-remote-04",
  "report": {                     // OPTIONAL, free-form self-report
    "battery_pct": 82,
    "rssi": -67,
    "firmware": "2.1.4"
  }
}
```

The optional `report` is bounded: at most **32 keys** and **4096 bytes** serialized.
"The hub takes no position on the contents" does not mean unbounded input — a report is
telemetry about one device, not a data channel. Reports exceeding either bound are rejected
with `422`.

A heartbeat is **positive signal only**: it marks the device reachable and
records `last_heartbeat`, and it never marks a device unreachable.

**Cause attribution for unreachable devices.** A command timeout on a UDP device
(e.g. WiZ) is identical whether the device lost power or lost network — the
transport carries no ACK to separate them. Rather than guess, `GET
/v1/health/reachability` cross-references the heartbeat signal and reports, per
unreachable device, a cause with its evidence and confidence:
`network_or_app` (heartbeat within the last ~90 s but unresponsive to actions —
alive just now, so not a power loss), `likely_powered_off` (long heartbeat
silence and unresponsive), or `indeterminate` (never heartbeat'd, so power and
network cannot be distinguished — stated plainly rather than guessed). The
attribution never claims certainty the transport cannot provide. A device that
stops sending heartbeats is simply one the hub has not heard from — weaker
evidence than an action timing out, so it does not by itself produce an
"unreachable" verdict. The optional `report` is stored verbatim and surfaced in
`GET /v1/health/devices/{id}`; the hub takes no position on its contents — it is
the device's own word about itself. Unknown devices are rejected with `404`: a
heartbeat is an assertion of identity and must come from a registered device.

### 7.5 Independent observation (`verify_with`)

`ActionResult.success` answers one question: **did the device accept the command?**
It does not answer whether the physical effect occurred. A lock can report
"locked" because its firmware received the command while the bolt never moved.

An action MAY declare an optional `verify_with` binding. After the action is
accepted, the hub reads the named sensor and compares it against the expected
reading, producing a `verification` result **separate from** `success`:

```
DeviceAction.verify_with = {
  "sensor_id":        "door-sensor:bolt",   // "device_id:sensor" or "device_id"
  "expected_reading": "locked",
  "deadline_s":       5.0
}
```

**Where it is declared.** The manifest may declare an actuator's natural pairing
(a lock's own bolt sensor). The intent context may add or override a
cross-device binding — the common real case, where the lock and the sensor that
confirms it come from different vendors — and the intent wins, because a
manufacturer cannot know sensors it does not ship with.

**States.** `verification.status` is one of:

| Status | Meaning |
|---|---|
| `verified` | the sensor agreed with `expected_reading` within the deadline |
| `contradicted` | the device accepted the command but the sensor DISAGREES |
| `unverifiable` | the verifying sensor itself did not answer — we could not look |
| `unverified` | no check ran (no binding declared) |

`unverifiable` is deliberately distinct from `contradicted`: the world did not
disagree; the hub simply could not observe. The result also records
`independence` — `independent_device` when the sensor lives on a different
device than the actuator, `same_device` otherwise — because the same firmware
reporting twice is weaker evidence than a genuinely independent observation.

**Behavior on contradiction.** The protocol REPORTS and does NOT act. A
`contradicted` (or `unverifiable`) result appends a first-class
`action_contradicted` / `action_unverifiable` entry to the tamper-evident audit
chain carrying the expected value, the observed value, the sensor, and the
independence grade. There is **no automatic retry and no automatic escalation**:
retrying an action whose effect was contradicted can worsen a physical state the
protocol cannot see, and a discrepancy is not an unsatisfiable emergency. What to
do about it is deployment-declared policy, never protocol-automatic — the same
position that governs compensation, which does not transfer cleanly from
transactions to the physical world.

**Opt-in.** An action with no `verify_with` is unchanged in every respect:
`verification` is absent and `success` means exactly what it always meant.

---

### 7.5.1 Verification from pushed readings

A verifying sensor is normally POLLED after the action. Push-only transports
(MQTT, GPIO) cannot be polled, so such a sensor could never verify anything and
a conforming hub returned `unverifiable`.

A binding MAY declare `accept_cached_within_s`, allowing a reading the device
PUSHED to serve as evidence. Two conditions apply and both are normative:

- The reading MUST have arrived **after the action was dispatched**. A reading
  that predates dispatch describes the state before the action and confirms
  nothing, however recent it is. Implementations MUST compare against the
  dispatch time, not against the current clock.
- The reading MUST be within the declared window.

**There is no default window, and implementations MUST NOT invent one.** No
single value is correct across devices: an ambient thermometer reporting every
five minutes is fine; a door sensor reporting every five minutes cannot confirm
a lock. Absent a declaration, the result is `unverifiable`, as before.

`VerificationResult.evidence` records how the observation was obtained —
`polled` or `pushed` — because `verified` MUST NOT mean two different things. A
polled reading is causally posterior to the action by construction; a pushed one
is weaker evidence, and an auditor has to be able to tell them apart.

`no_change_reported` is returned when a binding opted in and no qualifying
reading exists. It is distinct from `unverifiable`: a sensor that publishes on
change and stayed silent because nothing changed is healthy, and reporting it as
unverifiable sends an operator after a sensor that is working.

### 7.6 Audit chain integrity

Every action the hub takes is appended to a SHA-256 hash chain: each entry
carries the previous entry's hash and a monotonic `seq`, both inside the hashed
content. Altering or inserting a record breaks every link after it.

Hash links alone cannot detect a record removed from the END — every surviving
link of a truncated chain is intact. Two further layers close that:

- The latest `(seq, hash)` is recorded in a separate table as entries are
  appended, so removing rows from the log contradicts a record the removal did
  not touch.
- `audit-checkpoint` emits a **signed** statement of the chain head, intended to
  be stored off the hub. Verification against it detects a history rewritten
  wholesale — which no purely local check can, since an adversary with full
  database access controls every record such a check would consult.

**Normative:** a conforming hub MUST be able to emit a signed checkpoint of its
chain head, and MUST be able to verify a chain against one. The checkpoint
document format and the meaning of the verification are part of this
specification.

**Who this is for.** DoSync's audit chain serves two different needs that look
alike. Where the operator is the only interested party — a home, a small shop —
the chain is a LOG: it answers "what did the system do", and nobody will ask for
proof it was not edited. Where a regulator, an insurer or a second party can
ask, the same chain must function as EVIDENCE, which requires exported
checkpoints and a routine behind them.

`DOSYNC_ASSURANCE` declares which. It defaults to `standard`, and in that mode a
hub MUST NOT warn about missing checkpoint export: warning a household about an
adversary who controls the host means warning them about themselves, and a
warning that cannot be acted on teaches operators to ignore the ones that can.
`regulated` turns those warnings on. **Nothing in this section is required of a
`standard` deployment** — a home installation that configures none of it is
conforming and loses nothing it needed.

**Normative default:** a hub SHOULD generate checkpoints automatically. The
RECOMMENDED interval is **86400 seconds (daily)**, configurable through
`DOSYNC_CHECKPOINT_INTERVAL`; `0` disables generation. A deployment is free to
choose otherwise, but the default is not "none": a guarantee that requires
opt-in is one most installations will not have, and this specification already
assigns defaults to comparable risk parameters (`DOSYNC_UNREACHABLE_TTL` 1800s,
`DOSYNC_INTENT_TIMEOUT` 5000/10000ms).

Frequency is a genuine trade-off, because **the interval IS the window an
attacker could still edit**: events after the last checkpoint are not covered by
it. Daily is chosen as a default because it bounds that window at one day
without meaningful cost — signing is roughly 1.4 s of CPU per checkpoint on
modest hardware, negligible once a day and wasteful once a minute.

**Normative configuration point.** A hub MUST provide two settings and MUST warn
when neither is present:

- `DOSYNC_CHECKPOINT_EXPORT_DIR` — a location the hub copies each checkpoint to
  (push).
- `DOSYNC_CHECKPOINT_EXPORT_EXTERNAL` — a declaration that something outside
  collects checkpoints from the hub (pull).

Neither has a default, because no destination is universally correct. What is
standardised is that **silence about export is not permitted**: with neither set,
the hub MUST warn at startup and on every checkpoint that the chain's
tamper-evidence is incomplete.

The second setting exists because **pull is the stronger arrangement and looks
identical to a misconfiguration**: in it, `DOSYNC_CHECKPOINT_EXPORT_DIR` is
correctly unset, since pointing it at a mount the hub can write to would be a
downgrade. A warning that fires loudest at the best configuration teaches
operators to ignore warnings, so the protocol lets the operator say which case
this is rather than inferring it wrongly.

How much an export buys depends on the destination, and implementations SHOULD
NOT blur the difference:

| Destination | What it protects against |
|---|---|
| Unset | Nothing beyond local corruption |
| A directory this hub can write to (typically a network mount) | Loss of the local database; a remote filesystem keeping snapshots may hold history the hub cannot reach — but root here can usually delete there |
| Pull-based transfer, the hub holding no credentials to the far side | A host-level adversary. Only here is "the hub cannot reach it" literally true |

**Not normative:** the destination itself, and how long checkpoints are retained.

Consequently, conformance testing can verify that the MECHANISM works; it cannot
verify that an operator uses it. Implementations MUST NOT describe the chain as
proof against an adversary who controls the host unless checkpoints are in fact
exported beyond that host's reach. The full attacker model, including what
remains undetectable, is in `docs/AUDIT-THREAT-MODEL.md`.

### 7.7 Access control

A hub MUST support bearer-token authentication and MUST allow an operator to
manage it without shell access — `GET/POST /v1/auth/mode` and
`POST /v1/auth/token`. Requiring a terminal to change a password puts the
obstacle in front of exactly the operator least able to clear it.

**Normative:**

- A token chosen by the operator MUST be accepted, subject to a minimum length
  (12 characters in this implementation). Tokens are verified without rate
  limiting, so short values are guessed offline at full speed.
- Disabling authentication MUST require explicit confirmation, and MUST append
  to the audit chain. So MUST creating a token. The token VALUE MUST NOT be
  written to the chain.
- Where an environment variable and a stored setting disagree, the environment
  MUST win and the hub MUST say so rather than silently ignoring the request.
- With nothing configured, authentication MUST default to required.

**Not normative:** whether a given deployment requires a token at all. A hub on
a private network behind a router, unreachable from outside it, may legitimately
run open; the protocol provides the switch and records its use rather than
imposing one threat model on every installation.

## 7.8 Audit event types

Every entry in the audit chain carries a `type`. A conforming hub emits the
types below with these meanings; an implementation MAY emit additional types,
and MUST NOT reuse one of these names for a different meaning.

This table exists because the chain's value is its legibility to someone who did
not write the hub. An operator whose deployment records `device_quarantined` has
to be able to look it up, and a second implementation has to know what to emit
for the same situation. A chain of event names only its author understands is a
log, not evidence.

### Devices

| Type | Meaning |
|---|---|
| `device_registered` | A device declared its capabilities and was accepted |
| `device_updated` | An existing device re-registered with a changed manifest |
| `device_unregistered` | A device was removed from the registry |
| `device_renamed` | A device's display name changed; capabilities untouched |
| `device_adopted` | A scanned candidate was approved and named by an operator |
| `devices_auto_adopted` | Devices registered by an unattended scan (`approved_by_operator: false`) |
| `device_quarantined` | A device is registered but excluded from intents, with a reason |
| `device_unquarantined` | A quarantined device returned to service |
| `device_capability_anomaly` | Capabilities changed without a firmware version change — may indicate compromise |
| `device_event` | A device reported an event of its own |

### Intents and actions

| Type | Meaning |
|---|---|
| `intent_executed` | An intent was resolved to a plan and dispatched |
| `intent_pending_confirmation` | An intent requires operator confirmation before dispatch |
| `emergency_unsatisfiable` | An emergency intent resolved to no capable device |
| `phase_executed` | One phase of a multi-phase intent completed |
| `direct_action_executed` | A single action on a named device, bypassing resolution but not policy |
| `direct_action_blocked` | Such an action refused by deployment policy, naming the policy |
| `action_rejected_invalid_params` | An action refused because its parameters failed validation |
| `concurrent_same_rank_claims` | Two actions of equal urgency contended for one device; the later determines final state |

### Policy and access

| Type | Meaning |
|---|---|
| `policy_modified` | A policy altered a plan, with the SHA-256 of the policy file that decided |
| `auth_mode_changed` | The token requirement was turned on or off |
| `auth_token_created` | An API token was created (never the token itself) |
| `third_party_adapter_loaded` | An adapter from an installed third-party package was loaded |

### Composite operations

| Type | Meaning |
|---|---|
| `composite_started` | A multi-step operation began |
| `composite_finished` | It completed, with its outcome |
| `composite_rejected` | It was refused before starting |
| `composite_step_blocked` | One step was blocked while the operation continued |
| `composite_unknown_kind` | A composition kind the hub does not implement |
| `operation_created` | A long-running operation was created |
| `operation_transition` | Such an operation changed state |
| `operation_telemetry` | Telemetry recorded against a running operation |

### Environment

| Type | Meaning |
|---|---|
| `presence_updated` | Occupancy changed, with the signal that decided |
| `profile_loaded` | A deployment profile was loaded |
| `audit_archived` | Chain entries were moved to a segment file, binding its hash |

**Normative:** entries MUST NOT contain secrets. A token, a key or a password
belongs nowhere in the chain — it is readable by anyone who can read the chain.

## 7.9 Endpoint summary

The complete HTTP surface of a conforming hub. Sections above define semantics;
this exists so an implementer can see the whole surface at once.

A conforming implementation MUST expose the endpoints marked required below and
MAY omit the optional ones; it MUST NOT expose an endpoint under a `/v1/` path
with semantics other than those given here. How a project keeps this table
honest is its own business — the reference implementation fails a test when its
server grows past this list.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/status` | Hub identity, version, protocol, integrity |
| GET | `/v1/adapters` | Technologies this hub speaks, and on what basis each ships |
| POST | `/v1/devices/register` | A device declares its capabilities |
| GET | `/v1/devices` | Inventory, including quarantined devices |
| GET | `/v1/devices/{device_id}` | One device's manifest |
| PATCH | `/v1/devices/{device_id}` | Change presentation fields only — never capabilities |
| DELETE | `/v1/devices/{device_id}` | Remove a device from the registry |
| GET | `/v1/devices/{device_id}/describe` | Plain text: what the hub observed about a device, the tag vocabulary and the adapter format — enough to declare what it can do. Returns text and sends nothing anywhere |
| POST | `/v1/adapters/verify` | Tries a drafted adapter's `GET`/`HEAD`/`OPTIONS` requests against the device and reports what answered. Tries only what the draft declares — never probes for an API of its own, never proposes a replacement. Returns what it could not test, and why |
| POST | `/v1/devices/provision` | Pre-authorise a device id and issue its token |
| GET | `/v1/devices/provisioned` | Which device ids are provisioned |
| DELETE | `/v1/devices/{device_id}/token` | Revoke a device token |
| POST | `/v1/devices/verify-cert` | Validate a device certificate |
| POST | `/v1/intent` | Fire an intent synchronously |
| POST | `/v1/intent/async` | Fire an intent, returning immediately |
| GET | `/v1/intents/{intent_class}/explain` | Which devices were evaluated, included, and why |
| GET | `/v1/intent-classes` | Intent classes this deployment has registered |
| POST | `/v1/intent-classes` | Register a domain-specific intent class |
| DELETE | `/v1/intent-classes/{name}` | Remove one |
| POST | `/v1/device/action` | One action on one named device — policy-evaluated and audited |
| GET | `/v1/discovery/scan` | List candidates on every searchable transport; registers nothing |
| POST | `/v1/discovery/adopt` | Register one candidate under a name the operator chose |
| POST | `/v1/discovery/run` | Scan and register unattended (recorded as not operator-approved) |
| POST | `/v1/heartbeat` | Device-initiated liveness over an authenticated channel |
| POST | `/v1/heartbeat/signed` | Liveness signed with the device token, for hardware that cannot do TLS (§7.10) |
| GET | `/v1/health/devices` | Per-device health, including how each last reported |
| GET | `/v1/health/reachability` | Cause attribution for unreachable devices |
| GET | `/v1/audit` | The audit chain |
| GET | `/v1/auth/mode` | Whether a token is required, and which source decided |
| POST | `/v1/auth/mode` | Change that, confirmed and recorded |
| POST | `/v1/auth/token` | Set a token of the operator's choosing |
| GET | `/v1/keys` | API keys, as previews only |
| POST | `/v1/keys` | Create an API key |
| GET | `/v1/presence` | Current occupancy |
| POST | `/v1/presence` | Report an occupancy signal |
| GET | `/v1/operations` | Long-running operations |
| GET | `/v1/operations/{operation_id}` | One operation |
| GET | `/v1/hub/peers` | Known peer hubs |
| POST | `/v1/hub/promote` | Promote a secondary hub |

## 7.10 Signed heartbeats

A hub MAY accept heartbeats authenticated by signature rather than by transport
security, for hardware that cannot perform a TLS handshake. It MUST be disabled
by default.

The device signs `device_id \n timestamp \n report_json` with HMAC-SHA256, keyed
on the SHA-256 of its provisioning token — a value the device derives and the hub
already stores, so the token itself never has to be recoverable.

A conforming implementation MUST:

- reject a timestamp outside a narrow window of the hub's clock, and say that the
  clock is the problem rather than the key;
- reject a signature already accepted within that window. A heartbeat is a
  positive signal, so the attack it invites is REPLAY: repeating a captured
  message keeps a failed device reporting healthy, blinding failure detection;
- record the channel on the device (`report_channel`), because a device reporting
  over an unencrypted channel is in a different position from one on mTLS and the
  two MUST NOT be indistinguishable to an operator.

This channel provides message authenticity and replay resistance. It does NOT
provide confidentiality: `device_id`, timestamp and report travel readable.
Implementations MUST NOT describe it as secure transport.

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

DoSync maintains two independent versioned surfaces:

| Surface | Current | Exposed as |
|---|---|---|
| **Protocol version** | `0.4` | `dosync/0.4` in `protocol` field of `/v1/status`; `X-DoSync-Protocol-Version` response header |
| **REST API version** | `1` | `/v1/` URL prefix; `X-DoSync-API-Version` response header |

Every HTTP response from the hub includes both headers:

```
X-DoSync-Protocol-Version: 0.1
X-DoSync-API-Version: 1
```

Clients SHOULD read these headers to detect the version in use rather than parsing the URL.

### 10.1 Version semantics

**Protocol version** tracks changes to the semantic protocol — the intent format, the CapabilityManifest schema, urgency level semantics, and the core data model. A change to the protocol version signals that clients may need to update their understanding of what the hub communicates, not just how to call it.

**API version** tracks changes to the HTTP interface — endpoints, request/response shapes, authentication. A new API version introduces a new URL prefix (`/v2/`) while the previous version remains active during the deprecation window.

### 10.2 Backward compatibility commitment

A compliant hub implementation MUST NOT introduce breaking changes within the same API version. The following are **non-breaking** and may be deployed at any time without incrementing the API version:

- Adding new optional fields to response bodies
- Adding new optional query parameters
- Adding new endpoints
- Adding new optional intent classes via `POST /v1/intent-classes`
- Adding new urgency levels (if they do not change the semantics of existing ones)
- Changing default values for optional parameters (with documented notice)

The following are **breaking changes** and require incrementing the API version:

- Removing fields from request or response bodies
- Changing the type of an existing field
- Removing endpoints
- Adding required fields to request bodies
- Changing the semantics of existing fields
- Removing or renaming urgency levels

### 10.3 Deprecation policy

When a feature is deprecated, the hub MUST:

1. Add the `Deprecation` header (RFC 8594) to responses from the deprecated endpoint, with the deprecation date as the value
2. Add the `Sunset` header (RFC 8594) with the planned removal date
3. Document the replacement in the API documentation

```
Deprecation: Sat, 01 Jan 2028 00:00:00 GMT
Sunset: Sat, 01 Jul 2028 00:00:00 GMT
```

Deprecated features remain operational for a minimum of **6 months** after the deprecation notice before removal. The hub MUST continue serving the previous API version (`/v1/`) for at least 6 months after `/v2/` is available.

### 10.4 Protocol versioning

Protocol versions follow `MAJOR.MINOR` semantics:

- **MINOR** increment (e.g. `0.1` → `0.2`): additive changes. New optional fields, new intent classes in the universal set, new urgency levels. Backward compatible.
- **MAJOR** increment (e.g. `0.x` → `1.0`): breaking changes to the core data model, the intent format, or the CapabilityManifest schema. Clients may require updates.

The transition from `v0.x` to `v1.0` marks the protocol's stability milestone — after `v1.0`, breaking changes require a MAJOR increment.

---

## 11. Multi-Hub Architecture

### 11.1 Problem statement

A single DoSync hub is a single point of failure. If the hub process crashes or the host machine fails, all connected devices lose their semantic coordinator. For safety-critical deployments (industrial facilities, buildings with emergency systems, and other regulated environments), this is unacceptable.

### 11.2 Scope and constraints

This section defines the **requirements and constraints** for a compliant multi-hub implementation. It does not prescribe a specific consensus algorithm or synchronization protocol. Implementations are free to choose the appropriate mechanism (Raft, CRDTs, primary-standby replication, etc.) provided they satisfy the requirements below.

### 11.3 Hub discovery

The DoSync hub announces its presence via the existing UDP broadcast mechanism (Layer 1 — Transport). In a multi-hub deployment, each hub announces its own endpoint. Devices and clients MUST be configurable with multiple hub URLs as fallback targets.

The heartbeat endpoint (`GET /v1/hub/heartbeat`) MUST be used by standby hubs and monitoring systems to determine whether the primary hub is available. The endpoint returns:

```json
{
  "hub_id":           "8f16f011beab295a",
  "status":           "healthy",
  "protocol_version": "0.4",
  "api_version":      "1",
  "timestamp":        "2026-06-06T15:02:22.556184+00:00",
  "uptime_seconds":   1842,
  "devices":          32,
  "role":             "primary"
}
```

**Recommended polling interval:** 5 seconds. A hub SHOULD be considered unavailable after 3 consecutive missed heartbeats (15 seconds).

### 11.4 Required guarantees

A multi-hub implementation MUST satisfy the following:

**1. Intent execution consistency**
No intent MAY be executed by two hubs simultaneously for the same device. Concurrent execution creates conflicting device states and produces duplicate audit log entries that undermine the accountability model.

**2. Audit log integrity**
The SHA-256 tamper-evident audit log chain MUST remain consistent across hub instances. If two hubs write to the same audit log independently, the chain will diverge and `verify()` will return False. Implementations MUST choose one of:
- Only one hub writes to the audit log at any time (primary-standby)
- Audit logs are merged with conflict resolution before verification
- A distributed log with total ordering (e.g., consensus-based append)

**3. Device registry consistency**
The capability manifest registry MUST be eventually consistent across hubs. A device registered with hub A MUST be discoverable by hub B within a configurable convergence window.

**4. Emergency bypass must not degrade**
`urgency=emergency` intents MUST never be blocked by hub coordination overhead. If a hub cannot reach its peers (network partition), it MUST still execute emergency intents immediately and reconcile state after the partition heals.

**5. No split-brain silent corruption**
If two hubs cannot determine which is authoritative (split-brain scenario), they MUST NOT both execute non-emergency intents silently. Acceptable behaviors: one hub blocks non-emergency execution, or clients are returned a `503 Hub Unavailable` until consensus is restored.

### 11.5 Hub roles

The `DOSYNC_HUB_ROLE` environment variable declares the hub's intended role. Valid values:

| Value | Meaning |
|---|---|
| `primary` (default) | Hub serves all requests normally |
| `standby` | Hub monitors the primary and activates only on primary failure |

The `role` field is returned in `GET /v1/hub/heartbeat`. Clients MAY use this field to implement failover logic.

### 11.6 Known failure modes

Implementors MUST be aware of the following distributed systems failure modes and mitigate them explicitly:

**Split-brain**: Both primary and standby believe they are the active hub. Mitigation: fencing tokens, STONITH (Shoot The Other Node In The Head), or quorum-based election.

**False failover**: The standby promotes itself due to a network partition, while the primary is still healthy. Mitigation: require quorum acknowledgment before promotion.

**State divergence**: Devices registered with the primary are not yet replicated to the standby at failover time. Mitigation: synchronous replication for registry mutations, or accept a reconciliation window.

**Audit log corruption**: two hub processes writing to one store simultaneously. Mitigation: a hub MUST NOT share its audit store with another hub process. Recovery from a broken chain requires an administrative reset, which MUST itself be recorded — the reference implementation provides `manage.py db audit-reset`.

### 11.7 Implementation guidance

For home and small office deployments, the simplest compliant implementation is **active-passive with a shared external database**:

1. Primary hub writes to a network-accessible database (PostgreSQL, SQLite WAL on NFS with proper locking)
2. Standby hub polls `GET /v1/hub/heartbeat` every 5 seconds
3. On 3 consecutive failures, standby promotes itself and begins serving requests
4. On primary recovery, manual operator action is required to demote the standby

For enterprise deployments, a Raft-based consensus approach (etcd, Consul) is recommended to eliminate split-brain scenarios.

### 11.8 Certification

Multi-hub support is **optional** for Basic and Standard tier certification. A hub that supports multi-hub SHOULD declare it in `GET /v1/hub/heartbeat` via an additional `multi_hub_capable: true` field.

Emergency tier certification requires that the hub maintains emergency intent execution even during hub failover scenarios.

---


---

## 12. Error Behaviors and Operational Boundaries

This section defines the required behavior of a conforming hub in error conditions, and the operational boundaries within which the protocol has been validated.

### 12.1 Error behaviors

#### Intent timeout

If intent execution does not complete within `DOSYNC_INTENT_TIMEOUT` milliseconds (default: 5000ms for emergency, 10000ms for other urgencies), the hub MUST:

1. Cancel all pending adapter calls for the intent
2. Return an `IntentResult` with `status: "partial"` or `status: "failed"` reflecting which actions completed before timeout
3. Log the timeout event in the tamper-evident audit log with `type: "intent_timeout"`
4. Mark devices that timed out as unreachable for `DOSYNC_UNREACHABLE_TTL` seconds (default: 1800s)

The client receives a response immediately — the intent executes asynchronously via `POST /v1/intent/async`. Timeout is enforced server-side.

#### Policy block

When the policy engine blocks an intent, the hub MUST:

1. Return HTTP 429 (rate limit) or HTTP 403 (policy block) with a machine-readable reason
2. Log the block in the audit log with `type: "intent_blocked"` and `policy: "<policy_name>"`
3. NOT execute any device actions

The client MUST NOT retry a blocked intent without addressing the block condition.

#### Device registration failure

If `POST /v1/devices/register` fails validation, the hub MUST return HTTP 422 with a structured error body. The hub MUST NOT partially register the device. Registration is atomic.

If a device re-registers with changed capabilities (see §5.3), the hub classifies the change and emits the appropriate audit entry. Registration always succeeds — anomaly detection is an audit concern, not a registration failure.

#### Resolver empty plan

If the capability-based resolver finds no devices relevant to an intent, the hub MUST return an `IntentResult` with `status: "failed"`, `actions: 0`, and `success: false`. This is not an error condition — it indicates the registry has no devices capable of responding to the intent. The hub MUST NOT raise an exception.

#### Device unreachable

When an adapter returns a timeout or connection failure for a device, the hub MUST:

1. Mark the device as unreachable in the `StateAwareResolver` cache
2. Exclude the device from subsequent action plans for `DOSYNC_UNREACHABLE_TTL` seconds
3. Log the failure in the `IntentResult.results` array with `success: false` and the error message
4. Continue executing remaining actions in the plan (unless `failure_policy: "abort"` is set)

Unreachable devices automatically re-enter the resolver's consideration after the TTL expires. No operator action is required.

#### Audit log integrity failure

If `GET /v1/hub/heartbeat` returns `status: "degraded"`, the audit log chain is broken. The hub MUST continue operating but MUST report degraded status on every heartbeat until the chain is repaired. Repair is administrative and implementation-specific; the reference implementation provides `manage.py db audit-reset`.

---

### 12.2 Operational boundaries

These are the validated operational limits of the reference implementation. Conforming implementations SHOULD document their own limits.

| Parameter | Home deployment | Industrial (guideline) | Notes |
|---|---|---|---|
| Devices per hub | ≤200 | ≤1000 | Benchmark validated to 5000 within 500ms |
| Intents per minute (total) | ≤50 recommended | ≤200 | `IntentRateLimitPolicy` default: 60/min/source |
| Commands per device per minute | ≤20 | ≤60 | `DeviceActuatorRateLimitPolicy` default |
| Resolver latency (p99) | <0.5ms | <0.5ms | Reference impl: 0.11ms at 38 real devices |
| Concurrent WebSocket clients | ≤10 | — | Tested in reference deployment |
| SQLite DB practical limit | ~1GB | — | Performance degrades above this |

The resolver scales to 5000+ devices within the 500ms intent timeout at p99. Beyond 5000 devices, tag-based indexing (`O(1)` lookup) is recommended over linear scan.

---

### 12.3 Safety-critical deployment constraints

DoSync is NOT certified for safety-critical applications under IEC 61508, IEC 62304, or equivalent functional safety standards. Deployments in environments where failure could result in injury, death, or significant property damage MUST NOT rely on DoSync as the sole safety mechanism.

**Prohibited uses (without additional safety layers):**
- Primary control of medical devices or life support systems
- Single point of control for fire suppression or emergency egress
- Industrial machinery with SIL (Safety Integrity Level) requirements

**Appropriate uses:**
- Coordination of non-critical peripherals (lighting, access, climate, comfort) alongside certified systems
- Secondary notification layer alongside certified safety systems
- Home environments where failure results in inconvenience, not injury

The `emergency` urgency level bypasses policy constraints for speed — it does not imply the hub meets any safety certification standard. Emergency intents are logged with full audit trail; they are not validated against external safety standards.

See `DESIGN-PRINCIPLES.md` for the full reasoning behind these constraints.

---

### 12.4 Relationship to existing IoT standards

DoSync does not replace Matter, Zigbee, Z-Wave, or Home Assistant. It operates at a different layer of the stack.

**The positioning:** existing protocols solve *device interoperability* — ensuring that a lock from Brand A can be controlled by an app from Brand B. DoSync solves *agent-to-environment coordination* — ensuring that an AI system can express a goal and the environment responds, regardless of which protocol the individual devices use.

DoSync's Layer 1 (HAL) explicitly abstracts over Matter, Zigbee, Z-Wave, Thread, and Ethernet. A DoSync hub can sit on top of a Matter fabric and translate semantic intents into Matter commands — the protocols are complementary, not competitive.

**"Will Matter eventually add semantic intent?"** Matter's design priority is cross-brand device interoperability at the command level, backed by major consumer electronics manufacturers. Adding a semantic layer to Matter would require consensus across Apple, Google, Amazon, Samsung, and 280+ members — a standards process measured in years. DoSync's open, self-certifiable model allows deployments today without waiting for that consensus.

---

## Appendix A — Example scenarios

### A.1 Fall detection emergency

```
[Camera detects fall] → event: ensure_safety / emergency
[Hub receives event]
[Capability-based resolver activates]:
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
[Capability-based resolver activates]:
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

---

## Appendix A — Recommended metrics (non-normative)

Observability is an **operational feature of an implementation, not part of the
protocol**. A hub that exposes no metrics is still conforming. However, so that
independent implementations are comparable and operable with the same dashboards,
implementations that expose Prometheus-format metrics SHOULD use these names:

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `dosync_intents_total` | counter | `intent_class`, `urgency`, `outcome` | Intents received (`accepted` / `rejected`). Rejected intents MUST be counted under `intent_class="_invalid"` — never place unbounded user input in a label value. |
| `dosync_intent_executions_total` | counter | `outcome` | Completed executions (`success` / `partial` / `failed`) |
| `dosync_intent_actions_total` | counter | `result` | Device actions dispatched (`success` / `failed` / `superseded`) |
| `dosync_policy_decisions_total` | counter | `decision` | Policy engine decisions (`allow` / `block` / `confirm` / `modify`) |
| `dosync_emergency_intents_total` | counter | — | Intents accepted with emergency urgency |
| `dosync_device_preemptions_total` | counter | — | Lower-urgency actions superseded by an emergency device claim |
| `dosync_devices_registered` | gauge | — | Devices currently in the capability registry |
| `dosync_devices_emergency_capable` | gauge | — | Devices declaring `emergency_capable=true` |
| `dosync_audit_entries` | gauge | — | Entries in the tamper-evident audit log |
| `dosync_audit_integrity` | gauge | — | 1 if the SHA-256 chain verifies, 0 if broken |
| `dosync_ws_connections` | gauge | — | Active WebSocket connections |
| `dosync_hub_uptime_seconds` | gauge | — | Seconds since process start |
| `dosync_device_success_rate` | gauge | `device_id` | Per-device success rate over recent executions. `device_id` is acceptable here only because device health is a bounded, low-volume gauge; implementations MUST NOT use `device_id` as a label on high-volume counters. |

Latency histograms (`dosync_intent_resolution_seconds`, `dosync_action_execution_seconds`)
are anticipated in a future revision of this appendix once the reference
implementation ships them.

The reference implementation serves these at `GET /metrics` behind the same
authentication as the rest of the API (unlike `/v1/status`, which stays public as
a minimal health check for monitors and standby hubs).

---

*Revision (2026-07-03): Layers 1–2 reconciled with the reference implementation — transport-agnosticism stated as a normative design principle; native radio bindings, constrained-transport binary framing, native-transport onboarding, and fine-grained permission scopes relocated to explicitly non-normative "roadmap" notes; onboarding and permission model rewritten to the implemented REST + local-CA flow. No protocol behavior changed — documentation fidelity only.*

*DoSync Protocol Specification (protocol v0.4) — DoSync Initiative — Apache 2.0 License*
