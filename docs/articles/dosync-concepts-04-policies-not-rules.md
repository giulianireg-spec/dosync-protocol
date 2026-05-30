---
title: Rules Aren't Enough Once an AI Is in the Loop
published: false
tags: iot, ai, architecture, dosync, homeautomation, security
series: DoSync Concepts
---

*DoSync Concepts is a series exploring the ideas behind the DoSync Protocol — the semantic layer between AI agents and physical systems.*

---

Picture this: it's 03:00. An AI agent detects an intruder at the front entrance and fires `ensure_safety [emergency]`. The action plan includes unlocking the front door for emergency services.

But someone wrote a rule three months ago: *never unlock after midnight.*

The rule wins. The door stays locked.

This is not a bug in the AI. It's not a bug in the rule. It's a design problem — and it's the exact problem that appears when you build physical AI systems on top of automation logic that was never designed for an AI to act on.

The difference between a **rule** and a **policy** looks small from a distance. Both constrain behavior. Both say "don't do X in situation Y." But they operate at different levels of the system — and getting this distinction wrong in a physical environment has real consequences.

---

## What a rule is

A rule is a static, pre-written instruction that maps a condition to an action.

```
if time > 22:00:
    lock.lock()

if motion_detected and away_mode:
    alarm.activate()

if temperature > 28:
    ac.turn_on()
```

Rules live in the automation layer. They're written by a human, at configuration time, for a specific scenario that was anticipated in advance. They're deterministic in the right circumstances: given the same input, they always produce the same output.

Rules work well when the world is predictable, the device set is stable, and a human is always the one deciding what to do. They've been the foundation of home automation for fifteen years for exactly this reason. Nothing in this article argues that rules are wrong or should be replaced — they're the right tool for scheduling, thresholds, and simple automations that a human configured deliberately. A rule that turns on the porch light at sunset doesn't need a policy — it has no safety implications, no edge cases, and no AI acting on it. Rules fail not because they're a bad idea, but because they were never designed to compose with an autonomous agent that can fire arbitrary intents at arbitrary times.

But rules have a structural weakness: **they can only respond to situations that were anticipated when they were written.** And when an AI agent starts firing intents, the space of possible situations grows faster than anyone can write rules for.

---

## What breaks when AI enters the picture

An AI agent doesn't write rules. It fires intents.

When a vision model detects someone who has fallen, it doesn't execute `alarm.activate()` and `phone.call("911")` — it fires `ensure_safety [emergency]`. When a model infers that nobody is home, it doesn't iterate through a list of devices and turn them off one by one — it fires `save_energy [info]`.

The intent expresses a goal. The protocol figures out how to achieve it.

But here's the problem: once you have an AI agent firing intents, you need a way to constrain what it can and can't do — without writing a rule for every possible scenario. You need something that can evaluate intent against context at runtime, not something that was pre-written for a specific situation.

You need a policy engine.

---

## The difference that matters

A rule answers: *what should happen when X occurs?*

A policy answers: *is this action permitted, given who's asking, what they want to do, and when?*

The distinction is not cosmetic. It changes the entire structure of how you think about safety in a physical system.

Consider a front door lock. With rules:

```
# Rule 1: lock at midnight
if time == 00:00:
    lock.lock()

# Rule 2: unlock for delivery window
if time > 14:00 and time < 16:00 and delivery_expected:
    lock.unlock()
```

These rules handle the scenarios they were written for. But what happens when an AI agent fires `control_access [emergency]` at 03:00 because it detected an intruder? What happens when a different AI agent fires `away_mode` which includes an unlock action? What happens when two intents fire simultaneously and both want to act on the lock?

Rules don't compose. Each rule was written in isolation, without knowledge of the others. In a system where an AI can fire arbitrary intents at arbitrary times, the rule surface grows unbounded — and the gaps between rules are exactly where accidents happen.

A policy is different:

```python
NeverAfterHoursPolicy(
    actuator="unlock",
    device_ids=["lock-frontdoor-01"],
    blocked_hours=range(0, 6),      # never unlock 00:00–06:00
    except_urgency="emergency",     # unless it's an emergency
)
```

This policy applies to *every* intent that would unlock the front door, regardless of which AI fired it, regardless of what scenario triggered it. It's declared once. It evaluates at runtime. It composes with other policies automatically.

And it has an explicit emergency exception — because in a physical system, the rule "never unlock after midnight" has to be breakable in genuine emergencies. Rules can't express that nuance cleanly. Policies can.

---

## What a policy engine actually does

In DoSync, every intent passes through the PolicyEngine before execution. The engine evaluates the action plan against all configured policies and returns one of four verdicts:

- `ALLOW` — proceed as planned
- `BLOCK` — reject the intent entirely
- `CONFIRM` — hold execution and request human confirmation
- `MODIFY` — allow execution but remove or adjust specific actions

This happens between the resolver (which decides *what* to do) and the executor (which actually does it). The AI never bypasses this layer — not even for emergency intents, which bypass policy constraints at the urgency level, not by circumventing the engine entirely.

In DoSync, this looks like:

```python
# An AI fires save_energy at 02:00
intent = Intent(
    intent=IntentClass.SAVE_ENERGY,
    urgency=Urgency.INFO,
    context={"trigger": "occupancy_inferred_empty"},
)

# The resolver builds an action plan:
# → turn_off: wiz-living-01..10
# → turn_off: wiz-hallway-01       ← this one is excluded by policy
# → set_temperature: thermostat-01

# The PolicyEngine evaluates:
# DeviceExclusionPolicy: hallway light excluded from save_energy → MODIFY
# ContextualWeightingPolicy: 02:00 on a weekday → weight reduced but ALLOW

# The executor receives the modified plan:
# → turn_off: wiz-living-01..10
# → set_temperature: thermostat-01
```

The AI expressed a goal. The protocol decided how to achieve it. The policy engine ensured the result was safe — without the AI needing to know anything about the hallway light policy, or the time-based weighting, or which devices are excluded from which intents.

---

## Why this matters beyond the home

The rule/policy distinction becomes critical at scale and in regulated environments.

In a **hotel**, a rule that says "turn off lights when nobody is in the room" works until a guest leaves their laptop charging and the system decides the room is empty. A policy that says "never cut power to outlet circuits in occupied rooms regardless of occupancy inference" handles the edge case — without needing a rule for every possible device state.

In a **hospital**, a rule that says "lock all doors at 22:00" breaks the moment a crash cart needs to move between floors. A policy that says "door locks revert to unlocked state on emergency_capable override, regardless of schedule" is robust to the scenario that matters most — the one nobody wanted to think about at configuration time.

In a **factory**, a rule that says "shut down line B if temperature exceeds threshold" doesn't compose with the rule that says "never shut down line B during a shift change handover." A ConflictResolutionPolicy that assigns priorities to intents and resolves contention explicitly is the right abstraction — not an ever-growing set of conditional rules that interact in unpredictable ways.

The pattern is always the same: rules break at the boundaries between anticipated scenarios. Policies compose across all of them.

---

## The audit implication

There's one more difference that matters in regulated environments: **auditability**.

A rule that fires an action leaves a trace, but the reasoning is implicit — buried in the automation logic that evaluated the condition. If something goes wrong, you can see what happened, but reconstructing *why* requires reading the rule code.

A policy verdict is explicit. Every intent execution in DoSync's audit log records what happened, what the resolver decided, and what the executor did — in a tamper-evident SHA-256 chained record. The reasoning is in the system's configuration (which policies are active, with what parameters), not buried in conditional logic that has to be reverse-engineered after the fact.

```json
{
  "type": "intent_executed",
  "intent": "save_energy",
  "urgency": "info",
  "actions": 9,
  "failed": [],
  "success": true,
  "hash": "a3f7...",
  "prev_hash": "9c12..."
}
```

This matters for post-incident analysis. It matters for regulatory compliance. And it matters for the fundamental accountability question in any AI-augmented physical system: when something goes wrong, can you reconstruct exactly what the system decided and why?

With rules, the answer is usually "mostly." With policies, the answer is "yes, completely."

---

## The deeper point

Rules encode *what to do*. Policies encode *what's permitted*.

When a human is always in the loop, rules are enough — the human's judgment fills the gap between what the rules say and what should actually happen. When an AI agent is in the loop, that judgment gap has to be filled by the infrastructure.

That's what a policy engine is for. Not to constrain the AI arbitrarily, but to make the system's safety model explicit, configurable, and auditable — so that the AI can act autonomously within a boundary that a human defined deliberately.

The AI expresses goals. The protocol executes them. The policy engine ensures the result is safe.

That's the contract a physical AI system needs.

---

**DoSync Protocol:** https://github.com/giulianireg-spec/dosync-protocol
**License:** Apache 2.0

---

*Next in DoSync Concepts: Capability abstraction — how devices should declare what they can do, and why that changes everything about how AI coordinates physical systems.*
