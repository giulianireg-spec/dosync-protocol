# DoSync Protocol — Third-Party Certification Guide

**Guide version:** 0.3 · **Protocol:** v0.1  
**Applies to:** Any implementation of the DoSync Protocol (any language, any platform)  
**Certification tool:** `certify.py` from [giulianireg-spec/dosync-protocol](https://github.com/giulianireg-spec/dosync-protocol)

---

## Overview

DoSync uses a self-certification model. Any developer or manufacturer can certify their implementation independently using the official certification CLI. No third-party review is required to claim certification — the signed JSON report is the certificate.

The certification suite has four tiers. Each tier is cumulative: Standard includes all Basic tests, Emergency includes all Standard tests, Conformance includes all Emergency tests.

| Tier | Tests | What it validates |
|---|---|---|
| **Basic** | 10 | Connectivity, authentication, device registration, manifest structure |
| **Standard** | 33 | All Basic + intent processing, event handling, error codes, privacy, async polling |
| **Emergency** | 44 | All Standard + emergency override, audit log integrity, firmware re-registration |
| **Conformance** | 52 | All Emergency + v0.4 protocol features: sensor-kind declarations, policy-modification provenance in the audit chain, policy fingerprinting, and archive-preserving chain integrity |

**Conformance tier note.** The C-series tests verify the guarantees added in the 0.4 cycle over the wire against a running hub. Several are strongest when the hub runs a deployment policy that *modifies* plans (C04–C06 look for `policy_modified` chain entries with full provenance); run the tier against a hub with a real `DOSYNC_POLICIES` file, and exercise at least one intent that a policy modifies, for a complete result. C08 passes whether or not the chain has been archived — it verifies that segmentation preserves the tamper-evident guarantee when archiving *is* in use.

---

## Before You Start

Your hub must implement the following before running certification:

**For Basic (10 tests):**
- `GET /v1/status` — returns `protocol_version`, `api_version`, hub metadata
- `GET /v1/hub/heartbeat` — returns `status: "healthy"`, `role`, `hub_id`
- `POST /v1/devices` — registers a device with a Capability Manifest
- `GET /v1/devices` — lists all registered devices
- `GET /v1/devices/:id` — returns device detail
- `DELETE /v1/devices/:id` — removes a device from the registry
- Bearer token authentication on all endpoints — invalid token returns 401
- Duplicate registration returns 200 or 409, never 500
- Non-existent device returns 404

**For Standard (adds 22 tests):**
- `POST /v1/intent` — accepts valid intents (all urgency levels), returns intent_id; unknown intent returns 422
- `GET /v1/intent/:id` — async polling; result includes `source` field
- `GET /v1/audit` — audit log accessible; each executed intent generates an audit entry
- `POST /v1/events` — accepts sensor events; unregistered device returns 404
- `GET /v1/health/devices` — device health summary
- `GET /v1/health/devices/:id` — per-device health stats
- `GET /v1/intent/explain` — scoring breakdown for an intent
- `POST /v1/devices/:id/action` — direct device action
- `GET /v1/devices/:id` — must NOT expose `adapter_config` in the response body (S17)
- `GET /v1/intent-classes` — returns list of supported intent classes
- `X-DoSync-Protocol-Version` and `X-DoSync-API-Version` response headers on all endpoints
- Invalid urgency value returns 422

**For Emergency (adds 3 tests):**
- Emergency urgency (`ensure_safety [emergency]`) activates all `emergency_capable` devices
- Audit log entries SHA-256 chained — `GET /v1/audit` integrity check passes
- Firmware re-registration detection — hub handles version change gracefully

> **Note:** Emergency tests require at least one `emergency_capable` device registered and returning success on actions. The Python reference hub supports a simulated executor for this purpose:
> ```bash
> PYTHONPATH=. DOSYNC_TOKEN=your-token DOSYNC_CERTIFY=true uvicorn server:app --host 0.0.0.0 --port 47200
> ```
> Third-party implementations must ensure their executor returns success for `emergency_capable` devices during Emergency tests.

---

## Running Certification

### Step 1 — Clone the certification tool

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
pip install requests
```

### Step 2 — Start your hub

```bash
# Example for dosync-node
DOSYNC_TOKEN=your-token node src/server.js

# Example for the Python reference hub
PYTHONPATH=. DOSYNC_TOKEN=your-token uvicorn server:app --host 0.0.0.0 --port 47200
```


### Step 3 — Run the suite

```bash
# Basic tier
DOSYNC_TOKEN=your-token python3 certify.py --host localhost --port <your-port> --tier basic

# Standard tier (recommended minimum)
DOSYNC_TOKEN=your-token python3 certify.py --host localhost --port <your-port> --tier standard

# Full suite including Emergency
DOSYNC_TOKEN=your-token python3 certify.py --host localhost --port <your-port> --tier emergency

# Save the signed report
DOSYNC_TOKEN=your-token python3 certify.py \
  --host localhost --port <your-port> \
  --tier standard \
  --output dosync-cert-standard.json
```

### Step 4 — Interpret results

A passing run looks like:

```
✓ CERTIFIED — DoSync STANDARD (32/32)
```

A failing run shows the specific test that failed:

```
✗ S07  Unknown intent rejected with 422
  Expected: status 422
  Got:      status 200
```

The `--output` flag writes a signed JSON report with all test results, timestamps, and hub metadata.

---

## Publishing Your Certification

There is no submission process. To claim certification publicly:

1. Save the JSON report with `--output dosync-cert-<tier>-<timestamp>.json`
2. Commit it to your repository (see [dosync-node/CONFORMANCE.md](https://github.com/giulianireg-spec/dosync-node/blob/main/CONFORMANCE.md) as a template)
3. Add a badge to your README:

```markdown
![DoSync Standard 32/32](https://img.shields.io/badge/DoSync-Standard%2032%2F32-orange)
```

---

## Certification Tiers in Practice

**Basic** — minimum bar. Proves the hub speaks the protocol. Suitable for: proof-of-concept implementations, embedded devices with limited resources.

**Standard** — recommended minimum for any deployment. Proves intents work end-to-end, events are handled, errors are returned correctly, and privacy requirements are met.

**Emergency** — required for safety-critical deployments. Proves the hub can be trusted in scenarios where milliseconds and audit trails matter.

---

## Partial Failures

If some tests fail, the report marks the implementation as `"certified": false` but still lists which tests passed. A hub that passes 28/32 Standard tests is not Standard-certified but the report shows exactly what remains.

Common failure patterns:

| Failure | Likely cause |
|---|---|
| B02 protocol version | `/v1/status` missing `protocol_version` field |
| S07 unknown intent 422 | Hub returns 200 or 400 instead of 422 for unknown intents |
| S13 version headers | Missing `X-DoSync-Protocol-Version` response header |
| S17 adapter_config | Hub exposing `adapter_config` in device detail endpoint |
| E3 audit integrity | SHA-256 chain broken or `GET /v1/audit` not implemented |

---

## TLS / HTTPS

If your hub runs over HTTPS, set the CA certificate path:

```bash
DOSYNC_TOKEN=your-token \
DOSYNC_CA_CERT=/path/to/ca.crt \
python3 certify.py --host your-hub --port 47200 --tier standard
```

---

## Reference Implementations

| Language | Repository | Certification |
|---|---|---|
| Python | [giulianireg-spec/dosync-protocol](https://github.com/giulianireg-spec/dosync-protocol) | Emergency 35/35 |
| Node.js | [giulianireg-spec/dosync-node](https://github.com/giulianireg-spec/dosync-node) | Standard 32/32 |

Both repositories include their `CONFORMANCE.md` with real test results.

---

*DoSync Protocol v0.1 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
