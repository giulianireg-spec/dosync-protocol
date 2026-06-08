# DoSync Protocol — Node.js Reference Implementation

A second, independent implementation of the DoSync Protocol in Node.js.

This implementation was built from the protocol specification, independently of the Python reference implementation. Its purpose is to demonstrate that the spec is clear enough to be implemented by third parties.

**Python hub:** port 47200  
**Node.js hub:** port 47201

## Certification status

| Tier | Status |
|---|---|
| Basic | ✅ Passes all 6 tests |
| Standard | ✅ 32/32 passes |
| Emergency | 🔄 Planned |

## Quick start

```bash
cd implementations/dosync-node
npm install
npm start
# Hub running on http://localhost:47201
```

## Run certification

```bash
# From repo root
python3 certify.py --host localhost --port 47201 --tier basic
```

## Architecture

This implementation uses [Fastify](https://fastify.dev) for the HTTP layer.  
The registry, resolver, and audit log are in-memory — sufficient for Basic tier certification.

The resolver uses the same tag-matching algorithm as the Python `CapabilityMatchingResolver`:
- Tag overlap: 10 points per matching tag
- Emergency bonus: 30 points for emergency-capable devices on emergency intents
- Location bonus: 15 points when context location matches a device tag

## What this proves

A conforming DoSync implementation requires:
1. A capability registry (device manifests)
2. A semantic resolver (intent → device actions via tag matching)
3. A tamper-evident audit log (SHA-256 chained entries)
4. The standard REST endpoints defined in `DOSYNC-SPEC-v0.1.md`

No shared code with the Python implementation. Same protocol, different language.

---

*DoSync Protocol v0.1 · Apache 2.0*
