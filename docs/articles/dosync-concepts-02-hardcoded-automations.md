# Why Hardcoded Automations Fail AI Agents

*DoSync Concepts — Part 2 of 5*

---

There's a rule every smart home developer has written at least once:

```
if motion_detected and time > 22:00:
    turn_on(hallway_light)
```

It works. It's simple. And it's the foundation of how every major home automation platform thinks about intelligence today.

The problem isn't that the rule is wrong. The problem is what happens when you add an AI agent to the system — and why rules, no matter how many you write, are the wrong abstraction for that job.

---

## The rule-writing problem

Rules are commands in disguise. "If X happens, do Y" is just a delayed command — the human still decided what Y should be, they just decided it in advance.

That works perfectly when you can anticipate every scenario, your device set never changes, and the AI's job is simply to trigger pre-approved responses. None of those assumptions hold in a real AI-augmented environment.

Consider what happens when you add a new device. A smart lock arrives. You register it with your platform. Now you have to go back through every relevant rule and add the lock. Miss one and the system silently does nothing in exactly the scenario where you needed it most.

Or consider an AI that detects unusual temperature patterns in the kitchen — a sensor reading that might mean the fridge compressor is failing. The AI knows something is wrong. But there's no rule for "fridge compressor anomaly," because nobody wrote one. The system does nothing.

This is the brittleness problem: hardcoded automations only respond to situations that were anticipated when the rule was written.

---

## Why AI agents make this worse, not better

The intuitive solution is to write more rules, or smarter rules. Use the AI to generate them. Inspect logs and auto-suggest new automations.

But this misses the architectural problem.

An AI agent doesn't produce rules. It produces understanding. When a vision model detects a fall, it doesn't know it needs to call `phone.call("911")` and `lock.unlock()` and `alarm.activate("emergency")`. It knows there's an emergency. The translation from understanding to commands still has to be written somewhere — by someone — in advance.

The more capable the AI, the more situations it can detect. And every new situation the AI can detect is a new set of rules that needs to be written to respond to it. The rule surface grows faster than anyone can maintain it.

---

## What capability-based discovery changes

The alternative is to shift where the knowledge lives.

Instead of centralizing the response logic in rules, distribute it to the devices themselves. Each device declares what it can do and in what contexts it's appropriate to act:

```json
{
  "device_id": "lock-frontdoor-01",
  "tags": ["door-lock", "entrance", "emergency"],
  "actuators": [
    { "type": "unlock", "emergency_capable": true }
  ],
  "emergency_capable": true
}
```

This manifest is not a rule. It's a declaration. The device is saying: *I can unlock. I'm relevant in emergencies. I'm at the entrance.*

When the AI fires a semantic intent — `ensure_safety / emergency` — the resolver reads every registered manifest and asks: which devices declared themselves relevant to this situation? It builds the response at runtime, from what's actually available.

```
ensure_safety [emergency]
  → lock-frontdoor-01    unlock   (emergency_capable, entrance tag)
  → alarm-main-01        activate (emergency_capable)
  → wiz-living-01..10    turn_on  (light tag, full brightness)
  → notifier-sms-01      notify   (communication tag)
All in parallel. No rules written for this scenario.
```

Add the smart lock tomorrow — it participates automatically in every relevant scenario. No rule rewriting. No code changes. The device brought its own knowledge.

---

## The difference that matters

Hardcoded automations are fragile because they encode knowledge in a central place that can't keep up with change. Every new device, every new scenario, every new AI capability requires human intervention to update the rules.

Capability-based discovery distributes that knowledge to the edges. Devices own their context. The system is resilient to change because adding new capabilities is additive, not a rewrite.

For AI agents specifically, this is the difference between a system that only responds to situations you anticipated and a system that responds to situations the AI can detect — whether you anticipated them or not.

---

## What comes next

This is the second post in a series exploring the concepts behind DoSync — an open protocol (Apache 2.0) for semantic intent-based communication between AI and physical devices.

In the next post: **Event vs Intent — the architectural difference that matters** — why the distinction between what happened and what needs to happen changes everything about how you design the system.

**GitHub:** https://github.com/giulianireg-spec/dosync-protocol
**License:** Apache 2.0

---

*DoSync Concepts is a series exploring the ideas behind the DoSync Protocol — the semantic layer between AI agents and physical systems.*
