# Vision, scope, and what this project will not become

*This file replaces `ROADMAP.md`, which said the current release was v0.3 three
months after v0.5.0 shipped. A roadmap ages on its own; the things below do not.
What happened is in [CHANGELOG.md](../CHANGELOG.md), where it stays accurate
without anyone remembering to update a second place.*

---

## The bigger picture — FamilyOS

DoSync is part of a larger project: **FamilyOS** — a private, local,
generational AI for the home.

> *"The best inheritance we can leave our children is knowledge. DoSync is the
> protocol that lets the home itself become part of that inheritance."*

The idea is an AI that lives in your home, knows your family across
generations, acts on the physical world through DoSync, and never sends your
data to anyone else's computer.

```
FamilyOS  (generational family AI — local, private)
    ↓
DoSync Protocol      ← semantic intent layer
    ↓
Physical devices     ← whatever the deployment has
```

That ambition explains choices that look disproportionate for a device
protocol. The audit chain is treated as infrastructure rather than logging
because a system meant to accompany a family for years is judged by whether its
record of what it did can be trusted. The policy engine sits where an AI cannot
route around it because the same reasoning applies to a factory floor and an
operating theatre. The capability registry, the emergency override, the
declared simulation — all of it is built for a system that has to be trusted
with something real, not merely with convenience.

**And the guarantee that matters to anyone considering building on this:
DoSync remains an independent open protocol regardless of what happens to
FamilyOS.** The protocol is the infrastructure. The domain is yours — the
reference deployment is a home, the drone milestone exists precisely to prove
the protocol does not care.

---

## Not on the roadmap

Being explicit about scope is more useful than a list of intentions:

- **Cloud connectivity.** DoSync is for local, private deployments. No cloud
  dependency is planned. A hub that needs someone else's server to decide
  whether your door opens is a different product with a different threat model.
- **Matter, Zigbee or Z-Wave at the radio level.** DoSync sits above the
  transport and abstracts it. It does not replace them; it uses them.
- **A mobile app.** The MCP server and the web dashboard cover the cases that
  exist. An app is a maintenance commitment this project cannot honestly make
  with one maintainer.
- **A catalogue of supported products.** Adapters ship as worked examples, not
  as an inventory anyone promises to keep current for your hardware. Discovery
  listens for what a device announces about itself; it does not consult a list
  of vendors. That decision is why one implementation covers every device that
  speaks mDNS or SSDP rather than one adapter per brand.
- **Learning from your deployment.** The resolver does not adapt to what you did
  last week. Behaviour that changes without a decision cannot be audited, and
  everything else here is built so that what happened can be accounted for.

---

## Questions without a settled answer

Design problems the project has not resolved. Several that used to be here have
since been answered — arbitration between simultaneous intents by the device
arbiter and `spec/CONSISTENCY-MODEL.md`, the plugin model by `dosync/plugins.py`,
and mid-execution restarts by the operation reconciler — which is the reason a
list like this belongs somewhere that gets re-read rather than in a roadmap that
does not.

- **Who writes a manifest for a device the hub discovered.** A scan reports an
  address and a service type; the protocol resolves over declared capabilities,
  and nobody has declared any. The operator can write one, the project could
  ship profiles per service type, or the connected AI could draft one for
  approval. The third is the only option consistent with the founding principle
  and the least explored.
- **What "verified" should mean.** An action can be sent and not happen. The hub
  now says when it did not reach a device at all; it cannot yet say whether a
  device that accepted a command actually did the thing.
- **Coordination between hubs in one building.** A shared registry, federated
  intents, or something else. `docs/MULTIHUB-PHASE-A-DESIGN.md` is a first pass,
  not a conclusion.
- **Certification by someone other than this project.** The suite is
  self-administered, which attests that the tests were run, not that an
  independent authority verified anything. That needs an institution rather than
  a feature.

---

## Contributing

The RFC process is open. Open a
[Discussion](https://github.com/giulianireg-spec/dosync-protocol/discussions),
label it `RFC`, and describe the problem, the proposed solution and the
trade-offs. Adapter implementations, resolver implementations and certification
tests are especially welcome — and a second independent implementation of the
protocol would be worth more to this project than any feature on any list.
