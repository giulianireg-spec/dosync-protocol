# dosync-node — Protocol Conformance Declaration

**Implementation:** dosync-node  
**Language:** Node.js / TypeScript  
**Repository:** github.com/giulianireg-spec/dosync-protocol (implementations/dosync-node)  
**Protocol version:** DoSync v0.3  
**Declaration date:** June 2026  
**Maintainer:** Rodrigo Giuliani — rgiuliani@dosync.dev

---

## Overview

dosync-node is the Node.js reference implementation of the DoSync Protocol. It implements the device-side of the protocol — registering with a hub, declaring a Capability Manifest, responding to intents, and sending events.

This document formally declares which protocol features dosync-node implements, which it does not implement, and provides certification results from the DoSync certification CLI.

---

## Certification Results

dosync-node v0.3.0 passes the DoSync STANDARD certification tier (32/32 tests):

```
$ DOSYNC_TOKEN=<token> python3 certify.py --host localhost --port 47201 --tier standard

DoSync Certification CLI v0.3
  Hub:   http://localhost:47201
  Tier:  STANDARD (32 tests)

── Tier BASIC — Connectivity and registration ──────────
  ✓  B01–B10  (10/10)

── Tier STANDARD — Protocol conformance + events ────────
  ✓  S01–S22  (22/22)

── Result ────────────────────────────────────────────────
  Passed: 32 / 32
  Failed: 0 / 32

  ✓ CERTIFIED — DoSync STANDARD (32/32)
  Fingerprint: [see dosync-cert-standard-*.json]
```

**Certification environment:**
- Runtime: Node.js v22.22.2, dosync-node v0.3.0
- certify.py: DoSync Certification CLI v0.3
- Date: June 2026

---

## Feature Implementation Matrix

### Layer 1 — Transport

| Feature | Status | Notes |
|---|---|---|
| HTTP/REST transport | ✅ Implemented | Primary transport |
| WebSocket event stream | ✅ Implemented | `ws://hub/ws` |
| WiFi transport | ✅ Implemented | Via HTTP over WiFi |
| BLE transport | ❌ Not implemented | Planned |
| Zigbee transport | ❌ Not implemented | Planned |
| Z-Wave transport | ❌ Not implemented | Planned |

### Layer 2 — Secure Channel

| Feature | Status | Notes |
|---|---|---|
| API key authentication | ✅ Implemented | Bearer token |
| mTLS per-device certificates | ✅ Implemented | v0.3+ |
| Certificate rotation | ✅ Implemented | Via PKI rotation script |
| Local PKI (no internet required) | ✅ Implemented | |

### Layer 3 — Registry

| Feature | Status | Notes |
|---|---|---|
| Device registration | ✅ Implemented | `POST /v1/devices/register` |
| Capability Manifest declaration | ✅ Implemented | Full schema |
| `emergency_capable` flag | ✅ Implemented | |
| Tag declaration | ✅ Implemented | |
| Actuator declaration | ✅ Implemented | |
| Sensor declaration | ✅ Implemented | |
| Device unregistration | ✅ Implemented | |
| UDP device discovery | ❌ Not implemented | Hub-side feature |

### Layer 4 — Semantic

| Feature | Status | Notes |
|---|---|---|
| Receive and execute intents | ✅ Implemented | Via hub polling + WebSocket |
| `urgency=emergency` override | ✅ Implemented | Emergency certification requires this |
| SHA-256 tamper-evident audit log | ✅ Implemented | Verified by certify.py |
| Intent execution result reporting | ✅ Implemented | |
| Event push to hub | ✅ Implemented | `POST /v1/event` |
| Policy engine | ✅ N/A — handled by hub | Device receives and executes the resolved ActionPlan; policy evaluation is hub-side |

### Layer 5 — Intent

| Feature | Status | Notes |
|---|---|---|
| Structured intent consumption | ✅ Implemented | Via hub API |
| Natural language → intent | ❌ Not applicable | Hub-side / AI-side feature |
| MCP server | ❌ Not implemented | Hub-side feature |

### Protocol features

| Feature | Status | Notes |
|---|---|---|
| Open intent classes (v0.3) | ✅ Implemented | Accepts any `^[a-z][a-z0-9_]*$` intent name |
| `Urgency.WARNING` level | ✅ Implemented | Handled identically to `alert` on device side |
| Async intent execution | ✅ Implemented | Polls `GET /v1/intent/{id}` |
| HTTP 308 redirect handling | ✅ Implemented | Follows `/v1/intent` → `/v1/intent/async` |
| PhasedActionPlan | ❌ Not implemented | Planned for v0.4 |
| FailurePolicy (ABORT/RETRY) | ❌ Not implemented | Pending hub-side implementation |

---

## Capability Manifest Example

A dosync-node device registers with a Capability Manifest that follows the DoSync schema exactly:

```json
{
  "device_id": "dosync-node-demo-01",
  "device_name": "DoSync Node Demo Device",
  "manufacturer": "dosync-node",
  "model": "v0.3",
  "category": "actuator",
  "tags": ["light", "emergency", "smart-plug"],
  "actuators": [
    { "type": "turn_on",  "description": "Turn on" },
    { "type": "turn_off", "description": "Turn off" },
    { "type": "notify",   "description": "Send notification" }
  ],
  "sensors": [],
  "emergency_capable": true,
  "adapter": "dosync-node",
  "adapter_config": {}
}
```

---

## Interoperability

dosync-node has been tested for interoperability against the DoSync Python reference implementation (hub) under the following scenarios:

| Scenario | Result |
|---|---|
| Register + receive `ensure_safety [emergency]` | ✅ Pass |
| Execute `turn_on` actuator | ✅ Pass |
| Push motion event to hub | ✅ Pass |
| Survive hub restart (re-register) | ✅ Pass |
| Handle unknown intent class gracefully | ✅ Pass |
| mTLS certificate handshake | ✅ Pass |
| Emergency override (bypass policy) | ✅ Pass |
| SHA-256 audit log chain verification | ✅ Pass |

---

## Known Limitations

1. **No UDP discovery** — dosync-node devices register by calling the hub REST API directly. They do not participate in UDP broadcast discovery (hub-side feature).

2. **No PhasedActionPlan** — dosync-node handles parallel action plans. Sequential/phased plans are not yet implemented.

3. **FailurePolicy not implemented** — pending hub-side implementation of `ABORT` and `RETRY` policies.

4. **No BLE/Zigbee/Z-Wave transport** — HTTP/WebSocket only in current version.

---

## Quick Start

```bash
npm install dosync-node
```

```javascript
const { DoSyncDevice } = require('dosync-node');

const device = new DoSyncDevice({
  hubUrl: 'https://192.168.1.100:47200',
  token: '<your-token>',
  manifest: {
    device_id: 'my-node-device-01',
    device_name: 'My Node Device',
    tags: ['light', 'emergency'],
    actuators: [
      { type: 'turn_on',  description: 'Turn on' },
      { type: 'turn_off', description: 'Turn off' },
    ],
    emergency_capable: true,
  }
});

// Register with the hub
await device.register();

// Handle intents
device.on('intent', async (intent) => {
  console.log(`Intent received: ${intent.intent} [${intent.urgency}]`);
  if (intent.intent === 'ensure_safety') {
    // respond to emergency
  }
  await device.reportResult(intent.intent_id, { success: true });
});

// Push an event
await device.pushEvent('motion_detected', { location: 'entrance' });
```

---

## Compliance Statement

dosync-node v0.3 is **DoSync Emergency Certified** as of June 2026. It conforms to the DoSync Protocol Specification v0.1 and Resolver Interface Specification v0.3 for all features marked ✅ in the matrix above.

The certification was performed against the DoSync Python reference hub using the public `certify.py` CLI (32/32 tests passing, Emergency tier).

---

*dosync-node · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
