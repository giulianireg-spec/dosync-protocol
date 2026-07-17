# DoSync — Resolver recall: methodology and results

*Measured 2026-07-12 on the production reference deployment (Raspberry Pi 5, 30 devices).
Supersedes the semantic-accuracy figures in the IEEE WF-IoT submission (precision 0.85,
recall 0.49) — see "Relationship to the paper" below. The submitted manuscript is not
modified; this document is the current, reproducible measurement.*

---

## TL;DR — there are two numbers, and that is the point

| Metric | Value | What it measures |
|---|---|---|
| **Resolver recall** | **1.0** | Protocol property: given correctly-declared devices, does the resolver find everything relevant to an intent? |
| **Pre-policy precision** | **0.685** | NOT a resolver defect: the surface a deployment's policy layer trims. The resolver offers everything *capable*; deployment policy decides what *should* act. |

Both are honest; they measure different layers. Reporting only one would misrepresent the
system.

---

## Why the old number (recall 0.49) is invalid

The submitted paper reported mean precision 0.85 / recall 0.49 / F1 0.46 over 15 scenarios,
produced by `tools/scoring_sensitivity.py`. That measurement is **doubly invalid** and must
not be quoted going forward:

1. **It measured a frozen copy, not the live resolver.** The tool carried its own copy of
   the scoring logic and its own resolution map, over 13 intents — of which **8 no longer
   exist** as protocol seeds (the protocol converged on 5 universal intents + deployment
   customs). Its per-scenario recall was binary (≈1.0 or 0.00), and at least one 0.00
   (`report_status`) was an artifact of the frozen copy lacking the live resolver's
   read-only branch.

2. **The live resolver had a wiring bug that emptied every resolution.** `_get_resolution`
   read `self.hub` while `StateAwareResolver` stores `self._hub` (and, by a second path,
   the production `ExternalResolver` built its fallback unwired). Every intent resolution
   came back empty; non-emergency intents were semantically dead and emergency behavior
   rode on the empty-resolution "build everything" fallback. Fixed 2026-07-12
   (`tests/test_resolution_wiring.py` pins it). The old tool could not have seen this — it
   never ran the live resolver.

The number below is measured against the **live resolver, on the real deployment, after the
fix.**

---

## Methodology

**Instrument:** `tools/recall_benchmark.py` imports the live resolver and the live DB seeds
(no frozen copies), loads a device registry, runs each ground-truth case through
`resolver.resolve()`, and reports per-intent precision/recall/F1. Every miss is annotated
with the resolver's own `explain()` exclusion reason, so a miss is actionable, not a bare
number.

**Registry:** the production registry exported live from `GET /v1/devices` (30 devices: 10
native WiZ bulbs, PIR, DHT22, an SMS notifier, a test siren, and Home-Assistant-bridged TVs
and internal HA sensors).

**Ground truth — the honesty control.** An early run used a ground truth *derived from the
device tags*. It scored 1.0/1.0/1.0 — and that near-perfect result was **partly circular**:
the expected set was generated with rules close to the resolver's own, so agreement was
likely by construction. It verified end-to-end wiring on real data (valuable) but not that
the resolver's output matches operator intent.

So the reported number uses an **operator-authored ground truth**: the deployment owner
stated, in plain language and *without looking at the tags*, which devices should respond to
each universal intent for their home. Divergences between that intent and the resolver are
therefore **true semantic findings**, not tautologies.

---

## Results (operator ground truth)

| Intent | Recall | Pre-policy precision |
|---|---|---|
| `ensure_safety` | 1.0 | 0.80 |
| `alert_anomaly` | 1.0 | 0.29 |
| `control_access` | 1.0 | 1.0 |
| `report_status` | 1.0 | 1.0 |
| `notify` | 1.0 | 0.33 |
| **Mean** | **1.0** | **0.685** |

**Recall is 1.0 across every intent** — the resolver finds everything the operator wants,
on real data. This is the figure that replaces the paper's 0.49, and it is the resolver's
protocol-level metric.

**Precision below 1.0 is the deployment-policy surface, not a resolver error.** Every
false positive is a device that is genuinely *capable* but that this operator chose to
exclude:

- `ensure_safety` (0.80): the two TVs + the TV's HA switch. They *can* show on-screen
  notices (`communication`/`display` are true capabilities); the operator excludes screens
  from emergency response.
- `alert_anomaly` (0.29): the HA-internal Sun and Backup sensors. They *are* sensors
  (`sensor` is true); the operator doesn't want them swept into an anomaly check.
- `notify` (0.33): the two TVs again — capable of display, excluded by preference.

These are **not fixed by mutilating tags** (the capabilities are real) and **not by
hard-coding the operator's choices** (that would bake one home into the protocol). They are
fixed by the **PolicyEngine as deployment configuration** — see the design decision below.

### One thing that WAS a data fix (not preference)

The benchmark also surfaced a genuine *declaration error*, distinct from preference: an
HA-bridged light (Ambilight) declared `emergency_capable=true` and a spurious `climate`
tag. That is a false declaration, not a preference, so it was corrected at the source
(`HA_DOMAIN_MAP`: bridged lights → `emergency_capable=false`, tag `light`). The distinction
matters: **fix false declarations in the data; express preferences in policy.**

---

## Design decision: preferences are deployment config, not protocol (panel, 2026-07-12)

An expert panel considered whether these exclusions belong in the protocol, the reference
hub, or the deployment. Unanimous: **deployment configuration.** The protocol defines *how*
intent maps to capability; *which* devices exist and *what* preferences apply is the
deployment's — exactly as HTTP does not know which URLs live on your server. Consequences:

- Device manifests **declare true capabilities**; a tag is never removed to express a
  preference.
- Preferences (exclusions, confirmations, quiet hours) live as **PolicyEngine configuration
  loaded from a deployment file** — shareable and forkable between deployments, which is how
  a future ecosystem of shared configurations emerges without the project curating it.
- The reference hub must not bake any single deployment's choices into code. *(Known gap,
  tracked separately: `server.py` currently hard-codes example policies with concrete hours
  and device IDs — these should move to an example deployment policy file once a policy-config
  loader exists.)*
- The benchmark therefore measures **pre-policy** deliberately: it characterizes the
  resolver (a protocol property). A future **post-policy** mode will measure a configured
  deployment (an operator property). Both are valid; they measure different layers.

No device is mandatory. Another deployment without a PIR simply doesn't register one, and
its ground truth differs accordingly — the protocol imposes no device inventory.

---

## Relationship to the paper

The submitted IEEE WF-IoT manuscript (§ H3, Table: precision 0.85 / recall 0.49 / F1 0.46)
reflects the state at submission and is **not modified**. This document supersedes those
semantic-accuracy figures for all purposes going forward: the 0.49 was measured by a frozen,
bitrotten tool against a resolver that had an unrelated resolution-wiring bug. The current,
reproducible measurement is **recall 1.0** (resolver) with **pre-policy precision 0.685**
(the policy-layer surface). If the paper is revised or a follow-up is written, these are the
figures and this is the methodology to cite.

---

## Reproducing

```bash
# export the live registry
curl -sk https://<hub>/v1/devices --cacert <ca> -H "Authorization: Bearer <token>" \
  -o benchmarks/fixtures/prod_registry.json

# run against an operator-authored ground truth
PYTHONPATH=. python3 tools/recall_benchmark.py \
  --registry benchmarks/fixtures/prod_registry.json \
  --truth   benchmarks/fixtures/prod_ground_truth_operator.json \
  --json    benchmarks/recall-prod-operator-<date>.json
```

Artifacts: `benchmarks/fixtures/prod_ground_truth_operator.json` (the operator ground truth),
`benchmarks/recall-prod-operator-2026-07-12.json` (this run). The fixture registry and a
labeled synthetic fixture (`benchmarks/fixtures/recall_registry.json`, scores 1.0/1.0/1.0 as
a wiring self-test) are also included.

## Post-policy mode (2026-07-17) — the operative number

POL-1 made the policy layer configuration (`DOSYNC_POLICIES`), which unblocked measuring
what a deployment ACTUALLY EXECUTES, not just what the resolver proposes:

```
PYTHONPATH=. python3 tools/recall_benchmark.py \
  --registry benchmarks/fixtures/prod_registry.json \
  --truth    benchmarks/fixtures/prod_ground_truth_operator.json \
  --policies /etc/dosync/policies.json \
  --json     benchmarks/recall-prod-postpolicy-<date>.json
```

Every case is scored twice — on the resolver's raw plan (pre) and on that plan after
`PolicyEngine.evaluate()` with the deployment's own policy file (post), at the case's
ground-truth urgency so `bypass_on_emergency` semantics are measured exactly as they run.
Only deployment policies are loaded (rate limiting would block later cases and measure the
benchmark itself; conflict resolution needs live intent state).

**The two numbers answer different questions, and neither replaces the other:**

* **Pre-policy** measures the SEMANTIC LAYER against truthfully-declared capabilities. A TV
  that is genuinely display-capable and resolves for `notify` is a *correct* resolution even
  if this operator doesn't want it — precision 0.685 against the operator ground truth is the
  honest cost of refusing to mutilate capability declarations to encode preferences.
* **Post-policy** measures THE DEPLOYMENT: resolver plus the operator's own declared
  preferences. This is the number an evaluator should read as "what this installation does".

Quoting one as the other is how the 0.49 mistake happened; the report carries both plus the
per-case policy decision (`allow`/`modify`/`block`) and exactly which devices the policy
removed, so the delta is attributable line by line. When a policy contradicts the ground
truth (removes a device the GT expects), post-recall DROPS — the tool does not paper over an
operator whose policies fight their own expectations.

### Production run 2026-07-17 — the first post-policy numbers

Live registry export + operator ground truth + the deployment's real policy file
(`/etc/dosync/policies.json`: never_after_hours, require_confirmation, and an ABSOLUTE
device_exclusion of both TVs for notify/ensure_safety):

| intent | urgency | precision | →post | recall | →post | policy | removed |
|---|---|---|---|---|---|---|---|
| ensure_safety | emergency | 0.800 | **0.923** | 1.0 | 1.0 | modify | both TVs |
| alert_anomaly | alert | 0.294 | 0.294 | 1.0 | 1.0 | allow | — |
| control_access | alert | 1.000 | 1.000 | 1.0 | 1.0 | allow | — |
| report_status | info | 0.500 | 0.500 | 1.0 | 1.0 | allow | — |
| notify | info | 0.333 | **1.000** | 1.0 | 1.0 | modify | both TVs |
| **MEAN** | | **0.585** | **0.743** | **1.0** | **1.0** | | |

**Policy effect: precision +0.158 at zero recall cost** — the operator's declared preferences
removed exactly the devices their ground truth never expected, and nothing else. The absolute
exclusion is visible in the emergency row: TVs out at EMERGENCY urgency, measured.

**Every remaining tenth has a name.** The gap from 0.743 to 1.0 is fully attributed, and the
four causes are different in kind:

1. **`ensure_safety` → `rpi-pir-01`** (1 device). The resolver reads the motion sensor during
   a safety event; the operator GT did not list it. A read-only action with a defensible
   rationale (situational awareness) — this is a GT-vs-preference judgement for the operator:
   either the GT gains the PIR, or a policy excludes it. Not a bug on either side.
2. **`alert_anomaly` → both TVs** (2). The deployment's exclusion covers
   `["notify", "ensure_safety"]` only — `alert_anomaly` is not in the list. If the operator's
   preference is universal, the fix is one line in THEIR policy file. This is the mechanism
   working as designed: the number tells the operator exactly where their own declared scope
   ends.
3. **`alert_anomaly` → 10 HA housekeeping entities** (sun_next_* ×6, backup_* ×4). Home
   Assistant internals (sunrise times, backup status) registered as DoSync devices and
   resolving for anomaly monitoring. A registry-hygiene question for the deployment: these
   arguably should not be registered at all (HA-bridge import filter), or excluded by policy.
4. **`report_status` → 14 device-state readers** (10 WiZ bulbs, Ambilight, a TV switch, both
   TVs). Exactly the SENSOR-KIND finding: `report_status` reads every device declaring ANY
   sensor, and lamps truthfully declare brightness/state. The designed fix is
   `SensorSpec.kind` (environment vs device_state) with a scoped `report_status` — protocol
   evolution, backlogged, NOT to be faked by mutilating declarations.

**A note on drift, for honesty:** the 2026-07-12 pre-policy mean was 0.685; today's is 0.585
over the same ground truth. The registry is a LIVE deployment — ghost devices were
deregistered and HA-bridge mappings corrected between the two runs — so pre-policy numbers
are dated snapshots, not a fixed property. (The per-case artifact of the 07-12 run was
caught by the later `.gitignore` rule for per-run artifacts and never committed, so the
drift is not diffable per-case from the repo.) The comparable pair is always pre vs post of
the SAME run: **+0.158 precision, ±0.0 recall.**

*The old `tools/scoring_sensitivity.py` is retained only for historical reference and is
bitrotten; do not use it. `tools/recall_benchmark.py` replaces it.*
