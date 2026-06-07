# DoSync Resolver Interface — v0.2 Specification

**Status:** Draft  
**Date:** May 2026  
**Author:** Rodrigo Giuliani  
**Contact:** giulianireg@gmail.com

---

## Overview

The DoSync Capability-based Resolver is the component responsible for translating an `Intent` into an `ActionPlan`. It maps a high-level goal to concrete device actions by matching the intent's requirements against declared device capabilities.

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
    Formal interface for DoSync resolvers.

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
    intent:     IntentClass          # semantic goal (ensure_safety, save_energy, etc.)
    intent_id:  str                  # unique identifier (auto-generated)
    urgency:    Urgency              # emergency | alert | info
    context:    dict                 # arbitrary context data
    source:     str                  # who fired this intent (mcp, api, gpio, scheduler)
    timestamp:  float                # unix timestamp
```

### Intent classes

| IntentClass | Description |
|---|---|
| `ensure_safety` | Safety emergency — protect people and property |
| `alert_anomaly` | Something unexpected detected — investigate |
| `control_access` | Manage physical access to a space |
| `monitor_health` | Passive observation of a person's state |
| `notify_family` | Push information to family members |
| `report_status` | Generate a status report |
| `set_environment` | Adjust comfort parameters |
| `save_energy` | Reduce power consumption |
| `remind_chore` | Remind about a pending task |
| `bedtime_routine` | Prepare the space for sleep |
| `morning_routine` | Prepare the space for the day |
| `away_mode` | Secure and optimize for an empty space |
| `children_arrived_home` | Children detected at entrance |

### Urgency levels

| Urgency | Behavior |
|---|---|
| `emergency` | Bypasses all policy constraints. Executes immediately. |
| `alert` | High priority. Confirmation policies may apply. |
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
- `find_by_required_tags(required_tags)` — intersection lookup: devices with ALL of the given tags. O(|result|). Available as a utility for external queries; not used in `resolve()`.
- `find_emergency_capable()` — O(|emergency_devices|) with a dedicated index.

The resolver always uses union lookup. Intersection was considered but rejected: semantic intents need devices relevant to ANY resolution tag, not ALL simultaneously. See `DESIGN-PRINCIPLES.md` for the full rationale.

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
**Limitations:** No state awareness, no temporal context, no learned patterns

### 2. StateAwareResolver (recommended for production)

Location: `dosync/hub.py`

Extends `CapabilityMatchingResolver`. Maintains a state cache updated after each successful action execution. Before including an action in the plan, checks if the action would have any effect given the current state:

- Does not `turn_on` a device that is already on at the requested brightness
- Does not `unlock` a door that is already unlocked
- Does not `set_temperature` to a value already within 0.5°C of current

**Strengths:** Reduces redundant actions, improves efficiency, cleaner audit log  
**Limitations:** State cache is in-memory (resets on hub restart), does not query device state directly

---

## Custom Resolver Implementation Guide

### Minimal implementation

```python
from dosync.hub import BaseResolver
from dosync.models import Intent, ActionPlan, DeviceAction

class MyResolver(BaseResolver):
    def resolve(self, intent: Intent) -> ActionPlan:
        actions = []

        for device in self.registry.all():
            # Your custom logic here
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

### Registering a custom resolver

```python
from dosync.hub import DoSyncHub

hub = DoSyncHub()
hub.resolver = MyResolver(hub.registry)
```

### LLM-backed resolver

A resolver that uses a local LLM (e.g. Llama, Mistral) to reason about which devices should respond to an intent:

```python
class LLMResolver(BaseResolver):
    def __init__(self, registry, model_path: str):
        super().__init__(registry)
        self._model = load_model(model_path)  # your LLM loading logic

    def resolve(self, intent: Intent) -> ActionPlan:
        # Build a prompt with the intent and all device manifests
        devices = self.registry.all()
        prompt = self._build_prompt(intent, devices)

        # Query the LLM
        response = self._model.generate(prompt)

        # Parse the response into an ActionPlan
        return self._parse_response(response, intent)
```

This is the intended evolution path for the resolver — local LLMs can reason about context, learn patterns, and compose actions in ways that a tag-matching algorithm cannot.

---

## Versioning

This specification follows semantic versioning.

| Version | Changes |
|---|---|
| v0.1 | Initial implementation — `SemanticResolver` (tag matching) |
| v0.2 | Formal interface introduced — `BaseResolver`, `CapabilityMatchingResolver`, `StateAwareResolver` |
| v0.3 | Inverted tag index — O(1) candidate selection, union/intersection strategies, emergency guarantee |
| v0.4 (planned) | Direct device state querying — resolver queries device state before scoring |
| v1.0 (planned) | Stable interface — breaking changes require major version bump |

---

## Changelog

**v0.2 (May 2026)**
- Introduced `BaseResolver` as the formal interface
- Renamed `SemanticResolver` to `CapabilityMatchingResolver`
- Added `StateAwareResolver` with device state cache
- Decoupled resolver from Policy Engine (resolver runs before policy evaluation)
- Documented LLM-backed resolver path

---

*DoSync Protocol v0.2 — Resolver Interface Specification*  
*Apache 2.0 — github.com/giulianireg-spec/dosync-protocol*
