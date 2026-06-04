# DoSync Resolver Interface — v0.3 Specification

**Status:** Draft  
**Date:** June 2026  
**Author:** Rodrigo Giuliani  
**Contact:** rgiuliani@dosync.dev

---

## Overview

The DoSync Semantic Resolver is the component responsible for translating an `Intent` into an `ActionPlan`. It is the intelligence layer of the protocol — the part that decides *which devices should act* and *how* in response to a semantic goal.

The resolver interface is formally decoupled from the rest of the protocol. This means:

- Any third party can implement a custom resolver
- The resolver can be replaced without modifying the adapter layer, audit system, or transport layer
- A production deployment can use a local LLM as the resolver itself

This document defines the formal contract that all resolvers must fulfill.

---

## The Interface

```python
class BaseResolver:
    """
    Formal interface for DoSync semantic resolvers.

    The protocol defines WHAT a resolver must do, not HOW.
    Third-party implementations can be dropped in by subclassing this
    and passing the instance to DoSyncHub.

    A resolver receives an Intent and returns an ActionPlan.
    It has read-only access to the CapabilityRegistry.

    To implement a custom resolver:
        class MyResolver(BaseResolver):
            def resolve(self, intent: Intent) -> ActionPlan:
                ...

        hub = DoSyncHub()
        hub.resolver = MyResolver(hub.registry)
    """

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def resolve(self, intent: Intent) -> ActionPlan:
        raise NotImplementedError(
            'Subclasses must implement resolve(intent) -> ActionPlan'
        )
```

### Contract requirements

A conforming resolver implementation MUST:

1. Accept an `Intent` object as input
2. Return an `ActionPlan` object as output
3. Only read from the `CapabilityRegistry` — never write to it
4. Return an empty `ActionPlan` (no actions) when no devices are relevant, rather than raising an exception
5. Be deterministic for the same input in the same environment (no random behavior)
6. Complete within a reasonable timeout (recommended: < 500ms for non-LLM resolvers)

A conforming resolver MUST NOT:

- Modify device state directly
- Call device adapters directly
- Access the audit log
- Perform network I/O (unless implementing an LLM-backed resolver, see section below)

---

## Input: Intent

```python
@dataclass
class Intent:
    intent:     IntentClass          # semantic goal — open string type (^[a-z][a-z0-9_]*$)
    intent_id:  str                  # unique identifier (auto-generated)
    urgency:    Urgency              # emergency | alert | warning | info
    context:    dict                 # arbitrary context data
    source:     str                  # who fired this intent (mcp, api, gpio, scheduler)
    timestamp:  float                # unix timestamp
```

### Intent classes — open vocabulary (v0.3+)

As of v0.3, `IntentClass` is no longer an enum with fixed values. It is an open string type with format validation:

```python
class IntentClass(str):
    """
    Open string type — any value matching ^[a-z][a-z0-9_]*$ is valid.
    Five universal intents are seeded at hub init. All others are
    registered via POST /v1/intent-classes.
    """
    _PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
```

**Universal intent classes** (seeded at hub init, protected from deletion):

| IntentClass | Urgency | Description |
|---|---|---|
| `ensure_safety` | `emergency` | Safety emergency — protect people and property |
| `alert_anomaly` | `alert` | Unexpected condition detected — investigate |
| `control_access` | `alert` | Manage physical access to a space |
| `report_status` | `info` | Generate a status report |
| `notify` | `info` | Push information to any target |

**Domain-specific intent classes** are registered at runtime via the hub API:

```bash
POST /v1/intent-classes
{
  "name": "prepare_operating_room",
  "urgency": "alert",
  "resolution_tags": ["medical", "lighting", "access"],
  "resolution_actuators": ["turn_on", "unlock", "notify"],
  "domain": "healthcare"
}
```

The resolver automatically discovers registered intent classes from the database — no code changes or hub restart required.

### Urgency levels

| Urgency | Behavior |
|---|---|
| `emergency` | Bypasses all policy constraints. Executes immediately. |
| `alert` | High priority. Confirmation policies may apply. |
| `warning` | Elevated priority. Notable condition, no immediate action required. All policies apply. |
| `info` | Normal priority. All policies apply. |

---

## Output: ActionPlan

```python
@dataclass
class ActionPlan:
    intent_id:  str                  # links back to the originating intent
    actions:    list[DeviceAction]   # ordered list of actions to execute
    urgency:    Urgency              # inherited from the intent
```

```python
@dataclass
class DeviceAction:
    device_id:       str             # target device
    action:          str             # actuator type (turn_on, unlock, notify, etc.)
    params:          dict            # action parameters
    relevance_score: float           # resolver's confidence score (0.0–100.0)
```

### Action execution

The `ActionPlan` returned by the resolver is passed to the `PolicyEngine` before execution. The Policy Engine may:

- Block the plan entirely
- Request confirmation before executing
- Modify the list of actions (removing or adjusting)

Actions in the plan are executed in parallel by the `AdapterExecutor`, unless a `PhasedActionPlan` is returned (see section below).

---

## Capability Registry

The resolver has read-only access to the `CapabilityRegistry`, which contains the `CapabilityManifest` of every registered device.

```python
class CapabilityRegistry:
    def get(self, device_id: str) -> CapabilityManifest | None
    def all(self) -> list[CapabilityManifest]
    def find_by_tags(self, tags: list[str]) -> list[CapabilityManifest]
    def find_by_required_tags(self, required_tags: set[str]) -> list[CapabilityManifest]
    def find_emergency_capable(self) -> list[CapabilityManifest]
```

**Tag index (v0.3):** `CapabilityRegistry` maintains an inverted index mapping each tag to the set of device IDs that declare it. This index is updated incrementally on `register()` and `unregister()`.

- `find_by_tags(tags)` — union lookup: devices with ANY of the given tags. O(|tags| + |candidates|). Used by the resolver.
- `find_by_required_tags(required_tags)` — intersection lookup: devices with ALL of the given tags. O(|result|). Available as a utility for external queries.

```python
@dataclass
class CapabilityManifest:
    device_id:          str
    device_name:        str
    tags:               list[str]        # semantic tags for matching
    actuators:          list[ActuatorSpec]
    sensors:            list[SensorSpec]
    emergency_capable:  bool
    adapter:            str
    adapter_config:     dict
```

---

## Intent Class Resolution — Database-backed (v0.3+)

Prior to v0.3, the resolver used a static `INTENT_RESOLUTION_MAP` dict hardcoded in `hub.py` that mapped each `IntentClass` enum value to its resolution tags and actuators. **This map has been removed.**

As of v0.3, all intent class resolution data lives in the `intent_classes` SQLite table and is loaded at runtime via `_get_resolution()`:

```python
def _get_resolution(self, intent: Intent) -> dict:
    """Return resolution tags/actuators from the intent_classes DB table.
    All intent classes — universal and domain-specific — live in the DB.
    Falls back to empty resolution if intent class is not registered."""
    try:
        hub = getattr(self, "hub", None)
        db  = getattr(hub, "db", None)
        if db:
            name = str(intent.intent)
            row = db.get_intent_class(name)
            if row:
                return {
                    "tags":      row["resolution_tags"],
                    "actuators": row["resolution_actuators"],
                }
    except Exception as e:
        log.warning("_get_resolution: DB lookup failed for '%s': %s", intent.intent, e)
    return {"tags": [], "actuators": []}
```

**What this means for custom resolver implementations:**

Custom resolvers subclassing `BaseResolver` that previously accessed `INTENT_RESOLUTION_MAP` must update their implementation. The recommended approach is to either:

1. Call `hub.db.get_intent_class(name)` directly for the resolution data, or
2. Override `_get_resolution()` with custom logic

---

## Reference Implementations

### 1. CapabilityMatchingResolver (default)

Location: `dosync/hub.py`

The default resolver. Scores every registered device against the intent using:

- **Tag overlap** — devices whose tags match the intent's resolution tags score higher
- **Location match** — devices in the same location as the intent context score higher
- **Emergency bonus** — emergency-capable devices get a bonus score on emergency intents
- **Actuator match** — devices that support the required actuator types score higher

Devices with score > 0 are included in the action plan.

**Strengths:** Fast, deterministic, no external dependencies. Tag index reduces candidate evaluation by up to 97% for specific intents (v0.3).  
**Limitations:** No temporal context, no learned patterns

### 2. StateAwareResolver (recommended for production)

Location: `dosync/hub.py`

Extends `CapabilityMatchingResolver`. Maintains a state cache updated after each successful action execution and via a background refresh cycle (configurable interval, default 60s). Before including an action in the plan, checks if the action would have any effect given the current state:

- Does not `turn_on` a device that is already on at the requested brightness
- Does not `unlock` a door that is already unlocked
- Does not `set_temperature` to a value already within 0.5°C of current

**Strengths:** Reduces redundant actions, improves efficiency, cleaner audit log  
**Limitations:** State cache is in-memory (resets on hub restart unless persisted to DB)

---

## Custom Resolver Implementation Guide

### When to use a custom resolver

The `StateAwareResolver` is the recommended choice for production deployments — it handles tag-based matching, state awareness, and background refresh out of the box. A custom resolver is appropriate when:

- You want to use a **local LLM** to reason about device selection (see LLM-backed resolver below)
- Your deployment has **domain-specific scoring logic** that tag overlap doesn't capture (e.g. time-of-day weighting, occupancy signals, learned patterns)
- You are building a **research implementation** to evaluate alternative resolution strategies
- You need to integrate with an **external decision system** (building management software, industrial SCADA)

For most deployments, configuring tags correctly in device manifests is more effective than implementing a custom resolver.

### Minimal implementation

```python
from dosync.hub import BaseResolver
from dosync.models import Intent, ActionPlan, DeviceAction

class MyResolver(BaseResolver):
    def resolve(self, intent: Intent) -> ActionPlan:
        actions = []

        for device in self.registry.all():
            if self._is_relevant(device, intent):
                for actuator in device.actuators:
                    actions.append(DeviceAction(
                        device_id=device.device_id,
                        action=actuator.type,
                        params={},
                        relevance_score=1.0,
                    ))

        return ActionPlan(
            intent_id=intent.intent_id,
            actions=actions,
            urgency=intent.urgency,
        )

    def _is_relevant(self, device, intent) -> bool:
        # implement your logic
        return True
```

### Accessing intent class resolution data

```python
class MyResolver(BaseResolver):
    def __init__(self, registry, hub):
        super().__init__(registry)
        self.hub = hub

    def resolve(self, intent: Intent) -> ActionPlan:
        # Get resolution tags and actuators from the DB
        resolution = self._get_resolution(intent)
        target_tags = set(resolution.get("tags", []))
        target_actuators = set(resolution.get("actuators", []))

        # Use tag index for efficient candidate selection
        candidates = self.registry.find_by_tags(list(target_tags)) if target_tags else self.registry.all()
        # ... score and build ActionPlan
```

### Registering a custom resolver

```python
from dosync.hub import DoSyncHub

hub = DoSyncHub()
hub.resolver = MyResolver(hub.registry, hub)
```

> **Note:** `BaseResolver.__init__` only accepts `registry`. If your custom resolver needs access to `hub.db` (to call `_get_resolution()` or query intent classes), override `__init__` to accept `hub` as a second argument, as shown in the example above. The `StateAwareResolver` follows this same pattern — it receives `hub` in its constructor and stores it as `self.hub`.

### LLM-backed resolver

```python
class LLMResolver(BaseResolver):
    def __init__(self, registry, hub, model_path: str):
        super().__init__(registry)
        self.hub = hub
        self._model = load_model(model_path)

    def resolve(self, intent: Intent) -> ActionPlan:
        devices = self.registry.all()
        prompt = self._build_prompt(intent, devices)
        response = self._model.generate(prompt)
        return self._parse_response(response, intent)
```

---

## Versioning

| Version | Changes |
|---|---|
| v0.1 | Initial implementation — `SemanticResolver` (tag matching) |
| v0.2 | Formal interface introduced — `BaseResolver`, `CapabilityMatchingResolver`, `StateAwareResolver` |
| v0.3 | Inverted tag index — O(1) candidate selection, union/intersection strategies, emergency guarantee. Open intent classes — `INTENT_RESOLUTION_MAP` removed, all resolution data in SQLite `intent_classes` table. `Urgency.WARNING` formally defined. |
| v0.4 (implemented) | Direct device state querying — `StateAwareResolver` background refresh cycle queries device state before scoring. Configurable interval (default 60s). Persisted to SQLite. |
| v1.0 (planned) | Stable interface — breaking changes require major version bump |

---

## Changelog

**v0.3 (June 2026)**
- **BREAKING:** `INTENT_RESOLUTION_MAP` removed from `hub.py`. Resolution data now lives in SQLite `intent_classes` table. Custom resolvers must update to use `_get_resolution()` or `hub.db.get_intent_class()`.
- `IntentClass` redesigned from `str, Enum` to open `str` subclass. Any `^[a-z][a-z0-9_]*$` string is a valid intent class.
- Five universal intent classes seeded at hub init: `ensure_safety`, `alert_anomaly`, `control_access`, `report_status`, `notify`.
- Domain-specific intent classes registered at runtime via `POST /v1/intent-classes`.
- `Urgency.WARNING` formally documented — sits between `info` and `alert`.
- `StateAwareResolver` background refresh cycle documented.

**v0.2 (May 2026)**
- Introduced `BaseResolver` as the formal interface
- Renamed `SemanticResolver` to `CapabilityMatchingResolver`
- Added `StateAwareResolver` with device state cache
- Decoupled resolver from Policy Engine
- Documented LLM-backed resolver path

---

*DoSync Protocol v0.3 — Resolver Interface Specification*  
*Apache 2.0 — github.com/giulianireg-spec/dosync-protocol*
