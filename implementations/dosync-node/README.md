# dosync-node

> Node.js implementation of the DoSync Protocol

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.1-green.svg)](https://github.com/giulianireg-spec/dosync-protocol/blob/main/spec/DoSync-SPEC-v0.1.md)
[![Certification](https://img.shields.io/badge/certification-Standard%2032%2F32-orange.svg)](CONFORMANCE.md)
[![Node](https://img.shields.io/badge/node-%3E%3D18-brightgreen.svg)](https://nodejs.org)

---

## What is this?

dosync-node is an independent implementation of the [DoSync Protocol](https://github.com/giulianireg-spec/dosync-protocol) in Node.js.

DoSync is an open protocol (Apache 2.0) that lets AI systems interact with physical IoT devices using semantic intent — expressing *what they want to achieve*, not *how to achieve it*.

This implementation was built from the protocol specification (`DOSYNC-SPEC-v0.1.md`), independently of the Python reference hub. Its purpose is to demonstrate that the spec is clear enough for third-party implementations.

**Python reference hub:** [giulianireg-spec/dosync-protocol](https://github.com/giulianireg-spec/dosync-protocol) (port 47200)  
**This Node.js hub:** port 47201

---

## Certification

dosync-node v0.3.0 passes the **DoSync Standard certification tier (32/32 tests)**:

```bash
# Run against this hub
DOSYNC_TOKEN=<your-token> node src/server.js &
DOSYNC_TOKEN=<your-token> python3 certify.py --host localhost --port 47201 --tier standard
# → ✓ CERTIFIED — DoSync STANDARD (32/32)
```

The `certify.py` tool comes from the protocol repository. Full certification report: [CONFORMANCE.md](CONFORMANCE.md).

| Tier | Tests | Status |
|---|---|---|
| Basic | 10 | ✅ Passing |
| Standard | 32 | ✅ Passing |
| Emergency | 35 | 🔄 Planned |

---

## Quick start

```bash
git clone https://github.com/giulianireg-spec/dosync-node
cd dosync-node
npm install

# Start with authentication
DOSYNC_TOKEN=my-secret-token node src/server.js
# Hub running on http://0.0.0.0:47201

# Start without authentication (development)
node src/server.js
```

---

## What it implements

- REST API (all endpoints from `DOSYNC-SPEC-v0.1.md`)
- Capability-based resolver (same tag-matching algorithm as the Python hub)
- SHA-256 tamper-evident audit log
- In-memory device registry
- Intent async execution with polling
- Device health monitoring
- Version headers (`X-DoSync-Protocol-Version`, `X-DoSync-API-Version`)

Built with [Fastify](https://fastify.dev). No shared code with the Python reference hub.

---

## Interoperability

dosync-node has been tested for interoperability against the DoSync Python reference hub:

```
Register device → receive ensure_safety [emergency]  ✅
Execute turn_on actuator                              ✅
Push motion event to hub                              ✅
Handle unknown intent class gracefully                ✅
Return version headers on all responses               ✅
Exclude adapter_config from public API                ✅
```

---

## Resolver algorithm

dosync-node uses the same scoring algorithm as the Python `CapabilityMatchingResolver`:

| Factor | Points |
|---|---|
| Tag overlap (per matching tag) | 10 pts |
| Emergency bonus (emergency_capable on emergency intents) | 30 pts |
| Location bonus (context.location matches device tag) | 15 pts |

---

## Protocol

Full protocol specification: [DoSync Protocol](https://github.com/giulianireg-spec/dosync-protocol)

- `spec/DoSync-SPEC-v0.1.md` — full protocol specification
- `spec/RESOLVER-SPEC-v0.3.md` — resolver interface

---

## License

Apache 2.0 — free to implement, free to extend, no royalties.

---

*dosync-node v0.3.0 · © 2026 Rodrigo Giuliani · giulianireg@gmail.com*
