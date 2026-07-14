# DoSync — Conformance vs Integration testing

DoSync ships **two** test entry points that answer **two different questions**.
Running the right one for your goal — and reading its result correctly — matters,
especially if you are a manufacturer validating a device or a hub in your own lab.

## The distinction in one table

| | `certify.py` (conformance) | `integration.py` (integration) |
|---|---|---|
| **Question** | Does the hub speak the protocol correctly? | Does the real hardware actually do the thing? |
| **Determinism** | Deterministic — green by design | Depends on the deployment |
| **Hardware** | None required (acceptance mode) | Real devices, powered and reachable |
| **On a powered-off device** | Still passes (protocol is fine) | Reports `no-op` (a real condition, not a failure) |
| **Result shape** | PASS / FAIL per test; tiered CERTIFIED | Outcome per intent: executed / partial / no-op / error |
| **What it proves** | Protocol correctness | Physical execution on THIS deployment |
| **Fails the protocol?** | Yes, on a real protocol violation | No — it reports outcomes; it never fails the protocol |

## Why they are separate

A powered-off bulb must **not** turn a protocol certification red — that would
conflate "the hub implements the protocol" with "every device happened to be on."
Conformance certifies the **protocol**; integration exercises the **deployment**.
Keeping them in separate files keeps each answer clean.

Concretely: `certify.py` fires intents in **acceptance** mode (`fire_intent_conformance`)
and never waits for a device to move. `integration.py` fires real intents and
**polls to completion** (`fire_intent`), then classifies what physically happened.

## When to run which

- **Certifying protocol conformance** (the badge, the CI gate, a third party
  re-running your cert): `certify.py --tier {basic,standard,emergency}`. This is
  the artifact that means "this hub is a conformant DoSync implementation."
- **Validating that your devices actually actuate** (bringing up a new adapter,
  a hardware smoke test, a demo dry-run): `integration.py`. Its report is a
  point-in-time snapshot of a specific deployment, explicitly **not** a
  conformance certification.

## Usage

```bash
# Conformance (deterministic; use --certify for no hardware at all)
DOSYNC_TOKEN=<token> DOSYNC_CA_CERT=certs/ca.crt \
  python3 certify.py --host <ip> --port 47200 --tier emergency

# Integration (physical execution on the live deployment)
DOSYNC_TOKEN=<token> DOSYNC_CA_CERT=certs/ca.crt \
  python3 integration.py --host <ip> --port 47200 --json integration-$(date +%F).json
```

The integration report carries `"kind": "physical-execution"` and a note stating
it is not a conformance certification, so the two artifacts can never be confused
downstream.
