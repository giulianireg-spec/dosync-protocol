# DoSync Protocol — Compatibility Commitments

This document defines what DoSync commits to preserving across versions, and what may change. It is intended for manufacturers certifying devices, developers building integrations, and third parties implementing the protocol.

---

## What is guaranteed stable

The following are stable from the current version forward. Breaking changes to any of these require a MAJOR protocol version bump and a minimum 6-month deprecation window.

### Wire format

- The JSON structure of `Intent`, `CapabilityManifest`, `ActionPlan`, `DeviceAction`, and `IntentResult` as defined in `spec/schemas/`
- All **required fields** in those schemas — they will not be removed or have their type changed
- The five universal intent classes: `ensure_safety`, `alert_anomaly`, `control_access`, `report_status`, `notify`
- The four urgency levels and their priority order: `emergency > alert > warning > info`
- The guarantee that `emergency` urgency bypasses all policy constraints

### API surface

- All endpoints under `/v1/` that are currently documented
- The authentication model (Bearer token)
- The `X-DoSync-Protocol-Version` and `X-DoSync-API-Version` response headers
- The `POST /v1/devices/register` contract: a valid manifest is always accepted
- The `GET /v1/hub/heartbeat` response structure

### Certification

- The three certification tiers (Basic, Standard, Emergency) and their requirements
- The `certify.py` CLI interface: `--host`, `--port`, `--tier` flags
- The `dosync-cert.json` output format

### Audit log

- The SHA-256 tamper-evident chaining algorithm
- The `type` field in audit entries — existing types will not be removed or renamed
- The `integrity` field in `GET /v1/audit` responses

---

## What may change without a major version bump

The following may change in MINOR protocol versions. Implementations SHOULD handle unknown fields gracefully (ignore, do not reject).

- **New optional fields** in request and response bodies
- **New optional endpoints** — new paths under `/v1/`
- **New intent classes** in the universal set (there are currently five)
- **New urgency levels** — if added, they will not change the semantics of existing levels
- **New optional audit entry types** — existing entries are stable; new types may be added
- **New optional fields in `CapabilityManifest`** — device implementations should serialize only what they support
- **Default values** for optional fields — may change with documented notice

---

## What is explicitly experimental

The following are NOT covered by compatibility commitments in v0.x. They may change significantly before v1.0.

- The `DOSYNC_UNREACHABLE_TTL` behavior — the mechanism may evolve
- The scoring algorithm in `CapabilityMatchingResolver` — weights and thresholds are not part of the protocol specification
- The `PhasedActionPlan` structure — under active development
- Multi-hub coordination (§11) — specified as requirements, implementation guidance is subject to change
- The MCP server interface — MCP itself is evolving; the DoSync MCP server tracks it
- The `OccupancyEngine` inference model — heuristic, not normative

---

## How to build reliably against DoSync

**For device manufacturers:**

1. Implement the `CapabilityManifest` schema (`spec/schemas/capability-manifest.schema.json`). All required fields are stable.
2. Register via `POST /v1/devices/register`. This endpoint contract will not change.
3. Run `certify.py --tier standard` against your implementation before shipping.
4. Ignore unknown fields in responses — forward compatibility.
5. Do not depend on the exact scoring algorithm. Tag configuration affects resolution; consult `docs/DEPLOYMENT-TAGS-GUIDE.md`.

**For integration developers:**

1. Use the `Authorization: Bearer <token>` header on all authenticated endpoints.
2. Read `X-DoSync-Protocol-Version` from responses. If the major version changes, review the changelog.
3. Fire intents via `POST /v1/intent/async` and poll `GET /v1/intent/{id}` for results. The async model will not change.
4. Handle `IntentResult.status` values: `success`, `partial`, `partial_abort`, `failed`, `retry_exhausted`. New status values may be added — treat unknown values as `partial`.

**For hub implementors:**

1. The formal wire format in `spec/schemas/` is normative. Your implementation validates against it.
2. Pass `certify.py --tier emergency` to claim Emergency certification.
3. The five universal intent classes must be supported out of the box. Domain-specific intent classes are registered by the operator.
4. Implement the audit log with SHA-256 chaining as specified in §8.3. The chain algorithm is stable.

---

## Version transition plan

| Version | Status | Compatibility commitment |
|---|---|---|
| `v0.1` (current) | Active | Stable wire format; experimental hub internals |
| `v0.2` (planned) | Future | Additive — no breaking changes to v0.1 clients |
| `v0.3` (planned) | Future | Additive — no breaking changes |
| `v1.0` (target: 2027) | Future | Full stability — breaking changes require major bump |

From `v1.0`, the protocol follows strict semantic versioning. A `v1.x` → `v2.0` transition will include a minimum 12-month deprecation window and a documented migration guide.

---

## Reporting compatibility issues

If you encounter behavior that contradicts these commitments, open an issue at `github.com/giulianireg-spec/dosync-protocol` with the label `compatibility`.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
