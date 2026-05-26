---
title: Event vs Intent — The Architectural Difference That Matters
published: false
tags: iot, ai, architecture, dosync
series: DoSync Concepts
---

*DoSync Concepts is a series exploring the ideas behind the DoSync Protocol — the semantic layer between AI agents and physical systems.*

---

There's a distinction that comes up constantly when designing systems that connect AI to physical environments, and it's one that most protocols blur or ignore entirely.

The distinction is between an **event** and an **intent**.

They look similar on the surface. Both are messages. Both carry information about the world. But they describe fundamentally different things — and building a system that confuses them leads to brittleness, fragility, and automation that breaks exactly when it matters most.

---

## What an event is

An event is a description of something that happened.

```json
{
  "device": "pir-sensor-01",
  "type": "motion_detected",
  "timestamp": 1748000000,
  "location": "entrance"
}
```

An event is past tense. It's factual. It carries no opinion about what should happen next. The PIR sensor doesn't know whether this motion means "the kids just got home" or "there's an intruder" or "the cat walked by again." It just observed something and reported it asynchronously — fire and forget.

Events are the raw material of a physical system. Sensors generate them constantly — temperature readings, contact closures, power measurements, motion pulses. They are objective and dumb, in the best possible sense.

---

## What an intent is

An intent is a description of something that needs to happen.

```json
{
  "intent": "children_arrived_home",
  "urgency": "info",
  "context": {
    "trigger": "motion_detected",
    "time": "18:45",
    "day": "monday"
  }
}
```

An intent is future tense. It carries meaning. It encodes a goal — what the system should achieve — without specifying how to achieve it. The intent doesn't say "turn on lights 3, 7, and 9 at 80% brightness." It says "the kids are home." The infrastructure figures out the rest.

> **The leap from event to intent requires reasoning.** Something has to look at the motion event, consider the time, the day, the history, and decide: *this event means the kids just arrived.* That reasoning is exactly what AI agents are good at.

Concretely, that transition looks like this:

```
[PIR fires motion_detected at 18:45 on Monday]
        ↓
  AI agent reasons:
  - time: 18:45 → within arrival window
  - day: Monday → weekday
  - location: entrance → consistent with arrival
        ↓
  fires: children_arrived_home [info]
        ↓
  hub resolves against device registry:
  → 5 lights turn on
  → SMS sent to parents
  → audit log updated
```

The agent handled the *meaning*. The protocol handled the *coordination*.

---

## Why conflating the two breaks systems

Most smart home platforms today — Home Assistant automations, Matter scenes, Zigbee rules — are built on a hidden assumption: that the translation from event to action is a simple, static mapping.

```
IF motion_detected AND time > 18:00 AND day IN [Mon, Tue, Wed, Thu, Fri]
THEN turn_on(light_1), turn_on(light_2), send_sms("kids home")
```

This works until it doesn't. Add a new light and the rule doesn't know about it. Change the schedule and you rewrite rules. Move to a new home with different devices and you start over. The rule hardcodes both the reasoning ("this motion means the kids arrived") and the execution ("these specific lights, this specific message").

Separating events from intents breaks this coupling.

The AI agent handles the reasoning layer: event → intent. It observes the motion, considers the context, and decides what's happening. That reasoning can be as simple or as sophisticated as needed — a time-based rule, a learned pattern, or a full LLM inference.

The protocol handles the execution layer: intent → actions. Once the intent is declared, every device that registered as relevant participates automatically. Add a new light and it joins the response. Remove a device and the system adapts. No rules to rewrite.

---

## Why this matters beyond the home

The event/intent separation is not a smart home concept. It's an architectural pattern for any system where AI needs to act on physical infrastructure.

In a factory: a temperature sensor fires `threshold_exceeded` on a critical component — that's an event. The AI reasons about it: abnormal reading, production line active, no scheduled maintenance — and determines `execute_safe_shutdown`. That's an intent. The execution is then coordinated automatically across the line: equipment powers down in sequence, the supervisor is paged, a maintenance request is logged, cooling activates. None of those responses were hardcoded for that specific sensor reading. Each system declared what it could do, and the protocol assembled the response.

The pattern is always the same: the AI reasons about meaning, the infrastructure handles coordination. The temperature sensor does not know about the cooling system. The cooling system does not know about the sensor. The protocol coordinates both.

---

## How DoSync implements this separation

In DoSync, the separation is structural. Events flow in from sensors and adapters — raw, factual, carrying no prescription. Intents flow out from AI agents — meaningful, goal-oriented, carrying no implementation.

The hub sits in between: it receives intents, resolves them against every device's declared Capability Manifest, and builds an action plan at runtime. The resolver never sees the original event. The sensor never sees the resulting actions. The layers are cleanly decoupled.

That decoupling is what makes the system extensible by design. A new device joins the network, declares its capabilities, and automatically participates in every relevant intent — without any rule being written, without any configuration being changed.

That's the architectural difference that matters.

---

**DoSync Protocol:** https://github.com/giulianireg-spec/dosync-protocol
**License:** Apache 2.0

---

*Next in DoSync Concepts: Why physical AI systems need policies, not rules.*
