# DoSync Protocol — Idempotency and Delivery Semantics

**Status:** Specification supplement
**Protocol version:** 0.2 (additive over 0.1 — backward compatible)
**Location:** `spec/IDEMPOTENCY-MODEL.md`
**Applies to:** `POST /v1/intent/async` and any intent-submitting endpoint

---

## Overview

This document defines DoSync's delivery guarantee and the optional idempotency
mechanism that makes intent retries safe for physical actions.

It closes a gap in the v0.1 specification: the
[Consistency Model](CONSISTENCY-MODEL.md) §6 advises that the correct response to
a partial failure is *retry* ("the operator or AI agent can fire the intent
again") — but v0.1 did not define what happens when an intent **is** retried. For
software side effects, a duplicate is merely redundant. For physical side effects,
a duplicate is dangerous: a retried `control_access` intent could unlock a door
twice, and a retried `notify_family` could send two SMS messages. This supplement
makes the advised retry safe.

---

## Delivery guarantee

**DoSync provides at-least-once delivery with optional deduplication.**

This is a deliberate, honest choice among the three classic models:

| Model | Behavior | Why not chosen |
|---|---|---|
| at-most-once | May lose an intent, never duplicates | Losing an emergency intent is unacceptable — a missed `ensure_safety` leaves someone unattended |
| **at-least-once + dedup** | Never loses; duplicates suppressed by key | **Chosen.** Correct for physical actions over an unreliable network |
| exactly-once | Never loses, never duplicates | Not honestly achievable over HTTP without a distributed transaction the protocol does not have |

A hub MUST NOT silently drop a submitted intent. When a client cannot confirm
that an intent was received (timeout, connection reset), it is expected to retry.
The idempotency key makes that retry safe.

---

## Idempotency key

The idempotency key is an **optional**, client-supplied string. A UUID v4 is
RECOMMENDED.

### Request

```jsonc
POST /v1/intent/async
{
  "intent":          "control_access",
  "urgency":         "alert",
  "context":         { "trigger": "fall_detected" },
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000"   // optional
}
```

The key MAY also be supplied as the `Idempotency-Key` HTTP header; if both are
present, they MUST match or the request is rejected with `400 Bad Request`.
(Header support is OPTIONAL for a conforming hub; the body field is the
normative location.)

### Behavior

A conforming hub MUST implement the following decision table. The "body hash" is
a stable hash (SHA-256) of the semantically meaningful intent fields — `intent`,
`urgency`, `subject`, `source`, `context` — and MUST exclude the idempotency key
itself, so that the same logical intent hashes identically whether or not a key
is attached.

| Condition | Hub behavior | Response |
|---|---|---|
| No key supplied | Treat as a new, unique intent (v0.1 behavior) | `200` new `intent_id` |
| Key, not seen before | Process normally; record `key → (body_hash, intent_id)` | `200` new `intent_id` |
| Key seen, **same** body hash | Do NOT re-execute. Return the original `intent_id` | `200` with `"idempotent_replay": true` |
| Key seen, **different** body hash | Reject — a key MUST NOT be reused for different content | `409 Conflict` |

### Why `409` on key reuse with a different body

This rule is a **security control**, not just hygiene. Without it, an attacker who
can predict or observe a client's idempotency keys could pre-register a key with a
benign body; when the client later submits a real intent with that key, the hub
would treat it as a duplicate and suppress it. In an emergency system, suppressing
an intent is as harmful as duplicating one. Binding the key to a body hash and
rejecting mismatches makes a key unusable for content the holder does not control.

---

## Retention window

A hub MUST retain idempotency keys for at least the intent-result retention
window so that a key lives at least as long as the result a client may poll for.

- Controlled by the same retention as intent results (`_INTENT_STORE_TTL`,
  default **300 seconds**).
- The window MUST be `>=` the maximum intent execution time plus a reasonable
  retry margin, so a slow-network retry still deduplicates.
- After the window expires, a key is forgotten; a request reusing an expired key
  is treated as new. This bounds memory and is acceptable because a retry that
  arrives more than the full window late is indistinguishable from a fresh intent.

---

## Interaction with other layers

- **Emergency urgency.** Idempotency operates *before* resolution and policy
  evaluation. An `emergency` intent with a key is deduplicated like any other —
  deduplication does not delay or block emergencies, it only prevents a literal
  duplicate of the *same* emergency from executing twice. The emergency policy
  bypass is unaffected.
- **Authentication.** Idempotency does not change the auth model. A request must
  still authenticate; the key is not a credential and grants no access.
- **Audit log.** A deduplicated replay does NOT produce a second audit entry. The
  original intent's single audit entry stands, preserving the one-intent-one-entry
  guarantee of Consistency Model §5.
- **Device-level dedup (Consistency Model §4, planned v0.4).** That mechanism
  deduplicates identical actions *within a single ActionPlan* produced by the
  resolver. It is independent of this protocol-level idempotency, which
  deduplicates *retried intents over the network*. The two address different
  layers and compose without conflict.

---

## Versioning

This mechanism is **additive and backward compatible**, so it is a MINOR protocol
increment per spec §10.4: **protocol 0.1 → 0.2**.

- A v0.1 client that never sends a key sees identical behavior to before.
- A v0.2 client gains safe retries by sending a key.
- No existing field changes type or is removed; no endpoint changes shape.

A hub advertising `X-DoSync-Protocol-Version: 0.2` supports idempotency. A v0.2
client SHOULD degrade gracefully against a `0.1` hub (which ignores the key field
and provides at-least-once without dedup).

---

## Conformance

A hub claiming protocol v0.2 idempotency support MUST:

1. Accept the optional `idempotency_key` field on intent submission without error.
2. Return the original `intent_id` (not re-execute) on a key+same-body retry.
3. Reject key reuse with a different body via `409 Conflict`.
4. Exclude the key from the body hash.
5. Retain keys for at least the intent-result window.
6. Never produce a second audit entry for a deduplicated replay.

These behaviors are verified by `tests/test_idempotency.py` in the reference
implementation.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
