# DoSync Protocol — Design Principles

This document explains the deliberate design decisions behind DoSync — not what it does, but *why* it was designed this way. It is intended for engineers evaluating the protocol for production use, researchers studying its architecture, and contributors proposing changes.

---

> **One document, after two.** This file and `docs/DESIGN-PRINCIPLES.md` were
> both live for three months and drifted 265 lines apart — neither a stale copy
> of the other. The root held the founding principle (a mind, external, driving
> simple bodies); `docs/` held the rules on adapters, optional dependencies and
> how to write a test, one of which was consulted this week to decide that a
> discovery library belongs in the core install. A project whose reason for
> existing is that a system must not say two different things about itself does
> not get to keep two versions of its principles. Merged here; `docs/` now
> points at this file.

## Core principle: the protocol is infrastructure, not intelligence

DoSync's job is to translate a semantic intent into a coordinated set of device actions, execute those actions reliably, and produce a tamper-evident record of everything that happened.

That is the full scope of the protocol's responsibility.

DoSync does not decide whether an action was *wise*. It does not learn from outcomes autonomously. It does not adapt its behavior without explicit configuration. It does not interpret data for the operator.

These are not limitations — they are deliberate design constraints that make DoSync suitable for safety-critical environments.

---

## The intelligence lives in the mind, not the body

This is the deepest design commitment of the protocol, and the one that orients
everything else. It is the full statement of the principle the previous section
opens with.

**In DoSync, intelligence lives in an external mind — the AI that connects — never
in the device.** A device is a *body*: a set of actuators and sensors that declares
what it can do and lends its physical structure to whatever mind is driving it. The
device does not know *why* it is acting, *when* it should act, or *what* the larger
goal is. It knows only its own capabilities. The intelligence is borrowed, at
runtime, from outside.

This inverts the model most people inherit from science fiction, where the machine
*is* intelligent — the mind is built into the physical structure (the android, the
robot). DoSync assumes the opposite, and the opposite is what is actually buildable:
**the body is simple and cheap; the mind is external and connects.**

Consider the consequences, from the mundane to the ambitious:

- A **light** declares `turn_on`/`turn_off`. The mind decides it is an emergency
  and that the light should go to full brightness. The light does not know about
  emergencies.
- An **oven** declares `preheat`/`set_temperature`/`set_timer`. The mind knows
  *what* is being cooked and *when*; the oven is just heat the mind can direct.
- A future **cooking robot** is, architecturally, no different — a structure that
  declares `move`/`grasp`/`rotate`. Its intelligence is not in the chassis. A mind
  (a home AI, or any connecting agent) drives it through DoSync. The body is the
  same whether the mind is brilliant or absent; without a mind it simply does
  nothing, correctly.

The unifying frame: **DoSync is the nervous system that connects a mind to bodies.**
The Capability Manifest is how a body announces what it can do. The semantic intent
is how a mind expresses what it wants. The resolver maps one to the other. At no
point does the body need to be intelligent, and at no point should the protocol try
to make it so.

### Why this matters for what DoSync should and should not contain

This principle is not decoration — it decides scope. The most important consequence:

**DoSync must not contain the intelligence itself.** A recurring temptation is to
embed a language model inside the hub so it can "understand natural language." Under
this principle, that is a category error: it puts the mind inside the nervous system.
Natural-language understanding belongs to the connecting AI — that *is* its
intelligence. DoSync's job is to offer that external mind a clean, expressive
interface for discovering bodies and expressing intent (the MCP server, the intent
API, the Capability Manifest), not to be intelligent on its behalf.

Two practical implications follow:

- **Expanding what DoSync can touch is more central than improving how you talk to
  it.** The frontier is not faster language parsing; it is letting a mind act on
  *more of the physical world* — more transports (BLE, and beyond), more device
  classes, richer capabilities. A speaker, an oven, a robotic arm are all the same
  problem: a new body for an existing mind.
- **The body should not have to know DoSync exists.** Where possible, the adapter
  that speaks a device's native protocol lives in the *hub*, not the device. This
  lets DoSync lend intelligence to hardware that already exists and was never
  designed for it — which is exactly what "the mind is external" implies at the
  transport layer.

What this principle defers, deliberately, is the modeling of bodies with continuous
state and motion over time (an oven's rising temperature, a robot moving through
space). The current manifest models discrete capabilities. That is enough for the
devices that exist today, and the design should not *close the door* to continuous
capabilities later — but it should not build for them before a real body needs them.

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

## On perception, reflexes, and judgment

An AI that *perceives* a situation and fires an intent on its own — without a human telling it what to do — is a natural and powerful use of DoSync. A model watching a camera can decide "someone fell, ensure their safety" and fire the intent itself. This is agency, not automation: the model judges the situation from context, rather than following a pre-written `if-this-then-that` rule.

Three boundaries make this safe and keep the protocol clean.

**1. Perception lives in Layer 2, outside the protocol.** The component that watches a sensor or a camera, reasons about what it sees, and decides to fire an intent is a *client* of DoSync — not part of the protocol. DoSync is the response layer (Layer 1): it receives an intent, governs it, executes it, and records it. It does not perceive, and it does not interpret raw sensor data for the operator. A reference perception agent may ship alongside the protocol to demonstrate the pattern, but it is a client — device- and model-agnostic by design (pluggable frame source, pluggable perception model), never normative. Anyone may write their own; conformance is unaffected. This mirrors the rest of the protocol: the resolver, the transport, and the AI model are all pluggable and all live outside the normative core.

**2. Reflexes are not judgment — and must never pass through a model.** A decision splits by a single test: *if a wrong or late decision causes physical harm within the time it takes to call a model, it is a reflex; otherwise it is judgment.*

- **Reflex** — collision avoidance on a moving vehicle, an over-temperature cutoff, a machine emergency stop. Resolved in milliseconds, deterministically, locally: in the flight controller, in the operation guards, in threshold logic. A model round-trip (seconds) is far too slow; routing a reflex through an LLM is a safety defect, not a feature.
- **Judgment** — turning on a light for someone who entered a dark room, opening a gate, adjusting the environment, deciding a drone's next waypoint. Latency-tolerant and context-dependent. This is where a model earns its place — and where a fixed rule cannot capture the nuance (a model can decline to act at 3am when a rule would blindly fire).

A capable device may use both layers at once: for an autonomous drone, "don't hit the tree" is a reflex (flight control + guards) while "investigate that broken fence you saw" is judgment (model → intent → DoSync). The fast reflex keeps it safe; the slow judgment decides the mission.

**3. The model proposes; policy disposes; the log records.** An autonomous agent firing intents shifts some decision-making from Layer 3 (human) toward Layer 2 (model). That shift is graduated by consequence, never blanket:

- **Reversible, low-stakes actions** (a light, the climate, a notification): the agent may decide; the Policy Engine and audit log are the record.
- **Irreversible or high-stakes actions** (a lock, anything affecting physical safety, spending): the agent *proposes*; a confirmation policy or a human *decides*.

In all cases the proposed intent passes through the Policy Engine (confirm / block / modify) and the tamper-evident audit log before any device acts. The agent never actuates a device directly. This is the safety architecture for autonomy: the model proposes from judgment, policy imposes the guardrails, and the log makes the whole chain — what was perceived, what was proposed, what was done — verifiable after the fact.

**The honest limit.** Reliable perception — not failing to notice the fall, not hallucinating one — is only as good as the model doing the watching, and DoSync does not provide or guarantee it. For safety-critical detection (industrial hazards, life safety), the perception must be a deterministic reflex or a certified sensor, not a general-purpose model. DoSync coordinates the response; it does not vouch for the perception.

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

## On recurring operations

Some maintenance is not a task that gets done — it is a rate that has to be
matched. The audit chain is the clearest case: the reference deployment archived
28,189 entries on 2026-07-20 and was back to 16,223 five days later, roughly
2,800 per day. Archiving is not a job completed in July; it is an obligation that
recurs about weekly, forever, and grows with the deployment.

The same is true of exporting audit checkpoints, rotating credentials, and PKI
renewal. Each was, at some point, "done".

Two consequences for how this project tracks work:

- **A recurring operation is not closed by doing it once.** It closes by being
  scheduled, or by being written down as something a human must do on a rhythm,
  with the rhythm stated. "Archived the chain" is a log entry; "the chain needs
  archiving weekly at current volume" is the actual finding.
- **Manual is a valid answer, silence is not.** PKI rotation here is deliberately
  operator-driven rather than automated, and that is a defensible choice. What is
  not defensible is a guarantee that quietly depends on someone remembering. If
  an auditor asks "how often, and who ensures it", the answer must exist before
  they ask.

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

**The one exception, and it is not a tag filter: quarantine.** A device the
operator has withdrawn — a declarative device whose file was deleted, for
instance — is not a candidate for any intent, emergency included. Force-inclusion
exists to beat the *tag* filter, not to act on hardware the operator believes is
gone. Acting on a withdrawn device in an emergency would be the worse failure:
the operator planned around its absence.

The consequence is worth stating plainly, because it cuts against the guarantee
above: **a quarantined device will not respond to an emergency, even with
`emergency_capable=True`, and nothing about the emergency will announce that.**
Quarantine is therefore an operator decision with a safety consequence, and the
registry surfaces quarantined devices in the inventory (rather than hiding them)
precisely so that the decision stays visible. This was found in the reference
deployment, where a quarantined light had been entering every emergency for
weeks — the resolver had never filtered it, and `active()` said it should.

**Candidate reduction in practice** (1000-device deployment, realistic tag distribution):

| Intent | Candidates with index | Without index |
|---|---|---|
| `ensure_safety` | 94 | 1000 |
| `notify_family` | 18 | 1000 |
| `control_access` | 0 | 1000 |
| `save_energy` | 527 | 1000 |

The index is most effective for safety-critical intents with specific tags. Comfort and efficiency intents with common tags (`light`, `smart-plug`) show lower but still meaningful reduction.

---

## On adapters: what ships, and who answers for it

Adapters fall into three kinds, and the difference is a claim the project makes
rather than an accident of history:

- **Ecosystem** — implements an open standard or an open project: MQTT, Matter,
  BLE, MAVLink, the Home Assistant bridge. These belong in a protocol the way
  HTTP support belongs in a web framework.
- **Reference** — implements one vendor's product. WiZ and Shelly are here.
  They ship as worked examples of how an adapter is written, not as endorsement,
  partnership, or a promise to track anyone's firmware.
- **Infrastructure** — not a device technology (notifications).

The distinction exists because shipping vendor code silently says two things
this project does not mean: that it privileges those brands, and that it is a
smart-home product. Both are legible in the file tree. Deleting them would say
something equally wrong — they are the only executable answer to "how do I write
an adapter" — so the claim is declared (`adapter_kind`), exposed
(`GET /v1/adapters`) and tested instead.

**A new adapter must choose its kind.** Inheriting a flattering default is how a
classification stops meaning anything.

## On optional dependencies

A dependency needed to USE a capability the project offers by default cannot be
optional. Optional extras exist for hardware a deployment may or may not own —
`dosync[wiz]` because you have WiZ bulbs — and an advertised capability is not
hardware.

This appeared three times in two days, each time producing not friction but a
false belief:

- `bleak` in an extra: a user scanned, found nothing, and concluded DoSync does
  not support Bluetooth.
- The BLE adapter registered only on request: the library was installed and
  unused, so the same conclusion followed with the library sitting right there.
- `aiohttp` in an extra: a declarative adapter — whose only transport is HTTP —
  failed at EXECUTION, during an intent, rather than at load.

The last is the shape to watch for. A missing dependency that fails when the
hub starts is an inconvenience; one that fails when an emergency reaches a
device is a different category, and the difference is only visible if someone
asks *when* the failure lands.

## On loading adapters from a repository

DoSync does not download and execute adapter code from a remote source, and will
not. This is the same ruling as adapter-side fallback, for the same reason.

The entire argument of this protocol is that nothing acts on a physical device
without passing a policy and leaving a record. Fetching executable code from the
internet — code whose whole purpose is to actuate hardware — would place the
largest possible hole exactly where the guarantee lives. A bypass of one line
was closed here in July because it let an agent skip the policy engine; a remote
plugin loader is that hole with whole packages through it.

Three supported paths, covering different cases:

- **Ecosystem adapters**, in the package. The project answers for them.
- **Declarative adapters**, a file the operator writes describing an HTTP, MQTT
  or Modbus device. The operator answers for them. No code is executed that the
  operator did not write.
- **Third-party adapters via Python entry points** (group `dosync.adapters`) — a
  vendor publishes `dosync-adapter-x` and the operator installs it deliberately.
  The publisher answers for it, and installation is an explicit act with a supply
  chain behind it rather than a silent fetch.

  Such an adapter runs inside the hub with the hub's permissions, which is the
  cost of the arrangement and is not hidden: loading one is logged at WARNING and
  appended to the audit chain, because "what code was running when this happened"
  is a question an incident review asks. Its `adapter_kind` is set to
  `third_party` BY THE LOADER and not read from the plugin — where code came from
  is not the code's to assert, and a package claiming to be first-party code of
  this project is exactly the claim not to take on trust.

The difference between the third path and a plugin repository is consent and
attribution: someone chose to install it, and someone's name is on it.

## On adapter-side fallback ("local fallback without hub")

A recurring question in protocol design is whether adapters should be able to operate independently when the hub is unavailable — executing actions directly on physical devices without going through the capability resolution layer.

**DoSync deliberately does not implement this.** The reasoning:

A protocol cannot have a "mode without the protocol." If the hub is unavailable, the protocol is unavailable — this is correct and expected behavior, not a failure to be worked around. HTTP does not function without servers. Matter does not function without a fabric controller. DoSync does not function without a hub.

Bypass mechanisms that allow adapters to act without the hub would:

- Break the audit chain — actions executed without the hub produce no SHA-256 chained log entries
- Break the policy engine — safety constraints are not evaluated
- Break the semantic model — actions become commands again, which is exactly what DoSync was designed to avoid

**The correct resilience model for DoSync is hub availability, not adapter autonomy:**

- `FailurePolicy.RETRY` handles transient adapter failures
- Emergency snapshots (v0.2) re-fire critical intents on hub restart
- The `StateAwareResolver` TTL handles temporary device unavailability
- Hub deployment on reliable hardware (systemd service with `Restart=always`) handles hub restarts

These mechanisms collectively ensure that the hub recovers quickly from failures and that critical intents are not permanently lost. They do not attempt to replicate hub intelligence in the adapters — which would defeat the purpose of having a hub.

---

---

## On writing tests: assert the mechanism, not a symptom

A test must fail when the thing it names is broken. Three times in this project a
test passed while the mechanism it claimed to protect was removed, because it
asserted an *outcome* that something else also guaranteed:

- **The resolver scoring refactor.** A test compared the score `explain()` reports
  against the score the resolver decides with. Once both read one source, the
  comparison could not fail — it compared a value to itself. Fixed by asserting
  absolute values (a siren scores 52), which a changed weight breaks.
- **The sequence-gap test.** It edited `seq` on a sealed entry, so verification
  rejected the chain on the broken **hash**, never reaching the sequence check.
  Fixed by building entries with valid hashes and a missing number.
- **The archive head-mark test.** It asserted "no false alarm after archiving" —
  but the high-water-mark semantics already guarantee that, so deleting the
  archive-side fix did not fail it. Fixed by asserting what that fix actually
  buys: the mark **advances**, so a later truncation is still caught.

The pattern is identical each time: the assertion was true for a reason other
than the one under test. Two habits follow.

**Verify by deletion.** Before trusting a test, remove the code it protects and
watch it fail. A test that stays green has not been shown to test anything. This
is already routine here for features; it applies just as much to the tests
written alongside them.

**Name the mechanism in the assertion.** "Archiving does not raise a false alarm"
is a property of the system; several mechanisms could provide it. "The head mark
advances past its previous value" is a property of one mechanism, and only that
mechanism can satisfy it. When both are worth having, write both — but know which
one is load-bearing.

A test that cannot fail is worse than no test: it occupies the place where a real
one would go, and it reports success while the thing it names is gone.

## Summary

| Principle | What it means in practice |
|---|---|
| Mind external, body simple | Intelligence lives in the connecting AI, never in the device. DoSync is the nervous system; devices are bodies that declare capabilities and lend their structure. The protocol must not contain the intelligence itself. |
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
