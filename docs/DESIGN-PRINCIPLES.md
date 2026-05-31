# DoSync Protocol — Design Principles

This document explains the deliberate design decisions behind DoSync — not what it does, but *why* it was designed this way. It is intended for engineers evaluating the protocol for production use, researchers studying its architecture, and contributors proposing changes.

---

## Core principle: the protocol is infrastructure, not intelligence

DoSync's job is to translate a semantic intent into a coordinated set of device actions, execute those actions reliably, and produce a tamper-evident record of everything that happened.

That is the full scope of the protocol's responsibility.

DoSync does not decide whether an action was *wise*. It does not learn from outcomes autonomously. It does not adapt its behavior without explicit configuration. It does not interpret data for the operator.

These are not limitations — they are deliberate design constraints that make DoSync suitable for safety-critical environments.

---

## The three-layer model

Any deployment of DoSync operates within a three-layer model:

```
Layer 1 — Data (DoSync)
  Deterministic, auditable, structured.
  The protocol generates clean data: execution records, device states,
  audit log entries, intent outcomes. Every action is logged with
  a tamper-evident SHA-256 chain. This layer never lies.

Layer 2 — Interpretation (optional AI layer)
  Contextual, assistive, non-authoritative.
  An AI system (via the MCP server or any integration) can read
  Layer 1 data and translate it into human-readable insights:
  "Device X has been failing more than usual since 14:00."
  This layer assists — it does not decide.

Layer 3 — Decision (human operator)
  Final, accountable, irreplaceable.
  A human reads the data (directly or via Layer 2 interpretation)
  and decides what to do. This layer holds responsibility.
```

**Layer 1 must never be bypassed or replaced by Layer 2.** The AI interpretation is only as good as the underlying data. If Layer 1 is clean and detailed, Layer 2 can be useful. If Layer 1 is ambiguous or incomplete, Layer 2 amplifies the ambiguity.

**Layer 3 must never be replaced by Layer 2.** An AI interpreting data correctly is not the same as a human taking responsibility for a decision. In regulated environments — industrial, healthcare, public safety — the accountability chain requires a human at the end. An operator cannot delegate responsibility to an AI interpretation.

---

## Why DoSync does not learn autonomously

A natural evolution of the resolver would be to update device scores based on execution history — penalizing devices that fail frequently, rewarding devices that consistently succeed. This appears useful and has been deliberately rejected for the default resolver.

The reasoning:

**Unpredictability in critical environments.** A resolver that modifies its own behavior based on history produces different results for the same input over time. In a factory, a hospital, or any safety-critical environment, this unpredictability is unacceptable. Operators need to know that if they configure the system correctly today, it will behave the same way tomorrow.

**Feedback loop risk.** If a device's score drops below the inclusion threshold because it failed during a network outage, the resolver stops including it. Without inclusion, there are no new execution attempts. Without new attempts, the score cannot recover. The device is effectively silenced by a transient failure — potentially a critical device in a critical scenario.

**Domain mismatch.** Learned patterns make sense in a home with stable routines. They are dangerous in an industrial environment where variability is a signal of a problem, not a pattern to learn from. A protocol designed for general use cannot optimize for one domain at the cost of others.

**The correct model for device health** is observability, not autonomy: monitor execution outcomes, surface anomalies as alerts, and let the human operator decide whether to adjust the configuration. DoSync provides the data. The operator makes the decision.

---

## On unreachable devices and transient failures

The `StateAwareResolver` tracks device state in memory. When a device fails to respond — a network timeout, a low-power sleep state, a transient outage — the resolver marks it as `unreachable` and excludes it from subsequent action plans for a configurable period.

This behavior is controlled by `DOSYNC_UNREACHABLE_TTL` (default: 1800 seconds).

The design decision here is deliberate and worth explaining.

**Why exclude unreachable devices at all?** Without exclusion, every intent resolution that includes an unreachable device pays the full adapter timeout cost — potentially blocking execution for seconds. In emergency scenarios, that latency is unacceptable. Marking a device unreachable after its first failure makes subsequent resolutions fast and predictable.

**Why a TTL, not permanent exclusion?** A device that failed at 03:00 because the home WiFi rebooted is not a broken device — it's a temporarily unavailable one. Permanent exclusion would require manual intervention to restore it. A TTL means the device automatically re-enters the resolver's consideration after the configured period, without any operator action. The system recovers on its own.

**Why not learn from failure patterns?** The TTL is a blunt instrument by design. It does not penalize devices that fail often more than devices that fail once. It does not track failure history. Once the TTL expires, the device is treated exactly as it was before the failure. This is consistent with the broader principle that DoSync does not modify its behavior based on historical patterns — the same input always produces the same output.

**The failure pattern this addresses.** Production data from the reference deployment revealed a concrete scenario: `save_energy` executed at night sent UDP commands to WiZ bulbs that were physically off and in low-power state. The adapter timed out, the intent resolved as `partial`, and the audit log recorded 0% success for those devices. With TTL-based exclusion active, after the first timeout the bulbs are excluded for the configured window — subsequent `save_energy` intents resolve faster and without unnecessary UDP traffic to sleeping devices.

**Configuring the TTL.** The default of 1800 seconds (30 minutes) suits home deployments where transient failures are typically short-lived. Industrial or hospital deployments with stricter availability requirements may lower this value to reduce re-inclusion latency. Deployments where devices frequently enter low-power states may raise it to avoid constant re-exclusion cycles.

```bash
# Environment variable — set in .env or systemd service file
DOSYNC_UNREACHABLE_TTL=1800   # 30 minutes (default)
DOSYNC_UNREACHABLE_TTL=300    # 5 minutes — for high-availability deployments
DOSYNC_UNREACHABLE_TTL=3600   # 1 hour — for low-power device-heavy deployments
```

The TTL is not a health metric. It is an execution optimization. The Device Health Monitor is the correct tool for tracking device reliability over time — the TTL only determines how long the resolver waits before retrying a device that recently failed.

---

## PKI rotation policy

DoSync's local PKI has two components with different rotation schedules:

```
certs/
├── ca.crt / ca.key    — CA root. Valid 10 years. Rotated manually and rarely.
└── hub.crt / hub.key  — Hub certificate. Valid 1 year. Rotated annually.
```

**The CA is not rotated annually.** The CA is the root of trust for every client that connects to the hub — the Mac, `certify.py`, Claude Desktop, any adapter. Rotating the CA means every client loses trust and must receive the new CA cert before reconnecting. This is a significant operational event that should happen deliberately, not on a schedule.

**The hub certificate is rotated annually.** It is signed by the CA and can be replaced without touching the CA or redistributing anything to clients. The CA cert on the Mac remains valid after a hub cert rotation.

### Checking certificate status

```bash
# On the Pi — verify PKI health and days remaining
python3 -m dosync.security verify

# Or with the rotation script in check-only mode
bash rotate_pki.sh --check
```

### Annual rotation procedure

The `rotate_pki.sh` script automates the rotation:

```bash
# On the Pi
cd ~/dosync-protocol

# Check state first
bash rotate_pki.sh --check

# Rotate when hub cert is within 30 days of expiry (or use --force)
bash rotate_pki.sh

# The script:
#   1. Backs up current hub.crt and hub.key to certs/backup/<timestamp>/
#   2. Calls: python3 -m dosync.security renew hub
#   3. Verifies the new cert chains correctly to the CA
#   4. Restarts the dosync systemd service
#   5. Confirms the hub came back up
#   6. Prints manual steps for the Mac
```

After running the script, no Mac-side action is required unless the CA itself changed (it doesn't in a normal annual rotation). The Mac trusts the CA, and the new hub cert is signed by the same CA.

### When the CA must be rotated

CA rotation is rare and must be planned. It is necessary only if:

- The CA private key (`ca.key`) is compromised or suspected compromised
- The CA is approaching its 10-year expiry
- A deliberate security policy requires shorter CA lifetimes

When the CA is rotated, every client that has the old `ca.crt` in its trust store must receive the new one. For the reference deployment this means:

1. Generate new CA: `python3 -m dosync.security setup --force`
2. Copy new CA to Mac: `scp rgiuliani@<pi-ip>:~/dosync-protocol/certs/ca.crt ~/Desktop/dosync-ca.crt`
3. Update Claude Desktop config with the new CA cert path
4. Reissue all adapter certs: `python3 -m dosync.security renew gpio`
5. Restart the hub

CA rotation is a deliberate operational event, not an automated one.

---

## What the audit log is for

The SHA-256 tamper-evident audit log is not a debugging tool. It is an accountability infrastructure.

Every intent execution, every device action, every policy decision is logged with a cryptographic chain. Modifying any entry breaks the chain — making tampering detectable.

This design serves several purposes:

- **Post-incident analysis** — after any unexpected outcome, the full execution history is available for reconstruction
- **Regulatory compliance** — in environments with audit requirements, the log provides a verifiable record of system behavior
- **AI interpretation substrate** — the log is structured and detailed enough that an AI system can analyze it and surface meaningful insights without any loss of fidelity
- **Human accountability** — the log makes it possible to answer "what happened, when, and why" with precision

The log should be preserved, backed up, and treated as critical infrastructure — not as debug output to be rotated and discarded.

---

## On AI integration

DoSync ships a native MCP server that allows any LLM with MCP support to query hub state, fire intents, and read the audit log. This is intentional — AI agents are a primary use case for the protocol.

The design principle for AI integration is:

**AI can observe and act. It cannot override safety constraints.**

- An AI can fire any intent within the normal policy framework
- Emergency intents bypass policy constraints — but this is a protocol-level design, not an AI privilege
- An AI cannot modify device manifests, policies, or the audit log
- An AI cannot grant itself permissions that a human operator has not configured

The MCP server exposes the protocol's capabilities, not elevated access. An AI acting through DoSync operates within the same constraints as any other client.

---

## On domain applicability

DoSync's 5-layer architecture is domain-agnostic. The same protocol stack operates in a home, a hotel, a factory, or a smart building.

However, domain applicability has limits that must be stated clearly:

**DoSync is not certified for safety-critical medical applications.** Using DoSync in the direct care pathway of medical devices requires certifications (IEC 62304, ISO 14971) that the protocol does not currently hold. Appropriate use in healthcare is in peripheral systems — lighting, access control, comfort — never in the critical path of clinical decisions.

**DoSync does not replace domain-specific safety systems.** A factory fire suppression system, a hospital emergency call system, or a building evacuation system should not be replaced by DoSync. DoSync can complement these systems — coordinating non-safety-critical devices in response to their signals — but never replaces them.

**The protocol is infrastructure. The safety model belongs to the deployment.** DoSync provides the tools for safe operation: policy engine, audit log, emergency override, certification CLI. How those tools are configured and what safeguards surround them is the responsibility of the deploying organization.

---

## On the tag index and candidate selection strategy

As of v0.3, `CapabilityRegistry` maintains an inverted tag index — a dictionary mapping each tag to the set of device IDs that declare it. This index is updated incrementally on every `register()` and `unregister()` call.

**Why an inverted index?** The original resolver iterated all registered devices on every intent resolution — O(n). With the index, candidate selection is O(|tags| + |candidates|): instead of scanning all devices, the resolver takes the union of the index sets for the intent's resolution tags, then scores only that subset.

**Why union, not intersection?** Two candidate selection strategies were considered:

- **Intersection** (`find_by_required_tags`): returns devices that have ALL of the queried tags. Useful for queries like "thermostats in the living room" — requires `thermostat` AND `living_room` simultaneously.
- **Union** (`find_by_tags`): returns devices that have ANY of the queried tags. Correct for semantic intent resolution — `ensure_safety` wants devices relevant to `emergency` OR `alarm` OR `door-lock`, not devices that are simultaneously all three.

The intersection method exists in `CapabilityRegistry` as a utility for external queries but is deliberately not used in `resolve()`. Applying intersection in `resolve()` caused safety-critical devices (lights with `emergency_capable=True` but no `alarm` tag) to be excluded from emergency action plans — a direct safety regression.

**Emergency-capable devices are always candidates on emergency intents.** Regardless of tag overlap, any device with `emergency_capable=True` is included in the candidate set when `urgency == EMERGENCY`. This is a hard safety guarantee: the tag filter must never silently exclude a device that was explicitly configured to respond to emergencies.

**Candidate reduction in practice** (1000-device deployment, realistic tag distribution):

| Intent | Candidates with index | Without index |
|---|---|---|
| `ensure_safety` | 94 | 1000 |
| `children_arrived_home` | 25 | 1000 |
| `control_access` | 0 | 1000 |
| `save_energy` | 527 | 1000 |

The index is most effective for safety-critical intents with specific tags. Comfort and efficiency intents with common tags (`light`, `smart-plug`) show lower but still meaningful reduction.

---

## Summary

| Principle | What it means in practice |
|---|---|
| Deterministic resolver | Same input always produces same output. No autonomous learning. |
| Tamper-evident audit log | Every action is logged and verifiable. Nothing is hidden. |
| Human decision layer | DoSync informs. Humans decide. AI assists, never replaces. |
| Policy engine | Safety constraints are explicit, configurable, and auditable. |
| Domain agnosticism | The protocol works anywhere. Safety configuration is deployment-specific. |
| AI as observer and actor | AI can use DoSync. It cannot override its safety model. |
| Unreachable device TTL | Transient failures are excluded temporarily, not permanently. Recovery is automatic. |
| PKI rotation policy | Hub cert rotates annually. CA rotates only on compromise or expiry. Never automated. |

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
