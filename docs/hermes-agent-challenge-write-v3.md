---
title: Why AI Agents Like Hermes Need a Semantic Execution Layer for the Physical World
published: false
tags: hermesagentchallenge, devchallenge, agents
---

*This is a submission for the [Hermes Agent Challenge](https://dev.to/challenges/hermes-agent-2026-05-15): Write About Hermes Agent*

---

Most AI agents interact with the world through APIs, databases, and web services. The feedback loop is fast and forgiving — if something goes wrong, you retry.

Physical environments are different. A light that turns on twice isn't a problem. A door that unlocks at the wrong time is. An agent operating in the physical world needs not just reasoning capability, but a clear contract between what it decides and what actually executes.

This post is about that contract — and why Hermes Agent's architecture makes it a natural fit for physical systems.

---

## What makes Hermes Agent relevant here

Hermes Agent has three characteristics that matter specifically for physical environments:

**Native MCP support.** Hermes connects to external systems via Model Context Protocol natively. Any hub or device layer that exposes an MCP server becomes immediately accessible — no custom integrations, no bridges.

**Multi-step tool use with context.** Hermes doesn't just call tools — it reasons about which tool to call, in what order, based on context. For physical systems where the right action depends on time, location, and state, this matters.

**Open weights and local execution.** Physical environments often have strict privacy requirements. A system that can run fully local — without sending home sensor data to a cloud provider — is architecturally different from one that can't.

These three properties together describe an agent that can act on physical systems in a way that's both capable and auditable.

---

## The gap it needs to cross

Every physical device protocol was designed around one assumption: a human decides what to do, and a device executes it. The protocols speak in commands — `lock.unlock()`, `light.set_brightness(100)`.

An AI agent doesn't produce commands. It produces understanding. *"The kids just arrived home"* is not a command. The translation from that understanding to the right set of device actions — for the specific devices available, in the right context — still has to be written by someone, in advance.

This is where most physical AI integrations break down. The agent reasons correctly and the devices exist, but the translation layer between them is a pile of hardcoded rules that grows more fragile with every new device.

A semantic execution layer inverts this. Instead of the agent knowing *how* to act, devices declare *what they can do* and *when they're relevant*. The agent expresses goals. The infrastructure handles translation at runtime.

---

## An experiment: Hermes + DoSync

I tested this with [DoSync Protocol](https://github.com/giulianireg-spec/dosync-protocol), an open-source hub (Apache 2.0) that implements this semantic layer. Devices register with a capability manifest. The hub exposes a native MCP server.

Connecting Hermes to DoSync required three lines in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  dosync:
    command: python3
    args: [/path/to/dosync/mcp_server.py]
    env:
      DOSYNC_HUB_URL: http://localhost:47200
      DOSYNC_TOKEN: <token>
```

I gave Hermes a single prompt:

```
It's 18:45 on a Monday. The PIR sensor at the entrance just detected motion.
What does the system do?
```

Hermes queried the hub state, reasoned about the time and day, identified the appropriate semantic intent (`children_arrived_home`), and fired it once. Five physical lights turned on. An SMS was sent to the family. The audit log updated with two new SHA-256 chained entries.

What Hermes didn't need to know: which specific bulbs to address, the SMS provider, or the schedule policy restricting this intent to weekday evenings. That knowledge lived in the hub. Hermes expressed a goal — the infrastructure handled execution.

---

## The open question

This experiment raises a boundary question that physical AI deployments will have to answer: **where does agent reasoning end and infrastructure policy begin?**

Should Hermes decide which intent to fire, or should context mapping be pre-configured? When an agent acts in a physical environment with real consequences, how much autonomy is appropriate before a human needs to confirm?

Hermes Agent's transparency — open weights, observable tool calls, local execution — makes it the right kind of system to explore these questions. You can see exactly what it reasoned and why. In physical environments, that auditability isn't a nice-to-have. It's a requirement.

---

**DoSync Protocol (open source):** https://github.com/giulianireg-spec/dosync-protocol  
**Hermes Agent:** https://hermes-agent.nousresearch.com
