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

## Urgency levels — protocol-controlled values

The urgency level is one of only two values the protocol controls directly (the other being the intent class name format). It has direct safety implications across the stack.

| Level | Value | Behavior |
|---|---|---|
| `emergency` | Bypasses all policy constraints. Executes immediately. Emergency-capable devices always included. |
| `alert` | High priority. Confirmation policies may apply. |
| `warning` | Elevated priority. Warrants attention but no immediate action. All policies apply. |
| `info` | Normal priority. All policies apply. Default for routine operations. |

Only `emergency` triggers the emergency override path. The three remaining levels go through full policy evaluation. The distinction between `alert` and `warning` is intentional: `alert` implies a condition that may require the system to act; `warning` implies a condition that should be logged and monitored but not acted upon automatically.

**When to use `warning`:** anomalies that are notable but not urgent — a high temperature reading, an unusual sensor pattern, a device behaving unexpectedly. The gpio_adapter uses `warning` for DHT22 temperature thresholds above 35°C: the reading is significant enough to log and notify, but not significant enough to bypass policies or trigger emergency-capable devices.

---

## Open intent class vocabulary — the protocol defines format, not meaning

DoSync v0.4 introduces a fundamental architectural change: intent classes are no longer hardcoded in the protocol. The protocol defines the *format* of an intent class name, not its *vocabulary*.

**The reasoning:**

A protocol that hardcodes domain-specific intent classes (`bedtime_routine`, `children_arrived_home`) is not infrastructure — it is an opinionated application framework for a specific domain. A hospital deploying DoSync should not have to work around `bedtime_routine`. A factory should not inherit vocabulary that has no meaning in its context.

The correct model is the same one used by every successful open protocol:

- **HTTP** defines methods (`GET`, `POST`, `DELETE`). It does not know about REST, GraphQL, or webhooks.
- **MQTT** defines topics and QoS levels. It does not know about temperature, presence, or emergencies.
- **MIME types** define a namespace (`type/subtype`). The registry is open — anyone can register new types.

DoSync follows this pattern. The protocol defines:

1. **The format constraint** — intent class names must match `^[a-z][a-z0-9_]*$` (lowercase, digits, underscores)
2. **The urgency taxonomy** — `emergency | alert | info` (these have safety implications and are protocol-controlled)
3. **Five universal intent classes** — seeded into every hub at initialization, valid in any physical environment regardless of domain

```
ensure_safety   emergency   Safety emergency — protect people and property
alert_anomaly   alert       Unexpected condition detected — investigate
control_access  alert       Manage physical access to a space
report_status   info        Generate a status report of the environment
notify          info        Push information to any target
```

**Why exactly five?** These are the only intents that are genuinely domain-agnostic. Every physical environment has emergencies, anomalies, access control, status reporting, and notifications. No other intents meet this bar — `bedtime_routine` is residential, `prepare_operating_room` is healthcare, `line_emergency_stop` is industrial. Those belong to domain packages, not the protocol core.

**Domain vocabularies are registered at the hub level:**

```bash
# Healthcare deployment
POST /v1/intent-classes
{
  "name": "prepare_operating_room",
  "urgency": "alert",
  "resolution_tags": ["medical", "lighting", "access"],
  "resolution_actuators": ["turn_on", "unlock", "notify"],
  "description": "Prepare an operating room for a procedure",
  "domain": "healthcare"
}

# Industrial deployment
POST /v1/intent-classes
{
  "name": "line_emergency_stop",
  "urgency": "emergency",
  "resolution_tags": ["industrial", "safety", "alarm"],
  "resolution_actuators": ["stop", "notify", "alarm"],
  "description": "Emergency production line shutdown",
  "domain": "industrial"
}
```

No code changes. No hub restart. No coordination with the protocol maintainers. The deploying organization owns its vocabulary.

**What cannot be changed:** the five universal intents are protected at the API level — they cannot be overridden or deleted. They are the invariant core of the protocol that every compliant implementation must support. Everything else is deployment-specific configuration.

**The implication for interoperability:** two DoSync hubs in different domains will always share the five universal intents. Domain-specific intents are scoped to the deployment. A hub migration guide or a multi-domain deployment should document which domain packages are active — but the protocol itself remains the common ground.

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

```bash
# Environment variable — set in .env or systemd service file
DOSYNC_UNREACHABLE_TTL=1800   # 30 minutes (default)
DOSYNC_UNREACHABLE_TTL=300    # 5 minutes — for high-availability deployments
DOSYNC_UNREACHABLE_TTL=3600   # 1 hour — for low-power device-heavy deployments
```

The TTL is not a health metric. It is an execution optimization. The Device Health Monitor is the correct tool for tracking device reliability over time — the TTL only determines how long the resolver waits before retrying a device that recently failed.

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
| Open intent vocabulary | The protocol defines format, not meaning. Domain vocabularies are deployment-specific. |
| Five universal intents | ensure_safety, alert_anomaly, control_access, report_status, notify — valid in any domain. |
| Four urgency levels | emergency > alert > warning > info. Only emergency bypasses policy constraints. |

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
