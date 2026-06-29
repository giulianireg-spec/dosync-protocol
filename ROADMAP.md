# DoSync Protocol — Roadmap

> This roadmap reflects the current state of the project and planned evolution.  
> It is a living document — priorities may shift based on community feedback and real-world usage.  
> Contributions and discussion welcome in [GitHub Discussions](https://github.com/giulianireg-spec/dosync-protocol/discussions).

---

## Released

### v0.1 — Foundation
- 5-layer protocol architecture (Intent → Semantic → Registry → Security → Transport)
- `CapabilityMatchingResolver` — tag-based capability resolution
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

### v0.3 — Observability, Distribution, Robotics
- **Persistent state cache** — `StateAwareResolver` persists device state to SQLite, survives restarts.
- **Device Health Monitor** — per-device execution success rate with operator-facing alerts. The resolver does not modify scores autonomously — the operator decides. See [DESIGN-PRINCIPLES.md](DESIGN-PRINCIPLES.md).
- **Explainability endpoint** — `GET /v1/intents/{id}/explain` returns the resolver's reasoning.
- **MQTT transport adapter** — Layer 1 transport for devices that can't use HTTP.
- **External Resolver Protocol** — delegate resolution to an external HTTP service (`DOSYNC_RESOLVER_URL`).
- **Idempotency + delivery semantics** — opt-in idempotency keys make retries safe for physical actions.
- **Openly-registered intent classes** — 5 universal intents + domain-specific classes registered at runtime.
- **Multi-hub assisted failover (Phase A)** — operator-in-the-loop promotion with gateway-probe disambiguation; validated on real hardware. See [When Automatic Failover Is More Dangerous Than No Failover](https://dev.to/giulianiregspec/when-automatic-failover-is-more-dangerous-than-no-failover-2c9o).
- **Long-running operations** — hierarchical state machine + telemetry reconciliation for actions that take time ("silence is not success").
- **Robotics / drone milestone** — full AI→intent→mission loop flown in ArduPilot SITL from a single plain-language sentence, every step telemetry-confirmed; the supervisor aborts honestly when the AI guesses wrong. The proof that DoSync is domain-agnostic. See [the build log](https://dev.to/giulianiregspec/i-gave-an-ai-one-sentence-a-drone-flew-the-mission-and-when-the-ai-guessed-wrong-the-system-2h3m). *(SITL; physical flight pending.)*
- **dosync-node** — companion Node.js (device-side) implementation, same author, Standard-certified.
- **Certification CLI** — 33 Standard / 36 Emergency tests, signed reports (Ed25519).

---

## Planned

### Near-term
- **Emergency device-level preemption** — guarantee an emergency intent wins the last write on a shared device, even against an in-progress routine. Closes a documented consistency gap.
- **Multi-hub state replication (Phase B)** — replicate registry + audit log across hubs. Failover *safety* already shipped in Phase A; this adds state continuity.
- **Physical hardware** — drone flight on real hardware (pending airframe + regulation); BLE adapter validated against real hardware; physical lock demo.
- **Resolver recall** — per-intent coverage metrics + tagging guidance to raise recall on broad intents.

### v1.0 — Stable Interface + Independent Implementation
**Target:** 2027

- **A genuinely independent implementation** — by a *different author or organization*. `dosync-node` proves the spec is re-implementable in a second language, but a protocol becomes a standard only with independent implementors. This is the gating milestone for v1.0.
- **Stable API** — breaking changes require a major version bump from this point forward.
- **Third-party / hardware certification** — external review process for the signed certification report; formal DoSync certification tiers for device manufacturers.
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
