# DoSync Protocol — Consistency Model for Simultaneous Intents

**Status:** Specification  
**Version:** 0.1  
**Location:** `spec/CONSISTENCY-MODEL.md`

---

## Overview

This document defines the behavior of a DoSync hub when two or more intents are received simultaneously or overlap in execution. It specifies ordering guarantees, conflict resolution, audit log behavior, and the observable outcome for operators.

---

## Definitions

**Simultaneous intents** — two intents where the second is received before the first has been dispatched to the executor. The exact window is implementation-defined; in the reference implementation this corresponds to the resolver cycle duration.

**Overlapping intents** — two intents where the second arrives while the first's `ActionPlan` is still executing.

**Conflict** — two intents that would produce contradictory actions on the same device (e.g., `turn_on` and `turn_off` the same light).

---

## Model

### 1. Priority ordering

All intents carry an implicit priority derived from their `intent` class and `urgency`:

```
Priority (ascending):
  save_energy / away_mode           → 5 (lowest)
  set_environment / routines        → 4
  notify_family                    → 3
  control_access                    → 2
  alert_anomaly                     → 1
  ensure_safety [emergency]         → 0 (highest)
```

When two intents are simultaneous, the hub processes them in priority order. Lower number = processed first.

### 2. Same-priority resolution

When two intents share the same priority (e.g., two `save_energy` intents from different sources), the hub serializes them in arrival order (FIFO). The second intent begins execution only after the first has been dispatched.

### 3. Emergency concurrent execution

An `ensure_safety [emergency]` intent runs concurrently with any in-progress execution. If a `bedtime_routine` ActionPlan is currently executing and an emergency intent arrives:

1. The emergency intent is resolved immediately
2. The emergency ActionPlan is dispatched in parallel with the in-progress plan
3. The emergency plan does NOT abort the in-progress plan (aborting an in-flight action risks leaving a device in an undefined intermediate state; instant actions are not aborted mid-send)
4. Both executions are logged as separate audit entries

**Normative guarantee — emergency device-finality (overlap-scoped).** For instant (non–long-running) actions, an `emergency`-urgency write is the *device-final* write with respect to any lower-urgency action it overlaps with. Concretely, when an emergency and a lower-urgency plan target the same device with contradictory actions (e.g., `bedtime_routine` dims a light while `ensure_safety` sets it to full brightness):

- The emergency action is applied and the device ends in the emergency-commanded state.
- Any lower-urgency action on that device that arrives while the emergency is in effect is **dropped** (not sent to the device), reported with `success=false`, and recorded in the audit log as `action_superseded_by_priority`.
- If a lower-urgency action was already mid-send when the emergency arrived, it is not aborted; the emergency write follows and is the final state.

The window in which the emergency remains device-final is **bounded by the overlap**, not by a fixed wall-clock duration: it is held while the emergency intent is active and released on its completion, lingering only for a short `grace` that covers the dispatch skew of a concurrently-dispatched straggler (default 3 s, `DOSYNC_EMERGENCY_CLAIM_GRACE`; a `DOSYNC_EMERGENCY_CLAIM_MAX_HOLD` safety cap prevents an unreleased claim from ever locking a device). A routine dispatched *after* the emergency has resolved (beyond the grace) controls the device normally — the guarantee governs *overlap*, not ownership.

This guarantee is enforced at the **execution layer** (the device arbiter), not the pre-dispatch policy layer, because a pre-dispatch policy cannot retract or reorder actions from a plan that is already in flight. It applies to instant actions only; preemption of a long-running operation (e.g., a drone mid-maneuver) is governed by the operation lifecycle (the `INTERRUPTED` state), not this section.

### 4. Device-level conflict resolution

Two cases, resolved at two different layers:

**Simultaneous (both plans seen before dispatch)** — the `ConflictResolutionPolicy` resolves at the policy layer before dispatch:

| Scenario | Resolution | Status |
|---|---|---|
| Same device, same action, same params | Deduplicated — device called once | Specified (v0.4) |
| Same device, same action, different params | Higher-priority intent's params used | Implemented |
| Same device, conflicting actions (on vs off) | Higher-priority intent wins; lower-priority action dropped | Implemented |
| Different devices, no overlap | Both execute in parallel, no conflict | Implemented |

**Overlapping (a plan already in flight when a higher-urgency intent arrives)** — the pre-dispatch policy cannot act on an in-flight plan. The **device arbiter** enforces emergency device-finality per §3: per-device serialization plus an overlap-scoped claim under which lower-urgency actions are dropped and audited. Rows marked "Specified (v0.4)" describe intended behavior not yet present in the reference implementation.

### 5. Audit log guarantees

Each intent execution produces exactly one audit entry, regardless of conflicts or concurrent execution:

- `intent_executed` — intent resolved and dispatched
- `intent_blocked` — intent blocked by policy
- `intent_partial` — some devices failed, some succeeded
- `emergency_intent_blocked_by_policy` — emergency blocked by a policy with `bypass_on_emergency=False`

When two intents are simultaneous, the hub dispatches them in priority order. Audit entries are written as each execution completes; under high concurrency, the write order may differ from the dispatch order. The SHA-256 chain guarantees that whatever order is written is tamper-evident: `h_n = SHA256(e_n ∥ h_{n-1})`.

### 6. Atomicity guarantees

DoSync does NOT guarantee atomic execution of an ActionPlan. A plan that includes 10 devices may succeed on 7 and fail on 3. The result is `intent_partial`, not a rollback.

**Rationale:** physical devices cannot be rolled back. A light that turned on cannot "un-turn-on" atomically. The correct response to partial failure is observability (the audit log records which devices failed) and retry (the operator or AI agent can fire the intent again).

**Future work (v0.4):** a `CompensationPolicy` for intents that require all-or-nothing behavior on critical device groups.

### 7. State cache coherence

The `StateAwareResolver` maintains an in-memory cache of device states updated after each successful execution. Under simultaneous intents:

- Cache reads happen at resolve time (before dispatch)
- Cache writes happen after each device action completes
- Two simultaneous resolves may read the same stale cache state — both may include an action the other will also perform

This is acceptable: the device receives two commands and executes both. The second command is a no-op if the device is already in the target state (WiZ bulbs, HA entities). The cache converges to the correct state after both writes complete.

---

## Observable behavior summary

| Scenario | Behavior |
|---|---|
| Two intents, different priorities | Higher priority dispatched first; lower is serialized after |
| Two intents, same priority | FIFO ordering |
| Emergency + in-progress routine | Both run in parallel; emergency does not abort routine, but is device-final on any shared device ¹ |
| Two intents conflict on same device | Higher priority wins at device level |
| Two intents, no device overlap | Full parallel execution |
| Partial device failure | `intent_partial` logged; no rollback |

¹ On a shared device, the emergency write is the final state (§3): a lower-urgency action overlapping the emergency is dropped and audited as `action_superseded_by_priority`, not applied over the emergency.

---

## Implementation reference

The reference implementation in `dosync/policies.py` (`ConflictResolutionPolicy`) and `dosync/hub.py` (`DoSyncHub.fire_intent`) implements this model. The policy engine runs synchronously before dispatch; the executor runs asynchronously per device.

---

*DoSync Protocol v0.1 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
