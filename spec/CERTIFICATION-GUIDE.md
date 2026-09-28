# DoSync Protocol — Third-Party Certification Guide

**Guide version:** 0.4 · **Protocol:** 0.5  
**Applies to:** Any implementation of the DoSync Protocol (any language, any platform)  
**Certification tool:** `dosync-certify` (installed with `pip install dosync`), or `dosync/certify.py` in [giulianireg-spec/dosync-protocol](https://github.com/giulianireg-spec/dosync-protocol)

---

## Overview

DoSync uses a self-certification model. Any developer or manufacturer can certify their implementation independently using the official certification CLI. No third-party review is required to claim certification — the signed JSON report is the certificate.

The suite checks what the protocol promises over the wire, against a running hub. It has four tiers, each cumulative: Standard includes every Basic check, Emergency every Standard check, Conformance every Emergency check.

| Tier | Checks | What it validates |
|---|---|---|
| **Basic** | 10 | Connectivity, authentication, device registration, manifest structure |
| **Standard** | 33 (+23) | Intent processing, events, direct actions, error codes, privacy, async polling, explain, version headers |
| **Emergency** | 44 (+11) | Emergency dispatch, the SHA-256 audit chain, firmware re-registration, heartbeat after an emergency |
| **Conformance** | 65 (+21) | The protocol's later guarantees: sensor kind, policy provenance in the audit chain, chain archiving, adapters and discovery (0.4, C01–C12); device locations and what a location in an intent does (0.5, C13–C21) |

A run that stops early is **incomplete**, not failed: the report says how many
of the tier's checks it reached, and it never certifies.

**Not applicable.** One check, C21, fires a real emergency. Against a hub that
is not in certify mode it would act on physical devices, so it runs only when
the hub reports `certify_mode`. Otherwise it is recorded as *not applicable*,
with its reason, and the tier's expected count drops by one: it is neither
passed — nobody checked it — nor failed. The signed report lists it.

**The suite builds its own scene.** Standard registers a test device; the 0.5
checks register probe devices (`certify-probe-…`) and probe intent classes
(`certify_location_…`), place them with `PATCH`, and remove all of it when they
end, whatever happens. The 0.5 checks verify through `explain`, which executes
nothing.

---

## Before You Start

Your hub must implement what each tier exercises. The endpoints below are the
ones the suite calls.

**Basic (B01–B10)**
- `GET /v1/status` — reachable; declares `protocol_version`; carries the required fields
- Bearer token authentication — an invalid token returns `401`
- `POST /v1/devices/register` — registers a device from a Capability Manifest (`spec/schemas/capability-manifest.schema.json`); a duplicate registration returns 200 or 409, never 500
- `GET /v1/devices` and `GET /v1/devices/{id}` — the registry and a device's detail; an unknown device returns `404`

**Standard (S01–S23)**
- `POST /v1/intent/async` — accepts a registered intent, including `emergency` urgency, and answers with `intent_id` and `status`; an unknown intent returns `422`, an invalid urgency `422`. (`POST /v1/intent` was removed; it answers `410`.)
- `GET /v1/intent/{id}` — polls the result (`spec/schemas/intent-result.schema.json`)
- `POST /v1/event` — accepts a device event; an unregistered device returns `404`
- `POST /v1/device/action` — a direct action is executed or refused by policy
- `GET /v1/audit` — every executed intent leaves an `intent_executed` entry that carries its `source`
- `GET /v1/health/devices` and `GET /v1/health/devices/{id}`
- `GET /v1/intents/{class}/explain` — the scoring breakdown, the same the resolver decides with
- `GET /v1/intent-classes` and `GET /v1/hub/heartbeat`; `/v1/status` also carries `api_version`
- `DELETE /v1/devices/{id}` — unregisters a device, which can then register again
- An actuator's `params_schema` is enforced as a JSON Schema
- `X-DoSync-Protocol-Version` and `X-DoSync-API-Version` headers on every response
- A device's detail never exposes `adapter_config`

**Emergency (E01–E11)** — requires at least one `emergency_capable` device whose actions succeed
- An emergency intent is accepted for immediate dispatch and recorded in the audit log
- The audit log is a SHA-256 chain that verifies, and `/v1/status` reports `audit_integrity`
- `intent_executed` entries carry their required fields, including `source`
- A firmware change on re-registration is handled; the heartbeat stays healthy after an emergency

**Conformance (C01–C21)**
- *0.4 (C01–C12):* every sensor declares a valid `kind`; `report_status` accepts an explicit `environment` scope; a plan a deployment policy modifies leaves a `policy_modified` chain entry with its provenance and a SHA-256 policy fingerprint; the live and the archived chain verify; `GET /v1/adapters` declares valid adapter kinds; `GET /v1/discovery/scan` registers nothing and reports what it searched; the inventory separates active from quarantined devices; `POST /v1/heartbeat/signed` is disabled unless enabled
- *0.5 (C13–C21, spec §10.5):* `PATCH /v1/devices/{id}` sets a location, keeps it, and records `device_relocated`; `POST` and `DELETE /v1/intent-classes/{name}` with `location_role`; `explain` takes `?location=` and `?urgency=`; a restricting location contains what is below it by whole segment; an `informs` class excludes nothing; a restricting emergency stays in its zone, force-inclusion included; an unknown restricting location is refused (`422`, counted as `unknown_location` in `/v1/status`), except in an emergency, which reaches every capable device and is recorded; a re-registration keeps the operator's location

C04–C06 need a plan that a deployment policy **modifies**, and a clean hub has
none. Load a policy file that removes a device from an intent the suite fires.
The reference hub's CI does it with `.github/certification/policies.json`, which
excludes a sensor it registers for the purpose from `report_status`.

---

## Running Certification

### Step 1 — Get the tool

```bash
pip install dosync          # provides the dosync-certify command
```

It uses only the Python standard library to talk to your hub, so it runs
against an implementation in any language.

### Step 2 — Start your hub

```bash
# Your own implementation, however it starts
DOSYNC_TOKEN=your-token node src/server.js

# The Python reference hub, in certify mode (simulated executor: no physical device acts)
DOSYNC_CERTIFY=true DOSYNC_DEMO_TOKEN=your-token dosync-hub --port 47200
```

### Step 3 — Run the suite

```bash
DOSYNC_TOKEN=your-token dosync-certify --host localhost --port 47200 --tier basic
DOSYNC_TOKEN=your-token dosync-certify --host localhost --port 47200 --tier standard
DOSYNC_TOKEN=your-token dosync-certify --host localhost --port 47200 --tier emergency
DOSYNC_TOKEN=your-token dosync-certify --host localhost --port 47200 --tier conformance \
  --output dosync-cert-conformance.json
```

The report is signed (Ed25519) unless you pass `--no-sign`; `--verify <report>`
checks the signature of an existing one.

### Step 4 — Interpret results

A passing run:

```
✓ CERTIFIED — DoSync STANDARD (33/33)
```

A failing check names what it expected and what it got:

```
✗  S07  Unknown intent rejected with 422 — status=200 (expected 422)
```

A run that stopped early is reported as `NOT RUN — … stopped after N of M checks`,
and a check that could not run safely as `not applicable in production mode`.

---

## Publishing Your Certification

There is no submission process. To claim certification publicly:

1. Save the JSON report with `--output dosync-cert-<tier>-<timestamp>.json`
2. Commit it to your repository (see [dosync-node/CONFORMANCE.md](https://github.com/giulianireg-spec/dosync-node/blob/main/CONFORMANCE.md) as a template)
3. Add a badge naming the tier, the checks passed and the protocol version:

```markdown
![DoSync Conformance 65/65 · protocol 0.5](https://img.shields.io/badge/DoSync-Conformance%2065%2F65%20·%20protocol%200.5-orange)
```

---

## Certification Tiers in Practice

**Basic** — minimum bar. Proves the hub speaks the protocol. Suitable for: proof-of-concept implementations, embedded devices with limited resources.

**Standard** — recommended minimum for any deployment. Proves intents work end-to-end, events are handled, errors are returned correctly, and privacy requirements are met.

**Emergency** — required for safety-critical deployments. Proves emergencies dispatch and that what happened is on a tamper-evident record.

**Conformance** — proves the implementation keeps the protocol's current guarantees: governance that can be audited, and intents that act only where they are meant to.

---

## Partial Failures

If some checks fail, the report marks the implementation as `"certified": false` but still lists which passed, so it shows exactly what remains.

Common failure patterns:

| Failure | Likely cause |
|---|---|
| B02 protocol version | `/v1/status` lacks `protocol_version` |
| B04 invalid token | The hub accepts any token, or runs with authentication off |
| S07 unknown intent 422 | The hub returns 200 or 400 instead of 422 for an unregistered intent |
| S13 version headers | `X-DoSync-Protocol-Version` or `X-DoSync-API-Version` missing |
| S17 manifest privacy | The device detail exposes `adapter_config` |
| E04 chain integrity | The audit chain is broken, or `GET /v1/audit` is not implemented |
| C04–C06 policy provenance | No deployment policy modifies a plan the suite fires (see above) |
| C15, C17 location | The resolver ranks by location instead of restricting (protocol 0.4 behavior) |

---

## TLS / HTTPS

If your hub runs over HTTPS, set the CA certificate path:

```bash
DOSYNC_TOKEN=your-token \
DOSYNC_CA_CERT=/path/to/ca.crt \
dosync-certify --host your-hub --port 47200 --tier standard
```

---

## Reference Implementations

| Language | Repository | Certification |
|---|---|---|
| Python | [giulianireg-spec/dosync-protocol](https://github.com/giulianireg-spec/dosync-protocol) | Conformance 65/65 — every tier certified against a live hub in CI, on every push |
| Node.js | [giulianireg-spec/dosync-node](https://github.com/giulianireg-spec/dosync-node) | Standard 33/33 against an earlier version of the suite; re-validation against the 65-check suite pending |

---

*DoSync Protocol 0.5 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
