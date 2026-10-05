# DoSync Governance Specification

**Version 0.1 — 2026-10-05. Status: draft, extracted from DoSync protocol 0.5.**

This document specifies how a hub governs what an AI agent does to physical
devices, independently of how those devices are described. A hub conforms to it
whether its devices come from DoSync Capability Manifests, W3C WoT Thing
Descriptions or any other format, provided that format is mapped to the model in
§2.

It does not repeat the protocol: each guarantee below states what holds and
names the rule of the DoSync specification (`DoSync-SPEC-v0.1.md`, §6.8) that
defines it in full. Where the two could seem to disagree, the protocol rule is
normative. `tests/test_governance_spec_matches_the_hub.py` checks that every
refusal the reference hub can return, every universal class it seeds and every
certification check named here is in this document.

The key words MUST, MUST NOT and MAY are to be read as in RFC 2119.

---

## 1. Why this exists

An agent that controls devices can be trusted on average and still do, on some
day, with some phrasing or some manipulated input, what its deployment forbids.
Two comparisons of DoSync with an AI agent (pre-registered, in
`benchmarks/agent_eval/`) found that an agent chooses devices as well as a
rule-based resolver; what it cannot provide is a guarantee. This specification
is that guarantee: whoever chooses the devices — the hub or the agent — a
deployment's rules decide what runs, every refusal says why, and everything is
recorded.

## 2. What governance needs to know

A hub governs with this model, whatever format it was built from.

**A device** has:

| Field | Meaning | Set by |
|---|---|---|
| id | Unique within the hub | The description |
| declared actions | Action types it can perform, each with a parameter schema | The description |
| declared sensors | Sensor types it can read, each with a kind (`environment` or `device_state`) | The description |
| place | A path of segments (`plant-1/line-3`); a place contains what lies below it, by whole segment | **The operator** |
| emergency-capable | Whether it takes part in emergencies | The description, or the operator |
| emergency actions | What it does in an emergency, a subset of its declared actions | **The operator** |

A device's place and emergency actions MUST NOT be taken from a third party's
description of it: they are decisions of the deployment.

**An intent class** has: the actions it grants (a list of action types, or `*`
for any action a device declares), the sensors it reads, a default urgency, and a
location role — `restricts` (a place in the intent confines it) or `informs` (a
place only says where something happened). A class is the operator's grant of
authority: through an intent, an agent can do only what its class grants.

**An intent** has a class, an urgency (`info`, `warning`, `alert`, `emergency`)
and a context that MAY name a place, MAY narrow the class's actions
(`action_types`) and MAY carry the agent's own choice of device actions
(`proposed_actions`).

### 2.1 Universal classes

Every hub MUST provide these six classes; a deployment adds its own.

| Class | Grants | Location role |
|---|---|---|
| `ensure_safety` | `alarm`, `notify`, `call`, `turn_on`, `set_brightness` | informs |
| `alert_anomaly` | `notify`, `call` | informs |
| `control_access` | `lock`, `unlock` | restricts |
| `report_status` | reading sensors | restricts |
| `notify` | `notify`, `display`, `call` | informs |
| `operate_device` | `*` — any declared action, only as proposed | restricts |

## 3. Three ways an action reaches a device

1. **Resolution.** The agent fires an intent; the hub chooses the devices: those
   that declare what the class grants, confined by a restricting place, with
   emergency-capable devices taking part in emergencies.
2. **Governed direct mode.** The intent carries `proposed_actions`; the agent has
   chosen; the hub validates each proposal instead of resolving.
3. **Direct control.** One device, one action, no intent. This is an operator's
   path, not an agent's (G8).

Paths 1 and 2 meet the same guarantees, the same operator policies and the same
audit log.

## 4. Guarantees

| | Guarantee | Protocol rule |
|---|---|---|
| **G1** | **Declared.** A device performs only actions it declares. | §6.8 rules 1, 7, 8 |
| **G2** | **Authorized.** An action runs only if the intent's class grants it; `operate_device` grants any declared action, and an operator narrows or blocks it by policy. | §6.8 rule 7 |
| **G3** | **Placed.** When the class restricts, only devices at or below the named place act; an unknown restricting place is refused outside an emergency. | §6.8 rules 2, 3 |
| **G4** | **Consistent.** No plan sends one device two opposite actions (`lock`/`unlock`, `turn_on`/`turn_off`, `open`/`close`, `start`/`stop`, `arm`/`disarm`); an intent whose class grants both of a pair must say which it means, or is refused outside an emergency. | §6.8 rule 6 |
| **G5** | **Declared first in emergencies.** At emergency urgency, an emergency-capable device that has emergency actions performs exactly those. | §6.8 rules 4, 5 |
| **G6** | **Accountable.** A refused intent, proposal or direct action is never executed and never silently dropped: it is returned with its reason and recorded in the audit log. | §6.8 rule 7; §7.8 |
| **G7** | **Policed.** Before anything runs, the operator's policies evaluate the plan and MAY deny it or remove devices from it, recording the plan before and after. | §7 |
| **G8** | **No side door.** Direct control is closed by default, or open only to an operator credential that is never given to an agent; when open, G1 and G5 still hold. The hub reports which. | §6.8 rule 8 |

## 5. Refusals

Every refusal has a stable reason, so an agent can correct itself and an auditor
can count.

### 5.1 An intent, before anything runs (HTTP 422)

| Reason | When |
|---|---|
| `invalid_name` | The class name is not a valid class name |
| `not_registered` | No such class on this hub |
| `invalid_urgency` | The urgency is not one of the four |
| `unknown_location` | A restricting class names a place where no device is, outside an emergency |
| `idempotency_conflict` | The idempotency key was used for a different intent |
| `invalid_actions` | `action_types` is not a non-empty subset of what the class grants |
| `ambiguous_actions` | The class grants both actions of an opposite pair and the intent did not say which, outside an emergency |
| `invalid_proposals` | `proposed_actions` is not a list of `{device_id, action, params?}` |
| `proposals_required` | `operate_device` was fired with no proposals |

### 5.2 A proposed action (in the result and the audit log)

| Reason | When |
|---|---|
| `unknown_device` | No such device |
| `not_declared` | The device does not declare the action |
| `outside_class` | The intent's class does not grant the action |
| `outside_place` | The class restricts and the device is not in the named place |
| `not_its_emergency_action` | In an emergency, the device has emergency actions and this is not one |
| `opposite_actions` | The same device was proposed both actions of an opposite pair |

### 5.3 A direct action (HTTP 403 or 422)

| Reason | When |
|---|---|
| `direct_control_disabled` | Direct control is closed on this hub (the default) |
| `operator_credential_required` | Direct control is open only to the operator credential, and it was not given |
| `not_declared` | The device does not declare the action |
| `not_its_emergency_action` | In an emergency, not one of the device's emergency actions |

## 6. The audit log

Every decision is appended to a log chained as `h_n = SHA256(e_n ‖ h_{n-1})`, so
that removing or altering an entry breaks every hash after it. The log MUST record
executed intents with any refused proposals and any devices that took part
without acting; blocked intents with the deciding policy; plans a policy modified,
before and after; and direct actions executed, blocked or refused. Event types are
listed in the protocol's §7.8.

## 7. From a device description to the model

A format conforms by defining how its descriptions map to §2.

- **DoSync Capability Manifest** — the model's native form (protocol §5).
- **W3C WoT Thing Description 1.0 / 1.1** — protocol §5.9: actions and writable
  properties become declared actions, mapped in layers (the `dosync:` vocabulary,
  a known semantic `@type`, a verb in the name, a writable property, or the
  affordance's own name), with how each was mapped reported; readable numeric and
  boolean properties become sensors; place and emergency actions come from the
  operator. A device DoSync exports to a TD comes back from it with everything
  governance reads. The `dosync:` vocabulary is published at
  `https://dosync.dev/ns/governance`.

An action that keeps its own name is governed like any other: `operate_device`
grants it, and G1 and G3–G8 hold.

## 8. Conformance

A hub conforms to this specification if it passes these checks of the DoSync
certification suite (`dosync-certify`, spec/CERTIFICATION-GUIDE.md):

| Guarantee | Checks |
|---|---|
| G1 Declared | C25, C26 |
| G2 Authorized | C25 |
| G3 Placed | C15, C16, C17, C18, C19, C21 |
| G4 Consistent | C23, C25 |
| G5 Declared first | C22, C24 |
| G6 Accountable | C21, C25 |
| G7 Policed | C04, C05, C06 |
| G8 No side door | C26 |

and keeps the audit log verifiable (C07, C08).

## 9. What this specification does not do

It does not decide which devices *should* act for a goal: resolution is one way,
an agent's proposal another, and a deployment may use either. It does not make an
agent's choices correct; it bounds what any choice can do. And it has not been
tested against an adversarial agent: the bound holds by construction and by
certification, and how much it matters under attack is still to be measured.
