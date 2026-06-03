# DoSync Protocol — Intent Classes Guide

This document explains how to define, register, and use intent classes in DoSync deployments. It is intended for system integrators, device manufacturers, and domain architects building on top of the DoSync Protocol.

---

## What is an intent class?

An intent class is the semantic label of a goal that an AI agent wants to achieve. It is the single most important piece of information in a DoSync intent — it tells the resolver what the system is trying to accomplish, which determines which devices should act and how.

```json
{
  "intent": "ensure_safety",
  "urgency": "emergency",
  "context": { "trigger": "fall_detected", "location": "bedroom" }
}
```

The intent class (`ensure_safety`) is not a command. It is not device-specific. It does not say *how* to achieve the goal — it says *what* the goal is. The resolver reads the registered Capability Manifests of all devices and builds the action plan automatically.

---

## The two layers of intent classes

### Layer 1 — Universal intents (protocol-defined)

Five intent classes are defined at the protocol level and seeded into every DoSync hub at initialization. They are valid in any physical environment regardless of domain:

| Intent class | Urgency | Description |
|---|---|---|
| `ensure_safety` | `emergency` | Safety emergency — protect people and property |
| `alert_anomaly` | `alert` | Unexpected condition detected — investigate |
| `control_access` | `alert` | Manage physical access to a space |
| `report_status` | `info` | Generate a status report of the environment |
| `notify` | `info` | Push information to any target |

These five are **protected** — they cannot be overridden or deleted via the API. They represent the invariant core that every compliant DoSync implementation must support.

### Layer 2 — Domain intents (deployment-defined)

All other intent classes are defined by the deploying organization and registered at the hub level via the API. No code changes, no hub restart, no coordination with the protocol maintainers.

```bash
POST /v1/intent-classes
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "prepare_operating_room",
  "urgency": "alert",
  "resolution_tags": ["medical", "lighting", "access", "equipment"],
  "resolution_actuators": ["turn_on", "unlock", "notify"],
  "description": "Prepare an operating room for a surgical procedure",
  "domain": "healthcare"
}
```

---

## Naming conventions

Intent class names must match the pattern `^[a-z][a-z0-9_]*$`:

- **Lowercase only** — `ensure_safety`, not `EnsureSafety` or `ENSURE_SAFETY`
- **Underscores as separators** — `line_emergency_stop`, not `line-emergency-stop` or `lineEmergencyStop`
- **Must start with a letter** — `prepare_or`, not `3d_print_complete`
- **No special characters** — no hyphens, dots, slashes, or spaces

**Naming pattern:** `verb_noun` or `noun_state`

Good names describe a goal or a state, not a command:

```
✓ prepare_operating_room     (goal)
✓ line_emergency_stop        (goal + context)
✓ guest_checked_in           (state change)
✓ cold_chain_failure         (detected state)
✓ shift_handover             (event)

✗ turn_on_lights             (command, not a goal)
✗ unlock_door_3              (too specific, device-level)
✗ doSomething                (not snake_case)
✗ 2nd_floor_alert            (starts with digit)
```

---

## Choosing the right urgency

Urgency is the only protocol-controlled value in an intent class — because it has direct safety implications:

| Urgency | Behavior | When to use |
|---|---|---|
| `emergency` | Bypasses all policy constraints. Executes immediately without confirmation. All actions logged as critical. | Life safety, fire, structural failure, medical emergency. Use sparingly — misuse degrades trust in the emergency signal. |
| `alert` | High priority. Confirmation policies may apply depending on hub configuration. | Anomalies, access control requests, conditions requiring investigation but not immediate danger. |
| `info` | Normal priority. All policies apply. | Routine operations, status updates, notifications, scheduled events. |

**The principle:** urgency should reflect the *consequence of not acting immediately*, not the *importance of the task*. Preparing an operating room is important — but failing to do it in the next 100ms is not life-threatening. Use `alert`. A cardiac arrest in the hallway requires `emergency`.

---

## Resolution tags and actuators

When a hub receives an intent, the resolver scores every registered device against the intent's resolution tags. Devices whose Capability Manifest tags overlap with the intent's resolution tags are candidates for inclusion in the action plan.

**resolution_tags** — the semantic tags the resolver uses to find relevant devices:

```json
"resolution_tags": ["medical", "lighting", "access", "equipment"]
```

Tags should match the tags in your devices' Capability Manifests. If your operating room lights have `["light", "medical", "or-equipment"]` in their manifest, the tag `medical` will match them.

**resolution_actuators** — the actuator types the resolver will invoke on matched devices:

```json
"resolution_actuators": ["turn_on", "unlock", "notify"]
```

Devices that support these actuator types score higher in the resolution algorithm.

**The relationship between tags and actuators:**

Tags determine *which devices* are included. Actuators determine *what those devices do*. A device with the `medical` tag but no `turn_on` actuator will score lower but may still be included if it has other matching actuators.

---

## Domain packages

For deployments with many intent classes, we recommend organizing them into domain packages — logical groupings that can be registered together and documented as a unit.

**Example: healthcare package**

```json
[
  {
    "name": "prepare_operating_room",
    "urgency": "alert",
    "resolution_tags": ["medical", "lighting", "access", "equipment"],
    "resolution_actuators": ["turn_on", "unlock", "notify"],
    "description": "Prepare an OR for a surgical procedure",
    "domain": "healthcare"
  },
  {
    "name": "patient_deteriorating",
    "urgency": "emergency",
    "resolution_tags": ["medical", "communication", "emergency"],
    "resolution_actuators": ["notify", "alarm", "call"],
    "description": "Patient condition deteriorating — alert medical staff",
    "domain": "healthcare"
  },
  {
    "name": "room_discharge_ready",
    "urgency": "info",
    "resolution_tags": ["medical", "lighting", "climate", "communication"],
    "resolution_actuators": ["turn_off", "set_temperature", "notify"],
    "description": "Patient discharged — prepare room for next occupant",
    "domain": "healthcare"
  }
]
```

**Example: industrial package**

```json
[
  {
    "name": "line_emergency_stop",
    "urgency": "emergency",
    "resolution_tags": ["industrial", "safety", "alarm", "actuator"],
    "resolution_actuators": ["stop", "alarm", "notify"],
    "description": "Emergency production line shutdown",
    "domain": "industrial"
  },
  {
    "name": "maintenance_window_start",
    "urgency": "alert",
    "resolution_tags": ["industrial", "access", "lighting"],
    "resolution_actuators": ["unlock", "turn_on", "notify"],
    "description": "Scheduled maintenance window beginning",
    "domain": "industrial"
  },
  {
    "name": "quality_threshold_exceeded",
    "urgency": "alert",
    "resolution_tags": ["industrial", "sensor", "communication"],
    "resolution_actuators": ["notify", "alarm"],
    "description": "Quality control threshold exceeded on production line",
    "domain": "industrial"
  }
]
```

**Example: residential package**

```json
[
  {
    "name": "morning_routine",
    "urgency": "info",
    "resolution_tags": ["light", "blinds", "appliance", "climate"],
    "resolution_actuators": ["set_brightness", "set_position", "turn_on", "set_temperature"],
    "description": "Prepare the space for the day",
    "domain": "residential"
  },
  {
    "name": "bedtime_routine",
    "urgency": "info",
    "resolution_tags": ["light", "blinds", "smart-plug", "climate"],
    "resolution_actuators": ["set_brightness", "set_position", "turn_off", "set_temperature"],
    "description": "Prepare the space for sleep",
    "domain": "residential"
  },
  {
    "name": "away_mode",
    "urgency": "info",
    "resolution_tags": ["light", "smart-plug", "camera", "alarm", "thermostat"],
    "resolution_actuators": ["turn_off", "arm", "set_temperature"],
    "description": "Secure and optimize for an empty space",
    "domain": "residential"
  }
]
```

---

## API reference

### Register an intent class

```
POST /v1/intent-classes
Authorization: Bearer <token>
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✓ | `^[a-z][a-z0-9_]*$` — lowercase, digits, underscores |
| `urgency` | string | ✓ | `emergency` \| `alert` \| `info` |
| `resolution_tags` | list[string] | ✓ | At least one tag. Must match device manifest tags. |
| `resolution_actuators` | list[string] | | Actuator types to invoke. Empty list = tag-only matching. |
| `description` | string | | Human-readable description. |
| `domain` | string | | Domain identifier (`healthcare`, `industrial`, `residential`, etc.) |

**Response 200:** intent class registered  
**Response 409:** name collides with a universal intent class  
**Response 422:** name format invalid or urgency value invalid

### List all intent classes

```
GET /v1/intent-classes
Authorization: Bearer <token>
```

Returns all registered intent classes — universal and domain-specific — with their `is_universal` flag.

### Delete an intent class

```
DELETE /v1/intent-classes/{name}
Authorization: Bearer <token>
```

**Response 200:** deleted  
**Response 404:** intent class not found  
**Response 409:** cannot delete a universal intent class

---

## Firing a registered intent class

Once registered, an intent class can be fired via the standard intent endpoint:

```bash
POST /v1/intent/async
Authorization: Bearer <token>
Content-Type: application/json

{
  "intent": "prepare_operating_room",
  "urgency": "alert",
  "context": {
    "room": "OR-3",
    "procedure": "cardiac"
  }
}
```

The resolver automatically looks up the intent class in the database, retrieves its resolution tags and actuators, and builds the action plan. No code changes required on the resolver side.

---

## Frequently asked questions

**Can I update an existing intent class?**  
Yes — POST to `/v1/intent-classes` with the same name. The registration will update the existing record (resolution tags, actuators, description). The `is_universal` flag is never modified by an update.

**Can I fire an intent class that is not registered?**  
No. The hub validates that the intent class exists in the database before executing. This is by design — unregistered intents would produce unpredictable resolver behavior.

**Should I use the same intent class name across multiple hubs?**  
Yes, if the intents represent the same goal. Using consistent names across a multi-hub deployment enables unified audit log analysis and consistent policy configuration.

**Can an AI agent register intent classes?**  
Yes — the `/v1/intent-classes` endpoint requires only a valid API token. An AI agent with appropriate permissions can register domain-specific intent classes dynamically.

**What happens if I fire `ensure_safety` and no devices match the resolution tags?**  
The resolver returns an empty action plan. No devices act. The intent is logged as executed with 0 actions. This is the correct behavior — the protocol should not fail silently or guess which devices to activate.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
