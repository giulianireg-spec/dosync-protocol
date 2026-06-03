# Why your smart home breaks the moment you add an AI agent

*DoSync Concepts — Part 5 of 5*

*[Part 1: What is Semantic Intent?](https://dev.to/giulianiregspec/what-is-semantic-intent-and-why-its-not-the-same-as-a-command) · [Part 2: Why Hardcoded Automations Fail](https://dev.to/giulianiregspec/why-hardcoded-automations-fail-ai-agents) · [Part 3: Event vs Intent](https://dev.to/giulianiregspec/event-vs-intent-the-architectural-difference-that-matters) · [Part 4: Policies, Not Rules](https://dev.to/giulianiregspec/rules-arent-enough-once-an-ai-is-in-the-loop)*

---

You add a new smoke detector to your home network. It registers with the hub. Thirty seconds later, when someone fires an `ensure_safety` intent, the detector is part of the response — no automation written, no rule updated, no developer intervention.

How is that possible?

The answer is capability abstraction. And it's the design decision that separates systems that work with AI from systems that merely tolerate it.

---

## The device that knows its own role

Every existing smart home protocol treats a device as a passive endpoint. The device waits. Something external — an app, a rule engine, a developer's script — decides when to call it and what to tell it. The device's only job is to execute.

This model has a hidden assumption: that someone, somewhere, anticipated every scenario the device would be relevant for. They wrote the rule. They tested it. They kept it updated as new devices joined the network.

That assumption holds when humans are in the loop. It breaks the moment an AI agent enters the picture — because the AI doesn't have the rulebook. It has a goal. And the gap between "I have a goal" and "I know which devices to activate" is exactly the integration problem that makes AI-IoT systems fragile.

The fix is to move the knowledge of *when a device is relevant* from the rulebook into the device itself.

---

## The Capability Manifest — a declaration, not an API

In DoSync, every device publishes a Capability Manifest when it joins the network. Here's a real one from the production deployment — a Philips WiZ bulb running on a Raspberry Pi 5:

```json
{
  "device_id": "wiz-living1-01",
  "device_name": "Living Room — Bulb 1",
  "manufacturer": "Philips",
  "model": "WiZ RGBW Tunable",
  "firmware": "1.0.0",
  "category": "actuator",
  "tags": ["light", "climate", "smart-plug", "emergency", "wiz"],
  "actuators": [
    { "id": "wiz-living1-01-turn_on",       "type": "turn_on",       "description": "Turn on" },
    { "id": "wiz-living1-01-turn_off",      "type": "turn_off",      "description": "Turn off" },
    { "id": "wiz-living1-01-set_brightness","type": "set_brightness", "description": "Set brightness 0-100%" },
    { "id": "wiz-living1-01-set_color",     "type": "set_color",     "description": "Set RGB color" }
  ],
  "sensors": [],
  "emergency_capable": true,
  "adapter": "wiz",
  "adapter_config": { "ip": "192.168.100.28" }
}
```

This isn't an API specification. It's a declaration of identity and relevance. The device is answering three questions at once:

- **What can I do?** — turn on, turn off, set brightness, set color
- **Where do I fit?** — I'm a light, I'm a smart plug, I'm an emergency device
- **When do I matter?** — I'm emergency-capable; include me when safety is at stake

The AI agent never needs to know the device's native protocol. It fires:

```json
{
  "intent": "ensure_safety",
  "urgency": "emergency"
}
```

The resolver reads every registered manifest, scores each device for relevance — weighting tag overlap, location context, emergency bonus, and actuator match — and builds the action plan. The bulb is included because of its `emergency` tag and `emergency_capable: true` — not because anyone hardcoded it. Add ten more bulbs tomorrow with the same manifest structure, and they participate immediately in every emergency response.

---

## `emergency_capable` is a contract, not a flag

This boolean deserves more attention than it usually gets.

When a device declares `emergency_capable: true`, it's not filling in a form field. It's making a commitment to the protocol:

*I will respond to emergency intents without confirmation. I accept being included in the audit trail as a critical actor. I understand that my actions in emergencies will be logged with a tamper-evident SHA-256 chain.*

The protocol enforces this contract. Emergency-capable devices bypass the normal policy evaluation flow. They're always included as candidates in emergency intent resolution, regardless of tag overlap. And every action they take is logged — precisely because actions taken during emergencies are consequential enough to require a verifiable record.

This is the correct place for a safety guarantee to live. Not in a rule written by a developer who may or may not have anticipated the edge case. In the device's own declaration, verified and enforced by the protocol at runtime.

Compare the two designs:

```
Rule-based:  developer writes "if emergency, unlock frontdoor-01"
             → breaks when frontdoor-01 is replaced
             → breaks when a second entrance is added
             → breaks when the emergency scenario changes

Capability:  device declares emergency_capable: true
             → protocol enforces the contract
             → survives device replacement, new entrances, new scenarios
             → audit trail is automatic
```

---

## Why this is different from OpenAPI, MCP, and service registries

Capability abstraction is an old pattern. OpenAPI schemas describe what an HTTP service can do. MCP tool descriptions tell an LLM which tools are available and when to use them. Service registries in microservices architectures let services announce themselves at runtime.

DoSync's Capability Manifest is in this family. But there's a specific difference that matters for physical systems.

OpenAPI and MCP describe *interfaces* — the exact shape of inputs and outputs. They're precise but context-free. An OpenAPI schema for a door lock tells you the `/unlock` endpoint accepts a `duration_seconds` integer. It doesn't tell you that this lock is at the main entrance, that it's relevant in emergencies, or that it should be included in a safety response but not an energy-saving routine.

The Capability Manifest adds *semantic context* on top of interface description. The `tags` field isn't a type system — it's a declaration of relevance across scenarios. `["door-lock", "entrance", "emergency"]` tells the resolver three things that no API schema can express: what the device is, where it lives, and when it matters.

This distinction exists because AI agents reason at the semantic level, not the API level. An LLM detecting an emergency thinks "there's a safety situation" — not "send a PUT request to /api/v1/lock/frontdoor-01/state with body `{locked: false, duration: 300}`". The abstraction layer has to meet the AI where it operates — at the level of meaning, not syntax.

---

## The device as a participant

Here's the shift capability abstraction enables, stated plainly:

In the command model, adding a device means updating your automations. The device is inert until a human decides it should participate in something.

In the capability model, adding a device *is* the integration. The manifest is the contract. From the moment it registers, the device participates in every scenario it declared itself relevant for.

```
Before:  new device → engineer writes automation → device participates in scenario A
         new scenario B → engineer writes automation → device participates in scenario B

After:   new device registers manifest → device participates in all relevant scenarios
         new scenario added to protocol → device participates automatically
```

This is not a small operational difference. In a home with 40+ devices, maintaining the "before" model means hundreds of automation rules, each one a potential failure point when a device changes or a scenario is updated. The "after" model has one integration surface per device — the manifest — and it never needs to be updated when scenarios change.

---

## What the manifest teaches manufacturers

If you're building a device that will coexist with AI agents, the Capability Manifest is the interface that matters more than your API documentation.

A well-designed manifest answers the questions AI systems actually ask:

- Will this device respond in an emergency? → `emergency_capable: true/false`
- What kind of device is this? → `category` + semantic `tags`
- What can it physically do? → `actuators` with meaningful type names
- What can it sense? → `sensors` with type and unit
- How does the protocol talk to it? → `adapter` + `adapter_config`

A poorly designed manifest — missing location tags, wrong `emergency_capable` value, generic actuator types — means the device is invisible to the AI in exactly the scenarios where it should matter most. The resolver can only work with what the device declares.

Tag your devices correctly. The rest is automatic.

---

## The idea worth keeping

Five posts ago, we started with a simple observation: AI agents express goals, not commands. Every post since then has been an answer to the same question — *what does a system have to look like, at each layer, to bridge that gap?*

Semantic intent replaced commands. Policies replaced rules. Events were separated from intents. And now capability abstraction replaces the developer who used to be the translator between device APIs and AI goals.

The translator was always the fragile part. Every time a device changed, the translator broke. Every time a new scenario appeared, the translator had to be updated. Every time an AI agent expressed something that wasn't anticipated, the translator failed silently.

Capability abstraction removes the translator. The device speaks for itself. The AI listens. The protocol enforces the contracts between them.

That's not a smart home feature. That's the infrastructure for any physical environment where AI needs to act reliably.

---

**GitHub:** https://github.com/giulianireg-spec/dosync-protocol  
**Website:** https://dosync.dev  
**License:** Apache 2.0

---

*DoSync Concepts is a series exploring the ideas behind the DoSync Protocol — the semantic layer between AI agents and physical systems.*
