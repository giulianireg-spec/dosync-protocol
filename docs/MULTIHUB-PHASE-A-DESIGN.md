# DoSync Multi-Hub — Phase A Design: Assisted Failover Coordination

**Status:** Design (pre-implementation)
**Scope:** Phase A of §11 implementation — hub coordination with operator-assisted failover
**Validated by:** architecture panel (assisted-failover over naive auto-promotion)
**Test topology:** Pi (`<hub-address>`) = primary · Mac (`<standby-address>`) = standby

---

## What Phase A delivers (and what it deliberately does NOT)

Phase A proves that §11 is **implementable** by building the coordination layer
between two real hubs on a LAN. It implements failover of **role**, with a
safeguard that detects when failover of **state** would be destructive.

| Delivered in Phase A | Deferred to Phase B |
|---|---|
| Mutual hub discovery (Pi ↔ Mac) | Registry replication (standby holds primary's devices) |
| Peer heartbeat polling (the standby watches the primary) | Audit-log replication across hubs |
| Failure detection: distinguish "primary dead" vs "I lost the network" | Network database (PostgreSQL) migration |
| Split-brain signalling (report uncertainty, never silently corrupt) | Automatic promotion with quorum |
| Operator-assisted promotion (human confirms; never auto) | — |
| State-divergence safeguard (refuse destructive promotion) | — |
| `multi_hub_capable: true` in heartbeat (§11.8) | — |

**The honest limitation, stated up front:** without Phase B replication, a
promoted standby does not hold the primary's devices. Phase A makes this *safe*
by detecting the divergence and refusing to auto-promote into a destructive
state — it never hides it.

---

## Why assisted, not automatic

The naive §11.7 reading — "standby promotes itself after 3 missed heartbeats" —
introduces the split-brain that §11.6 warns about. If the network partitions
(standby cannot see primary, but primary is alive and serving its 34 devices),
an auto-promoting standby creates two primaries writing to diverging audit logs.
The SHA-256 chain breaks; `verify()` returns False; the accountability model
that is the core of DoSync is destroyed.

Phase A avoids this **without** the cost of quorum (which needs a third node we
don't have) by keeping a human in the loop. The standby detects and *proposes*;
the operator confirms. A human will not promote a standby while they can see the
primary is alive. This is the correct availability guarantee for DoSync's scale,
and it is declared honestly: **assisted failover, not automatic.**

---

## Component: the Hub Monitor

A new component runs inside a hub configured as `standby`. It is inert on a
`primary`. It does NOT touch the database, the resolver, or device adapters — it
only observes a peer and signals.

```
DOSYNC_HUB_ROLE=standby
DOSYNC_PRIMARY_URL=https://<hub-address>:47200   # the peer to watch
DOSYNC_HEARTBEAT_INTERVAL=5      # seconds (spec recommended)
DOSYNC_FAILURE_THRESHOLD=3       # consecutive misses before "primary may be down"
```

### State machine

The monitor occupies one of four states, derived only from observation:

```
            primary heartbeat OK
        ┌──────────────────────────┐
        ▼                          │
   WATCHING ───3 misses + peer ────► PRIMARY_DOWN ──operator confirms──► (promotion
   (passive)   network reachable     (proposes promotion)                 handled
        │                                                                 manually)
        │ 3 misses BUT peer's
        │ network unreachable
        ▼
   UNCERTAIN  (possible partition — signals "cannot confirm primary state",
              refuses to propose promotion)
```

- **WATCHING** — primary responds to heartbeat. Standby stays passive. (Normal state.)
- **PRIMARY_DOWN** — primary missed N heartbeats AND the standby can otherwise
  reach the network (see liveness probe below). The standby *proposes* promotion
  to the operator. It does NOT promote itself.
- **UNCERTAIN** — primary missed N heartbeats BUT the standby's own network
  looks degraded. This is a possible partition. The standby signals uncertainty
  and refuses to propose promotion. (§11.4 guarantee 5: no silent split-brain.)
- **STANDBY_ABSENT** is not a monitor state — it is simply the monitor not
  running (Mac asleep). The primary treats this as normal and never degrades.

### Distinguishing "primary dead" from "I lost the network"

This is the panel's required safety condition. Before concluding the primary is
down, the standby runs a **liveness probe** against an independent target on the
LAN (the default gateway, or a second known host). Logic:

| Primary heartbeat | Liveness probe (gateway) | Conclusion | State |
|---|---|---|---|
| Fails ×N | Succeeds | Primary is down, my network is fine | PRIMARY_DOWN → propose |
| Fails ×N | Also fails | My network is degraded — cannot judge primary | UNCERTAIN → hold |
| OK | — | Primary healthy | WATCHING |

This single probe does not give true quorum, but it cheaply separates the two
failure modes that matter, which is what makes assisted promotion safe.

---

## State-divergence safeguard

Before the monitor proposes promotion, it compares its own registry against the
last-known primary registry (captured from heartbeat `devices` count and, when
reachable, the public device list). If they diverge materially:

```
Primary last known: 34 devices
This standby:       23 devices
→ Promotion would LOSE 11 devices. The monitor marks the proposal
  "DESTRUCTIVE" and requires an explicit override flag from the operator,
  separate from ordinary confirmation.
```

This converts the Phase-B gap (no replication yet) from a silent hazard into an
active warning. The operator is told exactly what promotion would cost.

---

## Endpoint additions (minimal, backward compatible)

1. **`multi_hub_capable: true`** added to `GET /v1/hub/heartbeat` (§11.8 asks for
   this; declares the hub speaks the coordination protocol).

2. **`GET /v1/hub/peers`** (new) — returns the monitor's current view:
   ```json
   {
     "role": "standby",
     "monitor_state": "WATCHING",
     "primary_url": "https://<hub-address>:47200",
     "primary_last_seen": "2026-06-13T19:44:39Z",
     "consecutive_misses": 0,
     "primary_devices_last_known": 34,
     "local_devices": 23,
     "state_divergent": true,
     "promotion_safe": false
   }
   ```
   On a primary, returns `{"role": "primary", "monitor_state": "n/a"}`.

3. **`POST /v1/hub/promote`** (new, operator action) — promotes a standby to
   primary. Requires the monitor to be in `PRIMARY_DOWN` state. If the proposal
   is marked DESTRUCTIVE (state divergence), requires `{"force": true}` in the
   body; otherwise returns `409 Promotion would lose state` with the divergence
   detail. This is the human-in-the-loop gate.

No existing endpoint changes shape. A hub with no monitor configured behaves
exactly as today (single-hub).

---

## Test plan (three scenarios, real Pi + Mac)

The panel's three scenarios, runnable on the two machines:

**Scenario 1 — Steady state (WATCHING).**
Pi primary up, Mac standby running. Monitor polls, primary responds, monitor
stays WATCHING. `GET /v1/hub/peers` on the Mac shows `monitor_state: WATCHING`,
`consecutive_misses: 0`. Assert the Mac never proposes promotion.

**Scenario 2 — Real primary failure (PRIMARY_DOWN).**
Stop the dosync service on the Pi (`sudo systemctl stop dosync`). Mac's monitor
misses 3 heartbeats, liveness probe to the gateway still succeeds → transitions
to PRIMARY_DOWN, proposes promotion. Because Mac has 23 devices vs Pi's last-known
34, the proposal is marked DESTRUCTIVE. Assert `POST /v1/hub/promote` without
`force` returns 409; with `force:true` it promotes and logs the device loss.

**Scenario 3 — Network partition (UNCERTAIN).**
Block the Mac's route to the Pi (firewall rule) while the Pi stays up serving its
devices. Mac misses 3 heartbeats BUT the liveness probe also degrades (or: probe
a target only reachable via the same path) → transitions to UNCERTAIN, does NOT
propose promotion. Assert the Mac never promotes during a partition while the
primary is alive. This is the scenario only two real machines can exercise.

Unit-testable parts (no hardware): the state machine transitions, the divergence
detection, and the promote endpoint's 409/force logic — via the TestClient
pattern, feeding the monitor synthetic heartbeat responses.

---

## What this is NOT (anti-scope, to prevent the next contributor from "completing" it wrong)

- NOT automatic promotion. A human confirms every promotion. By design.
- NOT quorum or Raft. No third node. Deferred (Phase B / enterprise).
- NOT state replication. The standby does not hold the primary's devices yet.
  The safeguard detects this; it does not fix it. (Phase B.)
- NOT a shared SQLite file between hosts. §11.6 forbids it; Phase B needs a
  network DB instead.

---

## Honesty declaration (for the spec / a future implementer)

DoSync multi-hub Phase A provides **operator-assisted failover of hub role**,
with detection of network partition and of state divergence. It does NOT provide
automatic failover, quorum-based election, or state replication. A standby
promoted without Phase B replication serves an empty-of-primary-devices registry
and warns the operator before doing so. Automatic promotion and replication are
declared future work, consistent with how the protocol declares its other
guarantees (e.g. at-least-once delivery for idempotency).

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
