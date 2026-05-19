# DoSync Protocol — Roadmap

> This roadmap reflects the current state of the project and planned evolution.  
> It is a living document — priorities may shift based on community feedback and real-world usage.  
> Contributions and discussion welcome in [GitHub Discussions](https://github.com/giulianireg-spec/dosync-protocol/discussions).

---

## Released

### v0.1 — Foundation
- 5-layer protocol architecture (Intent → Semantic → Registry → Security → Transport)
- `CapabilityMatchingResolver` — tag-based semantic resolution
- `CapabilityRegistry` — device self-registration with manifests
- REST API (12+ endpoints) + WebSocket real-time events
- Web dashboard
- API key authentication + SHA-256 tamper-evident audit log
- Philips WiZ adapter (UDP local)
- Home Assistant bridge (10 domains, 3000+ devices)
- Native MCP server (Claude, ChatGPT, any LLM)
- GPIO adapter — Raspberry Pi 5 (PIR + DHT22)
- SMS notifications via Twilio
- UDP device discovery
- SQLite persistence (survives restarts)
- Docker Compose — reproducible demo with 8 simulated devices
- Certification CLI — 16/16 tests, 3 tiers (Basic / Standard / Emergency)

### v0.2 — Resolver Interface + Security
- `BaseResolver` — formal interface, third-party implementations can be dropped in
- `StateAwareResolver` — eliminates 35% of redundant actions via state cache
- `PolicyEngine` — 6 policies: NeverAfterHours, RequireConfirmation, DeviceExclusion, ConflictResolution, ContextualWeighting, BlockIntent
- Conflict resolution with priority map (Emergency → Security → Presence → Comfort → Efficiency)
- Local PKI — CA, hub cert, adapter certs (TLS/mTLS, Layer 2 of the spec)
- Resolver benchmark — p99 < 0.11ms @ 38 real devices, scalability to 5000+
- `dosync.security` CLI — `setup`, `issue`, `verify`, `renew`, `info`
- GitHub Discussions open
- `RESOLVER-SPEC-v0.2.md` — formal resolver interface specification

---

## Planned

### v0.3 — Learned Patterns + Persistent State
**Target:** Q3 2026

- **Learned patterns in the resolver** — weight updates based on execution history (frequency, success rate, recency). The resolver becomes progressively smarter without requiring an LLM.
- **Persistent state cache** — `StateAwareResolver` persists device state to SQLite. Survives hub restarts. Configurable TTL per device type.
- **Tag-based indexing** — pre-group devices by tag to bring large registries (5000+ devices) to near-O(1) resolution. Current O(n) algorithm handles production scale well; indexing unlocks industrial deployments.
- **Explainability endpoint** — `GET /v1/intents/{id}/explain` returns the resolver's reasoning: which devices scored, why, and what was filtered by policies.

### v0.4 — Multi-Agent + Physical Hardware
**Target:** Q4 2026

- **Physical lock demo** — relay + GPIO for `control_access` intent. Video demo.
- **BLE transport adapter** — Bluetooth LE device support.
- **Multi-agent conflict resolution** — formal model for simultaneous intents from multiple agents of equal priority against shared physical state. Current last-write-wins approach is insufficient for multi-agent environments.
- **Shelly adapter** — HTTP local control for Shelly relays and switches.
- **zigbee2mqtt adapter** — Zigbee device support via zigbee2mqtt bridge.

### v1.0 — Stable Interface + Independent Implementation
**Target:** 2027

- **Second independent implementation** — minimal hub in Node.js or Go implementing the resolver interface, 3+ endpoints, and the certification suite. A protocol needs multiple independent implementations to become a standard.
- **Stable API** — breaking changes require a major version bump from this point forward.
- **Third-party certification** — external review process for the CLI-generated certification report.
- **Hardware certification program** — formal DoSync certification tiers for device manufacturers.
- **LLM-backed resolver** — local LLM (Llama, Mistral) as a drop-in resolver. The interface is already designed for this path.

---

## Open Questions

These are design problems without a clear answer yet. Discussion welcome.

- **Multi-agent state** — when two agents of equal priority send contradictory intents simultaneously, how should the system arbitrate? Optimistic locking? Intent queuing? Explicit agent priority?
- **Resolver plugins** — should third-party resolvers be distributable as Python packages (`pip install dosync-resolver-llama`)? What's the right plugin model?
- **Cross-hub coordination** — how should two DoSync hubs in the same building coordinate? Shared registry? Federated intents?
- **Offline-first guarantees** — what happens when the hub restarts mid-execution of an emergency intent? How do we ensure safety-critical actions complete?

---

## The Bigger Picture — FamilyOS

DoSync is part of a larger project: **FamilyOS** — a private, local, generational AI for the home.

> *"The best inheritance we can leave our children is knowledge. DoSync is the protocol that lets the home itself become part of that inheritance."*

The vision: an AI that lives in your home, knows your family across generations, acts on the physical world through DoSync, and never sends your data to the cloud. The product name is still being defined — but the direction is clear.

```
FamilyOS  (generational family AI — local, private)
    ↓
DoSync Protocol      ← semantic intent layer
    ↓
Physical devices     ← lights, locks, sensors, alarms
```

DoSync is designed from the ground up as the physical execution layer for this kind of AI. The capability registry, the policy engine, the emergency override, the tamper-evident audit log — all of it is infrastructure for a system that needs to be trusted with real family safety, not just convenience.

DoSync will remain an independent open protocol regardless of how FamilyOS evolves. The protocol is the infrastructure. The domain is up to you.

---

## Not on the Roadmap

To be explicit about scope:

- **Cloud connectivity** — DoSync is designed for local, private deployments. No cloud dependency is planned.
- **Matter at the radio level** — DoSync sits above the transport layer and abstracts it. It does not replace Matter, Zigbee, or Z-Wave at the radio level.
- **Mobile app** — the MCP server + web dashboard covers the primary use cases. A dedicated mobile app is not planned.

---

## Contributing

The RFC process is open. To propose a change:

1. Open a Discussion in the [Discussions tab](https://github.com/giulianireg-spec/dosync-protocol/discussions)
2. Label it `RFC`
3. Describe the problem, proposed solution, and tradeoffs

Adapter implementations, resolver implementations, and certification test contributions are especially welcome.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
