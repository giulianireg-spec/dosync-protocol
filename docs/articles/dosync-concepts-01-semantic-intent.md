# What is Semantic Intent? And Why It's Not the Same as a Command

*DoSync Concepts — Part 1 of 5*

---

There's a word that keeps appearing in conversations about AI and the physical world: *intent*.

We say things like "the AI understood my intent" or "I want the system to infer my intent from context." But when you actually sit down to build something — a smart home, a hotel, a factory floor — you quickly discover that most protocols don't have a concept of intent at all.

They have commands.

And that difference, small as it sounds, is architectural. It changes everything about how you build systems that are supposed to work with AI.

---

## The command model

Every smart home protocol in existence today was designed around one assumption: a human decides what to do, an app translates that decision into a specific instruction, and a device executes it.

```
Human → App → Command → Device
lock.unlock()
light.set_brightness(80)
thermostat.set_temperature(21)
```

This is the command model. It works beautifully when the human is in the loop — when someone taps a button, sets a schedule, or writes a rule. The intent is implicit in the human's action. The app just needs to translate it correctly.

The problem starts when you remove the human.

---

## What an AI agent actually produces

When an AI system observes the world — a camera detecting a fall, a sensor registering unusual temperature, a model inferring that nobody is home — it doesn't produce a command. It produces an understanding of a situation.

"There is an emergency."  
"The person who lives here has arrived."  
"Something is wrong with the refrigerator."

These are not commands. They're semantic descriptions of a state of the world. They describe *what is happening* or *what needs to happen*, not *how to make it happen*.

To translate this into device commands, someone has to write that translation — manually, in advance, for every possible situation, for every device combination. Miss one edge case and the system does nothing. Add a new device and you rewrite the rules.

This is the command gap. And it only becomes visible when you try to add AI to a physical system.

---

## Semantic intent: a different contract

A semantic intent is a structured expression of a goal — what needs to be achieved — without specifying how to achieve it.

Compare:

```json
// Command (existing protocols)
{
  "device": "lock-frontdoor-01",
  "command": "unlock",
  "duration": 300
}

// Semantic Intent (DoSync)
{
  "intent": "ensure_safety",
  "urgency": "emergency",
  "context": {
    "trigger": "fall_detected",
    "location": "bedroom"
  }
}
```

The command says: *unlock this specific lock for 5 minutes.*  
The intent says: *there is a safety emergency in the bedroom.*

The difference isn't just syntactic. It's about who holds the knowledge.

In the command model, the knowledge of *which devices to activate and how* lives in the app, or in the rules engine, or in the code a developer wrote last year. That knowledge is static. It can't adapt to new devices, new scenarios, or new contexts.

In the intent model, that knowledge lives in the devices themselves — in their **Capability Manifests**. Each device declares what it can do and when it's appropriate to act. The system assembles the response at runtime, based on what's available.

---

## How DoSync implements this

In DoSync, every device publishes a Capability Manifest when it joins the network:

```json
{
  "device_id": "lock-frontdoor-01",
  "tags": ["door-lock", "entrance", "emergency"],
  "actuators": [
    { "type": "unlock", "description": "Unlock for emergency access" },
    { "type": "lock" }
  ],
  "emergency_capable": true
}
```

This manifest is not a command interface. It's a declaration of capability and context. The device is saying: *I can unlock. I am relevant in emergencies. I am at the entrance.*

When an AI agent fires an intent:

```json
{
  "intent": "ensure_safety",
  "urgency": "emergency"
}
```

The DoSync semantic resolver reads every registered manifest and asks: *which devices are relevant to this intent, given their declared capabilities and tags?* It builds an action plan automatically — no rules written, no pre-configuration required.

```
ensure_safety [emergency]
  → lock-frontdoor-01    unlock  (emergency_capable, entrance tag)
  → alarm-main-01        alarm   (emergency_capable)
  → phone-family-01      call    (communication tag)
  → wiz-living-01..10    turn_on (light tag, full brightness)
All in parallel. Under 100ms. No internet. No cloud.
```

Add a new device tomorrow — a smart siren, a second lock, a notification relay — and it participates automatically in every relevant scenario. No rule rewriting. No code changes.

---

## Why this matters beyond the home

The command gap isn't unique to smart homes. It's structural — it appears anywhere an AI system needs to coordinate physical devices in response to real-world events.

A factory line detects an anomaly on a critical component. The AI knows something is wrong. With commands, someone pre-wrote the response. With intent, the devices coordinate the *response around* a shutdown — notifying the supervisor, logging a maintenance request, adjusting lighting and access — while the line's own safety-rated shutdown system does the actual stop. The audit log captures every action with a tamper-evident chain.

The protocol is domain-agnostic. The insight is not.

---

## The deeper shift

Here's what semantic intent actually changes, at a philosophical level:

In the command model, the AI is a **remote control**. It knows the exact sequence of buttons to press. It breaks when a device changes, when a new device appears, or when a scenario wasn't anticipated.

In the intent model, the AI is a **coordinator**. It expresses what needs to happen. The devices figure out their role. The system is resilient to change because the knowledge is distributed — each device owns its own context.

This is not a small difference. It's the difference between building a system that works today and building a system that can grow.

---

## What comes next

This is the first post in a series exploring the concepts behind DoSync — an open protocol (Apache 2.0) for semantic intent-based communication between AI and physical devices.

In the next post: **Why hardcoded automations fail AI agents** — and what a system designed from first principles for AI actually looks like.

**GitHub:** https://github.com/giulianireg-spec/dosync-protocol  
**License:** Apache 2.0

---

*DoSync Concepts is a series exploring the ideas behind the DoSync Protocol — the semantic layer between AI agents and physical systems.*
