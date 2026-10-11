# SAMUEL — Standard for Agent Mediation Under Enforceable Limits

*What an AI agent may do to physical devices, within limits the operator sets and
the hub enforces.*

**Version 0.2 — 2026-10-11. Status: draft. Normative on its own.**
DoSync is its reference implementation (`dosync/samuel/`, the kernel;
`dosync/samuel_http.py`, the binding of §11). Supersedes v0.1, which deferred to
the DoSync protocol's §6.8 for the rules; that list now defers here.

The limits in the name are the agent's: the bounds of what it is allowed to do —
which actions, on which devices, in which places, in an emergency — not
quantities.

The key words MUST, MUST NOT, SHOULD and MAY are to be read as in RFC 2119.

---

## 1. Why this exists

An agent that controls devices can be trusted on average and still do, on some
day, with some phrasing or some manipulated input, what its deployment forbids.
SAMUEL is the part that does not depend on the agent being right: whoever chooses
the devices, a deployment's rules decide what runs, every refusal says why, and
everything is recorded.

If the agent is the mind and the devices are the body, a SAMUEL hub is part of
the nervous system between them: the mind proposes, and what reaches the body
crosses rules the mind cannot rewrite. Nothing here is specific to a kind of
deployment. A production line, a hospital wing, a warehouse, a fleet of drones
and a house are governed by the same rules; only their places, classes and
devices differ.

**Scope.** SAMUEL specifies what a hub lets through and what it records. It does
not specify how a hub chooses devices for a goal (that is resolution, one way of
proposing among others), how it talks to devices, or how devices are described
(§10 says how a description maps to the model).

## 2. Threat model

- **Trusted:** the hub, its configuration, and the **operator credential** — the
  credential a person uses to place devices, define classes, set policies,
  release or deny what is held, and read the record.
- **Not trusted:** the **agent**. It may be wrong, misled or injected — by text
  in the operator's message, in a device's description, in anything it reads. It
  holds only the **agent credential**, which can fire intents and nothing else.
- **What the attacker controls:** the agent's inputs. The attacker does not know
  which actions the operator marked for confirmation (§5 R9), and does not hold
  the operator credential.
- **What SAMUEL does not claim:** it does not detect injection. An injected
  request and a genuine one are the same intent. SAMUEL bounds the damage to what
  the operator did not mark, and makes every decision auditable.

## 3. The model

A hub governs with this model, whatever format its devices come from.

**A device** has:

| Field | Meaning | Set by |
|---|---|---|
| id | Unique within the hub | The description |
| declared actions | Action types it can perform, each with a parameter schema | The description |
| declared sensors | Sensor types it can read, each with a kind (`environment` or `device_state`) | The description |
| place | A path of segments (`site-a/line-3/cell-2`); a place contains what lies below it, by whole segment | **The operator** |
| emergency-capable | Whether it takes part in emergencies | The description, or the operator |
| emergency actions | What it does in an emergency, a subset of its declared actions | **The operator** |

A device's place and emergency actions MUST NOT be taken from a third party's
description of it: they are decisions of the deployment. To conform, a hub MUST
reserve setting them, and defining intent classes, to the operator credential on
every interface it offers, and report so (`configuration: operator`, §11). A hub
MAY let a deployment leave them open to the hub's general credential (reporting
`configuration: hub`); such a deployment does not conform, because an agent
holding that credential could place devices or grant itself authority.

**An intent class** has: the actions it grants (a list of action types, or `*`
for any action a device declares), the sensors it reads, a default urgency, and a
location role — `restricts` (a place in the intent confines it) or `informs` (a
place only says where something happened). A class is the operator's grant of
authority: through an intent, an agent can do only what its class grants.

**An intent** has a class, an urgency (`info`, `warning`, `alert`, `emergency`)
and a context that MAY name a place (`location`), MAY narrow the class's actions
(`action_types`) and MAY carry the agent's own choice of device actions
(`proposed_actions`, a list of `{device_id, action, params?}`).

**Opposite pairs:** `lock`/`unlock`, `turn_on`/`turn_off`, `open`/`close`,
`start`/`stop`, `arm`/`disarm`.

### 3.1 Universal classes

Every hub MUST provide these six classes; a deployment adds its own.

| Class | Grants | Location role |
|---|---|---|
| `ensure_safety` | `alarm`, `notify`, `call`, `turn_on`, `set_brightness` | informs |
| `alert_anomaly` | `notify`, `call` | informs |
| `control_access` | `lock`, `unlock` | restricts |
| `report_status` | reading sensors | restricts |
| `notify` | `notify`, `display`, `call` | informs |
| `operate_device` | `*` — any declared action, only as proposed | restricts |

## 4. Three ways an action reaches a device

1. **Resolution.** The hub chooses the devices for an intent. A hub MAY resolve;
   if it does, what it chooses is a proposal like any other and crosses the same
   rules.
2. **Governed direct mode.** The intent carries `proposed_actions`; the agent has
   chosen; the hub validates each proposal.
3. **Direct control.** One device, one action, no intent. This is an operator's
   path, not an agent's (R8).

Paths 1 and 2 meet the same rules, the same operator policies and the same
record.

## 5. Rules (normative)

**R1 — Participation is by capability.** A device takes part in a resolved intent
only if it declares an action or sensor the class grants. Anything else a hub
uses (tags, ranking) MAY order devices, MUST NOT admit one.

**R2 — A place restricts.** When the intent names a place and its class
`restricts`, only devices at or below that place act — in an emergency too.

**R3 — An unknown place is refused, except in an emergency.** A restricting place
at which no device is MUST be refused (`unknown_location`) outside an emergency.
An emergency is never refused for its place: it acts as if none were given, and
the hub records it.

**R4 — An emergency-capable device does what was declared, never everything.**
At `emergency` urgency, an emergency-capable device that has no action the class
grants performs its declared emergency actions and nothing else; declaring none,
it takes part without acting, and the hub records that.

**R5 — In an emergency, what was declared comes first.** An emergency-capable
device with declared emergency actions performs exactly those in an emergency,
even when the class grants other actions it declares — on both paths. A proposed
action that is not one of them is refused (`not_its_emergency_action`); one that
is, runs even if the class does not grant it.

**R6 — A plan never undoes itself.** No plan sends one device both actions of an
opposite pair. `action_types` MUST be a non-empty subset of the class's grants
(else `invalid_actions`). Outside an emergency, an intent to be resolved whose
class grants both actions of a pair MUST say which (else `ambiguous_actions`). Two
opposite proposals for one device are both refused (`opposite_actions`).

**R7 — The agent proposes, the hub guarantees.** Each proposed action runs only
if the device exists (`unknown_device`) and declares the action (`not_declared`;
reading sensors needs declared sensors), the class grants it (`outside_class`;
`operate_device` grants any declared action), a restricting place contains the
device (`outside_place`), and R5 and R6 allow it. `operate_device` acts only on
proposals (`proposals_required`); proposals MUST be a list of at most 200
`{device_id, action, params?}` (`invalid_proposals`); an empty list means none.

**R8 — Direct control is not an agent's path.** A hub either has no direct
control, or opens it only to the operator credential, and reports which. A hub
MAY open it for development; such a hub does not conform. When open, R1 (declared
actions) and R5 still hold.

**R9 — What the operator marks consequential waits for a person.** The operator
MAY mark action types, optionally on named devices, as needing a person. The hub
then withholds **only** the marked actions of a plan and runs the rest; a held
action runs only when a person confirms it with the operator credential, and is
dropped when denied; an unreleased hold expires. In an emergency, a device's
declared emergency actions are not held by a mark unless the mark says
`even_in_emergency`.

**R10 — No device oscillates.** Within a rolling window, a device is not reversed
across an opposite pair more than an allowed number of times, across separate
intents; a further reversal is dropped (`oscillation`) and never runs. Declared
emergency actions are exempt. The window and the number are the hub's
configuration and MUST be reported (§11); the reference values are 60 s and 1.

**R11 — Policies before anything runs.** The operator's policies evaluate every
plan before it runs and MAY deny it or remove devices from it; a modification is
recorded with the plan before and after.

**R12 — Nothing is silent.** A refused intent, a refused proposal, a dropped,
held, released, denied or expired action, and a policy's decision are each
returned to the caller with their reason and appended to the record (§9).

## 6. Guarantees

| | Guarantee | Rules |
|---|---|---|
| **G1** | **Declared.** A device performs only actions it declares. | R1, R7, R8 |
| **G2** | **Authorized.** An action runs only if the intent's class grants it. | R6, R7 |
| **G3** | **Placed.** When the class restricts, only devices at or below the named place act; an unknown restricting place is refused outside an emergency. | R2, R3 |
| **G4** | **Consistent.** No plan sends a device two opposite actions; no device oscillates across intents. | R6, R10 |
| **G5** | **Declared first in emergencies.** An emergency-capable device with declared emergency actions performs exactly those, on both paths. | R4, R5 |
| **G6** | **Accountable.** Nothing refused, dropped or held is silent: it is returned with its reason and recorded. | R12, §9 |
| **G7** | **Policed.** The operator's policies evaluate every plan before it runs. | R11 |
| **G8** | **No side door.** Direct control is closed, or open only to the operator credential; placement and the record are the operator's. | R8, §3 |
| **G9** | **Confirmed.** What the operator marks waits for a person; the rest runs. | R9 |

## 7. Refusals

Every refusal has a stable reason, so an agent can correct itself and an auditor
can count.

### 7.1 An intent, before anything runs (HTTP 422)

| Reason | When |
|---|---|
| `invalid_name` | The class name is not a valid class name |
| `not_registered` | No such class on this hub |
| `invalid_urgency` | The urgency is not one of the four |
| `unknown_location` | A restricting class names a place where no device is, outside an emergency (R3) |
| `idempotency_conflict` | An idempotency key was reused for a different intent (a binding MAY offer idempotency keys) |
| `invalid_actions` | `action_types` is not a non-empty subset of what the class grants (R6) |
| `ambiguous_actions` | The class grants both actions of an opposite pair and the intent did not say which, outside an emergency (R6) |
| `invalid_proposals` | `proposed_actions` is malformed (R7) |
| `proposals_required` | `operate_device` was fired with no proposals (R7) |

### 7.2 A proposed action (in the decision and the record)

| Reason | When |
|---|---|
| `unknown_device` | No such device |
| `not_declared` | The device does not declare the action |
| `outside_class` | The intent's class does not grant the action |
| `outside_place` | The class restricts and the device is not in the named place |
| `not_its_emergency_action` | In an emergency, the device has emergency actions and this is not one |
| `opposite_actions` | The same device was proposed both actions of an opposite pair |

Applied to any plan, resolved or proposed: `oscillation` (R10). An action marked
for confirmation (R9) is not refused: it is **held**.

### 7.3 A direct action (HTTP 403 or 422)

| Reason | When |
|---|---|
| `direct_control_disabled` | Direct control is closed on this hub (the default) |
| `operator_credential_required` | Direct control is open only to the operator credential, and it was not given |
| `not_declared` | The device does not declare the action |
| `not_its_emergency_action` | In an emergency, not one of the device's emergency actions |

## 8. Holds

A hold is keyed by the intent that produced it. It ends in exactly one of:
**confirmed** (the operator credential releases it; the actions run, without
being held again), **denied** (dropped), or **expired** (dropped when unreleased
past the hub's timeout, which MUST be reported). A hold that has ended cannot be
released.

## 9. The record

Every decision is appended to a log chained as `h_n = SHA256(e_n ‖ h_{n-1})`, so
that removing or altering an entry breaks every hash after it. The record MUST
include: intents refused as a whole, with their reason; executed intents with
their refused proposals and devices that took part without acting; intents a
policy blocked, with the policy; plans a policy modified, before and after;
actions held, released, denied or expired; actions dropped for oscillation; and
direct actions executed, blocked or refused. The record is read with the
operator credential.

## 10. From a device description to the model

A format conforms by defining how its descriptions map to §3.

- **DoSync Capability Manifest** — DoSync's native form.
- **W3C WoT Thing Description 1.0 / 1.1** — actions and writable properties become
  declared actions, mapped in layers (the `dosync:` vocabulary, a known semantic
  `@type`, a verb in the name, a writable property, or the affordance's own
  name), with how each was mapped reported; readable properties become sensors;
  place and emergency actions come from the operator (§3). The `dosync:`
  vocabulary is published at `https://dosync.dev/ns/governance`.

An action that keeps its own name is governed like any other, but R6 and R10 only
see the opposite pairs of §3: a format SHOULD map actions to those types where it
can. Measured on 515 public plugfest TDs, 81% of actions kept their own names.

## 11. HTTP binding (`samuel/v0`)

A conforming hub exposes these operations under one base path (`/samuel/v0` in
the reference implementation). The agent credential is a bearer token; the
operator credential is sent as `X-Operator-Token`. They MUST be different.

| Operation | Credential | Body / answer |
|---|---|---|
| `GET /samuel/v0` | any | `{binding: "samuel/v0", samuel, resolves, direct_control: closed\|operator\|open, configuration: operator\|hub, oscillation: {window_seconds, max_reversals}, confirmation_timeout_seconds, conformance_mode}` |
| `PUT /samuel/v0/devices/{id}` | operator | `{actions: [{type, params_schema?}], sensors: [{type, kind?}], place, emergency_capable, emergency_actions: [{action, params?}]}` |
| `PUT /samuel/v0/classes/{name}` | operator | `{grants, sensors?, urgency?, location_role}`; a universal class cannot be redefined (409) |
| `POST /samuel/v0/intents` | agent | `{class, urgency, context}` → `{intent_id, status, ran: [{device_id, action}], refused: [{device_id, action, reason}], held: [{device_id, action, reason}]}`; a refusal of the intent as a whole is 422 `{reason, detail}` |
| `POST /samuel/v0/intents/{id}/confirm` | operator | the released actions' results; 404 if nothing is held |
| `POST /samuel/v0/intents/{id}/deny` | operator | 404 if nothing is held |
| `GET /samuel/v0/audit?since=n` | operator | `{verified, length, entries}` |
| `PUT /samuel/v0/policies` | operator, conformance mode only | `{exclusions: [{classes, devices}], confirmations: [{actions, devices?, even_in_emergency?}]}`; replaces only what this operation set |

Policies are set over the binding only in conformance mode; a deployment
configures its own policies by its own means, never over an API an agent can
reach.

## 12. Conformance

A hub conforms to SAMUEL 0.2 if `samuel-certify` (in the reference
implementation, `dosync/samuel_certify.py`; standard library only) passes every
check against its binding, in conformance mode:

| Guarantee | Checks |
|---|---|
| G1 Declared | S03 |
| G2 Authorized | S04, S05, S18 |
| G3 Placed | S06, S07, S08 (if the hub resolves) |
| G4 Consistent | S09, S10 |
| G5 Declared first | S11, S12 (if the hub resolves) |
| G6 Accountable | S19, S20 |
| G7 Policed | S13 |
| G8 No side door | S01, S02 |
| G9 Confirmed | S14, S15, S16, S17 |

The suite's scene is a production cell, a line and a hangar bay. DoSync also
certifies itself with its own suite (`dosync-certify`, CONFORMANCE tier), which
covers DoSync's API beyond SAMUEL.

## 13. What this specification does not do, and what was measured

It does not decide which devices *should* act for a goal, and it does not make an
agent's choices correct; it bounds what any choice can do. Against an agent
misled through its inputs (two pre-registered comparisons, 232 attack runs, one
model, a home and a plant), no run violated a guarantee once two defects the
first comparison found were fixed; the structural guarantees stopped few attacks
— most asked for what a class already grants, and the agent's own judgement
stopped most of the rest — and with operator marks (R9) nothing unmarked passed
beyond what was predicted to pass by design, at the cost of holds on ordinary
requests (28 over 24). The guarantees are tested — by conformance, by the
reference implementation's test suite and by replaying recorded agent behaviour
against it — not formally verified.
