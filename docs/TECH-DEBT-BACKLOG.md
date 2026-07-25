# DoSync — Technical debt backlog (living)

*Items surfaced during audits and deferred deliberately. Each is verifiable against the repo.*

## POL-1 — Deployment policy config loader — DONE 2026-07-14 · effort M
The reference hub hard-codes example policies in `server.py` (concrete quiet hours,
concrete device IDs in NeverAfterHoursPolicy / RequireConfirmationPolicy). Per the
2026-07-12 panel (see DoSync-Panel-Frontera-Deployment), device preferences are
DEPLOYMENT configuration, not protocol or reference-hub code. Work:
  (a) define a deployment policy config format (JSON),
  (b) add a loader so the hub reads policies from a deployment file,
  (c) move the example policies out of server.py into an example deployment file
      (e.g. examples/policies.deployment.json).
This also enables the benchmark's future POST-policy mode and the shareable-config
future the project wants to enable (without curating that catalog).
Validation: hub loads policies from a file; server.py contains no deployment-specific
hours or device IDs; benchmark can run pre- and post-policy.

**DELIVERED 2026-07-14.** `dosync/policy_config.py` loads deployment policies from a JSON file
(DOSYNC_POLICIES), server.py now constructs only infrastructure policies (rate limits, conflict
resolution, contextual weighting), and the two that were hard-coded — the 00:00-06:00 unlock
window and alarm confirmation, one household's choices inherited by every hub — moved to
examples/policies.deployment.json, usable as-is by anyone who wants them. Loading FAILS LOUDLY
by design: unknown type, bad argument or missing file stops the hub rather than silently
dropping a restriction the operator asked for. An AST test pins that server.py never again
constructs a deployment policy. Also unblocked: policies that carry no deployment values but
only suit some deployments (GeofencePolicy — "each deployment configures its own perimeter",
says its own docstring) previously could not be registered at all without forking the hub.
Remaining: (a) the post-policy benchmark mode below is now unblocked; (b) POL-2 (the bare
`except Exception` around policy setup) was found while doing this.

## QA-benchmark-postpolicy — Post-policy benchmark mode — DONE 2026-07-17 · effort S
`tools/recall_benchmark.py` measures the resolver PRE-policy (a protocol property,
recall 1.0). Once POL-1 lands, add a mode that applies the PolicyEngine so the tool
can also report the configured-deployment precision (an operator property). Both
numbers are honest; they measure different layers (see docs/BENCHMARK-RECALL.md).

**DELIVERED 2026-07-17.** `--policies <file>` scores every case pre- AND post-policy at its
ground-truth urgency, reporting per-case decision (allow/modify/block), the exact devices
removed, and the mean delta. Only deployment policies load (rate limiting would measure the
benchmark itself). Fail-loudly inherited from the loader: a typo'd policy file raises instead
of silently scoring as "no policies". Both honest directions pinned by tests: post-precision
RISES when the operator GT agrees with the exclusion, post-recall DROPS when a policy removes
a device the GT expects. Production numbers live in docs/BENCHMARK-RECALL.md.

## SENSOR-KIND — Distinguish environment sensors from device state — DONE 2026-07-17 · effort M
`report_status` (empty-resolution read-only branch) reads every device that declares ANY
sensor. WiZ bulbs declare brightness+state as SensorSpec, so a status query over the home
reads 28 devices — 20 of them lamp brightness/on-off, not environmental sensing. The number
is correct but misleading, and the query is genuinely ambiguous ("all state" vs "environment").
Panel (DoSync-Panel-SensorKind) consensus: the SensorSpec stays (brightness IS real telemetry;
hiding it would be lying, the TV mistake we rejected), but the MODEL should distinguish the two:
  (a) add SensorSpec.kind: "environment" | "device_state" (default "environment", non-breaking),
  (b) make report_status scope selectable by context/policy ({"scope": "environment" | "all"}),
      with the default being deployment preference (a hospital may want all; a home may want
      environment only) — protocol distinguishes, deployment decides.
This is protocol evolution (touches the spec + adapters that declare sensors) and enables
answering precisely "how many environmental sensors vs device-state readings". Not urgent.

**DELIVERED 2026-07-17, exactly as paneled.** `SensorSpec.kind` ("environment" |
"device_state", default environment — every existing manifest stays byte-for-byte valid,
grain PER SENSOR: a thermostat's current_temp measures the room while its target_temp is a
setpoint). `report_status` scope: intent.context["scope"] wins per-query, else
DOSYNC_STATUS_SCOPE (deployment config), else "all" (unchanged behavior — the protocol
gained a distinction, not an opinion). Adapters declare truthfully: WiZ brightness/state →
device_state; HA_DOMAIN_MAP per-sensor kinds across 8 domains (sensor/binary_sensor stay
environment). Field documented in spec/DoSync-SPEC-v0.1.md §5.1. Serialization round-trips
all four reconstruction paths (to_dict via __dict__, hub restore, register endpoint,
benchmark load_registry) with legacy manifests defaulting. Migration note: persisted
manifests carry no kind until devices re-register (WiZ --register, HA bridge re-import).
EXPECTED effect once the deployment re-registers and opts in (DOSYNC_STATUS_SCOPE=environment):
report_status 28→14 reads, precision 0.5→1.0, post-policy mean 0.743→~0.843 — numbers to be
CONFIRMED by the production benchmark, not assumed; this line gets updated when they exist.
Validation: a report_status can be scoped; metrics can separate the two sensor kinds.


## DEVICE-HEALTH-ACTIVE — Device heartbeat + cause attribution — (b) CONFIRMED 2026-07-21 · effort M
PARTIALLY DELIVERED 2026-07-14. The wiring audit found that active probing already existed:
a background refresher polling get_state() every 60s. It had never run in production because
server.py gated it on `isinstance(hub.resolver, StateAwareResolver)` — always False under the
ExternalResolver production runs — and said so only at debug level. It is now hub-owned
(DoSyncHub.start_state_refresh), runs under any resolver, and marks devices reachable on a
successful probe, so recovery is detected within one interval WITHOUT executing any action.

  DONE (a) periodic lightweight probe so health is known without acting.
  DONE (b) device-initiated heartbeat — SHIPPED 2026-07-21. New protocol surface
           `POST /v1/heartbeat` (spec §7.4): a device the hub CANNOT poll (behind NAT,
           sleeping, inbound-blocked) asserts liveness by reaching out. Feeds the SAME
           DeviceHealth.mark path as the probe (record_heartbeat), stamps last_heartbeat,
           stores an optional free-form self-report verbatim, surfaces in
           /v1/health/reachability with a note distinguishing the signal source. Preserves
           the positive-signal-only asymmetry: a heartbeat clears a stale unreachable mark
           but never CREATES one — absence of heartbeats is weaker evidence than an action
           timing out. Unknown devices rejected 404 (a heartbeat asserts identity). Tests
           pin the asymmetry and verify the clear-on-recovery; validated end to end against
           a running hub. **CONFIRMED in production 2026-07-21:** POST /v1/heartbeat for
           rpi-dht22-01 acknowledged; /v1/health/reachability shows last_heartbeat stamped,
           note "last confirmed by a device-initiated heartbeat", and the self-report
           {uptime_s, firmware} stored verbatim.
  DONE (c) distinguish "powered off" from "network-unreachable" — SHIPPED 2026-07-21. The
           honest design: a UDP command timeout (WiZ) is identical for power loss and network
           loss, so (c) does NOT guess from the timeout (that would be a workaround). It
           cross-references the independent heartbeat signal (part b) via
           DeviceHealth.reachability_assessment and returns a cause WITH evidence and
           confidence — network_or_app (heartbeat <90s ago but unresponsive: alive just now),
           likely_powered_off (long heartbeat silence + unresponsive), or indeterminate
           (never heartbeat'd: transport genuinely cannot tell, stated plainly). Exposed per
           unreachable device in /v1/health/reachability `assessments`; documented spec §7.4.
           Answers the panel's operator question (#2, Delgado): "is this something I need to
           go fix?" — network_or_app yes, likely_powered_off check the power. 6 tests pin the
           calibration incl. the honest indeterminate case. **CONFIRMED in production
           2026-07-21:** an emergency drill left 4 powered-off WiZ bulbs (comedor-01/02,
           living1-01/02) unreachable; each reported `indeterminate (low)` — "device never
           sent a heartbeat, so power and network cannot be distinguished" — which is the
           correct, honest verdict: those bulbs never used heartbeat, so the UDP transport
           genuinely cannot tell, and the system says so rather than guessing.

Note the deliberate asymmetry, preserved from the original design: the probe is POSITIVE-SIGNAL
ONLY. A device that fails get_state() is skipped, never marked unreachable — a failing probe is
weaker evidence than an action timing out (adapters implement get_state unevenly), and marking
on weak evidence would manufacture false "dead device" reports. Only real action timeouts mark
a device unreachable.


## POL-2 — server.py swallows PolicyEngine build failures — CLOSED 2026-07-15 (by incident) · effort S
`server.py` wraps the whole policy-engine setup in a bare `except Exception` that logs one
warning and continues. Any failure while registering policies therefore yields a RUNNING hub
with fewer restrictions than intended — silently, behind a warning nobody reads. Found while
wiring POL-1: the deployment policy loader was written to fail loudly, and this handler
defeated it outright (a typo'd policy file started a hub with the operator's restrictions
absent). PolicyConfigError is now re-raised explicitly, which fixes the configured-file case,
but the general smell remains: a hub that cannot build its safety policies is not safe to run.
Work: narrow the handler to what it actually means to tolerate (a missing optional module),
and decide whether any other policy-setup failure should be fatal.
Validation: a forced failure in policy setup does not produce a silently-unprotected hub.

**CLOSED 2026-07-15 — the smell stopped being theoretical within a day.** A NameError in the
policy setup block (a log call before `log` existed) was swallowed by this exact handler,
right before `hub.policy_engine = policy_engine`: the engine was built and all seven policies
registered — and the hub never attached to it. Production ran with hub.policy_engine=None (no
deployment policies, no rate limits, no conflict resolution) while an emergency intent drove
devices the operator had absolutely excluded. The handler is now fatal: nothing in that block
has environmental failure modes (core imports, in-memory construction; the deployment file is
already fatal via PolicyConfigError), so any failure there means a hub without its policy
layer — and that hub must not run. Regression pinned on the RIGHT object
(server.hub.policy_engine, not the module-level variable the original validation checked).

## HA-BRIDGE-HYGIENE — Housekeeping entities imported as devices — SHIPPED 2026-07-19 · effort S
The HA bridge imports EVERY entity in mapped domains, including Home Assistant's own
housekeeping: sun.sun times (sun_next_* ×6) and backup status (backup_* ×4) become DoSync
"devices" and resolve for alert_anomaly (benchmark cause #3 — the largest remaining
precision gap, alert_anomaly at 0.294). This is bridge-standard behavior, not one
deployment's quirk: every HA deployment has these entities, and none of them are devices in
any meaningful sense. Frontier ruling (operator, 2026-07-17): causes #1 (PIR in the GT) and
#2 (TVs in alert_anomaly scope) are deployment decisions and stay in the operator's files;
THIS one belongs to the bridge. Design question to settle before implementing: skip-list of
HA integration sources by default (sun, backup — with an opt-in to import them), vs a
configurable entity/domain exclusion in the bridge config. Either way the default should
not register housekeeping as devices. Validation: fresh HA import registers no sun/backup
entities; alert_anomaly precision moves accordingly in the production benchmark.

**SHIPPED 2026-07-19.** Design resolved per the frontier ruling: skip-list by DEFAULT
(`HA_HOUSEKEEPING_PREFIXES = ("sun_", "backup_")` — deliberately minimal, only integrations
we KNOW; the trailing underscore protects real sensors like `sunroom_temperature`), opt-in
via `DOSYNC_HA_IMPORT_HOUSEKEEPING=true`, plus deployment-declared extra exclusions via
`DOSYNC_HA_EXCLUDE_ENTITIES` (comma-separated prefixes). The operator ground truth was
updated with the same logic — if they are not devices, they cannot be expected devices
(report_status expected 14→4). Found along the way: a pre-existing test bypassed __init__
with `__new__` and broke the day the constructor gained state — now uses the real
constructor (which does no I/O). **CONFIRMED in production 2026-07-19:** the 10 live entities deregistered (all
"unregistered"), registry at 20 devices with zero housekeeping, and a fresh HA import
brings none back. Measured: alert_anomaly precision 0.294→0.714 (the predicted number,
exactly — the remaining gap is the 2 TVs, operator decision #2 pending), report_status
1.0/1.0 against the updated GT (4 expected, 4 read), post-policy mean 0.743→0.927
(predicted ~0.927), recall 1.0 throughout. With this, every benchmark cause that belonged
to the STANDARD is closed and measured; the distance from 0.927 to 1.0 is entirely
operator decisions (#1 PIR in ensure_safety, #2 alert_anomaly in the exclusion scope).

## AUDIT-PROVENANCE — Chain must bind decisions, not only commands — SHIPPED 2026-07-18 · effort M
Origin: external dev.to review (2026-07-18). Verified: BLOCK and CONFIRM leave chain
entries; a policy MODIFY leaves NONE — hub.py replaces the plan and `intent_executed`
records only the post-policy action count. The device removals live in the runtime log
(rotating, not tamper-evident), so the public claim "the record shows what was proposed
and what the rules decided" is currently ahead of what the hash chain binds. Work: a
`policy_modified` chain entry (or fields on `intent_executed`) binding the SHA-256 of the
active policy file, the pre-policy plan (device_ids), the removed devices and deciding
policy, and the decision; attach sensor evidence where the domain already produces it
(vehicle telemetry). Validation: the care-facility scenario (absolute exclusion at
emergency) is fully reconstructible from the chain alone, with `audit-verify` green.

**SHIPPED 2026-07-18** (same day as the public commitment in the dev.to reply). A MODIFY now
leaves a `policy_modified` chain entry binding: pre-policy plan, post-policy plan, removed
devices, deciding policy, the policy's OWN declared reason (the engine used to collapse it
into a generic phrase — the operator's words now travel to the chain), and the SHA-256 of
the exact policy file bytes that were loaded (hashed at read time, not re-read from disk).
Reconstructibility pinned by test: the care-facility decision recovers from the chain alone.
**CONFIRMED in production 2026-07-18** (drill int-1784426941-211fc0): `policy_modified` in
the live chain with both TVs removed, the operator's own declared reason carried verbatim,
and the fingerprint matching the SHA-256 of the live /etc/dosync/policies.json. Chain valid
over 24,022 entries. Shipped, deployed and production-validated the same day as the public
commitment in the dev.to reply.

## EMERGENCY-UNSAT-ESCALATION — A silent no-op emergency is the worst state — SHIPPED 2026-07-18 · effort S
Origin: same review. Verified: stacked absolute exclusions can empty an emergency plan
and today that executes zero actions silently (status completed). Rejecting the plan is
the WRONG fix — it would override the operator's declared judgment, which this layer
refuses to do; the failure is silence, not obedience. Work: (a) executing an EMERGENCY
intent whose post-policy plan is empty emits a dedicated audit entry + operator
notification ("emergency fired, 0 actions after policy filtering — your standing rules
made this intent unsatisfiable"); (b) config-load lint against the live registry warning
when declared exclusions can empty an emergency-class intent. Validation: the drill
produces the loud path, and the lint fires the day the rule is written.

**SHIPPED 2026-07-18.** (a) An emptied EMERGENCY plan now leaves a dedicated
`emergency_unsatisfiable` chain entry + log.critical; the rules are still honored (0 actions
execute — refusing to obey the operator is not the fix; silence was). Emptied non-emergency
plans stay quiet: a preference doing its job is not an incident. (b)
`lint_emergency_satisfiability` runs at policy load against the live registry through a
THROWAWAY engine (linting must not consume production rate-limit state) and warns the day
the rule is written. **CONFIRMED in production 2026-07-18:** the lint ran at startup and
stayed correctly silent (the deployment's rules are survivable — the siren remains); the
loud path is pinned by tests. Note for the radar: the live chain reached 24,022 entries
(~12.6k on Jul 14) — the audit archiving item keeps growing more relevant.

## INDEPENDENT-OBSERVATION — Device ack ≠ observed reality — SHIPPED 2026-07-21 · effort M/L
Origin: same review. Completion is confirmed (never assumed) and vehicles are supervised
against reconciled telemetry, but systematic cross-verification against an independent
sensor ("the lock says locked AND the door sensor agrees") is not expressible in the
protocol. Explore: an optional `verify_with` binding on consequential actions (sensor id +
expected reading + deadline), feeding partial/failure states. Position recorded on the
saga framing: COMPENSATION does not transfer cleanly from transactions to the physical
world (you cannot un-notify a person; compensating half an emergency can be harmful — the
codebase already overrides ABORT to CONTINUE for emergencies). Compensation, if ever, is
deployment-declared policy, never protocol-automatic. Manual escalation as a formal state
is the undefended gap and belongs to EMERGENCY-UNSAT-ESCALATION's family.

## AUDIT-ARCHIVE — Segment the chain with a hash anchor — CONFIRMED 2026-07-20 · effort M
The live chain grew without bound (24,022 entries at the reference deployment, roughly
doubling every few days), all reloaded into memory at every hub start. Design: anchored
segments. `manage.py db audit-archive --keep N --out FILE` (dry-run default, --apply with
the hub STOPPED) moves the oldest entries to a self-describing segment file that records
the anchor it chains FROM; the DB stores the new anchor in `audit_meta`; live verification
(AuditLog.verify, manage audit-verify, backups) starts from the anchor instead of genesis;
consecutive generations interlock (segment N+1's anchor == segment N's last_hash), so the
FULL history stays verifiable end to end. The act of archiving leaves its own chain-bound
`audit_archived` entry carrying the segment file's SHA-256 — the same philosophy as
policy_modified binding the policy file: a silently swapped archive would contradict the
hash the chain remembers. Fail-loudly: refuses to archive a chain that does not verify
(archiving would enshrine the corruption). Backups are now self-contained (carry their
anchor). Pinned by tests: standalone segment verification, restart continuity, generation
interlock, tamper detection on both sides, broken-chain refusal. **CONFIRMED in production 2026-07-20** (generation 1): 28,189 live entries → 26,189 archived
to a segment (sha256 2a8841ae…), 2,000 kept live. Live DB verifies anchored (`generation 1,
26189 archived`, Chain valid ✓); the segment verifies standalone (Chain valid ✓); the
audit_archived entry binds the segment's sha256. The operational win landed as designed:
the hub restored 2,001 entries instead of 28,189 — the chain no longer reloads unbounded
history into memory at every start, with zero loss of end-to-end verifiability.

## CERT-CONFORMANCE-TIER — Certify the v0.4 protocol features — CONFIRMED 2026-07-20 · effort S
Everything shipped in the 0.4 cycle (SENSOR-KIND, AUDIT-PROVENANCE, EMERGENCY-UNSAT,
AUDIT-ARCHIVE) had unit tests but no CONFORMANCE coverage — nothing proved, over the wire
against a running hub, that the protocol delivers what its spec now promises. New
`--tier conformance` (52 tests, cumulative over emergency) adds 8 C-series checks: sensor
kinds valid and expressed (C01–C02), report_status accepts an explicit scope (C03),
policy MODIFY leaves a chain-bound entry with full provenance and a SHA-256 fingerprint
(C04–C06), the live chain verifies (C07), and an anchored/archived chain still verifies —
segmentation preserves integrity (C08). `/v1/status` now surfaces `audit_anchored` +
`audit_anchor_prefix` so C08 can check archive integrity over the wire. Validated against a
running hub with a modifying deployment policy: all 8 C-tests pass (the 2 environmental
fails — B04 auth-off, S23 jsonschema-absent — predate this work and are simulation-mode
artifacts). The old radar item "certification tests 16→30+" is obsolete — the suite was
already at 44; it is now 52.

**CONFIRMED in production 2026-07-20** — full conformance tier against the live Pi hub over
mTLS: **52/52, ✓ CERTIFIED**, Ed25519-signed report (fingerprint a41aa0bc…, key 17b987f4…).
The C-series numbers narrate the whole 0.4 cycle at once: C01 32 sensors all valid, C02 25
device_state (SENSOR-KIND), C04–C06 5 policy_modified entries with the live fingerprint
f379fb95… (AUDIT-PROVENANCE), C08 anchored=True from 9a546cd5… — the chain is archived AND
still verifies (AUDIT-ARCHIVE). B04/S23 pass in production (they were simulation-mode
artifacts, as predicted). The signed report is third-party-verifiable without hub access
(`certify.py --verify`) — independent proof of protocol conformance for the IEEE paper and
any grant.

## MCP-V13 — Partial response before the global timeout — CONFIRMED 2026-07-21 · effort M
Radar v0.3 item, and the fix for the known gap "MCP tool reports failure when the intent
executes correctly but devices are physically off". Before: a poll of a still-executing
intent returned an opaque {status: pending}, and the MCP tool on timeout said only "still
processing — check the audit log", losing every action that had ALREADY fired. Now the hub
publishes progress as each action completes — via an OPTIONAL progress_cb threaded through
execute_intent and injected by a thin executor wrapper, so the three execution strategies
(_execute_abort/_execute_retry/_execute_parallel) are untouched and the no-callback path is
byte-for-byte the old behavior. GET /v1/intent/{id} on a pending intent now carries a
`partial` block (actions_completed + per-action results), documented in spec §7.2; the MCP
tool reads it on timeout and reports "N actions succeeded so far, M still pending" instead
of a blind message. The callback is best-effort — a raising progress_cb is swallowed (an
observer cannot break the observed). Found along the way: the main execution path called the
new cb-wrapper WITHOUT passing progress_cb (silent no-op) — caught by the smoke test, not by
reasoning; fixed and pinned. 627/627; the HTTP partial exposure verified to fail with the
bug reintroduced. **CONFIRMED in production 2026-07-21** (drill int-1784606153-18aebe): a
real emergency over the live registry executed 23 actions — the responsive devices recorded
success (both living lamps on at brightness 100, the SMS sent, the alarm activated, the PIR
read) while 16 powered-off WiZ bulbs timed out at 5.0s. This is precisely the case v13
exists for: the successful actions survive the others' timeouts, so a caller learns "siren
and SMS fired, 16 lamps are off" instead of a blind failure. (The poll landed after
execution finished, so it returned the terminal `partial` rather than the pending
`partial` block — the pending block is the shorter-deadline path, exercised by the MCP
client's own timeout and pinned by the HTTP test.)

## RADAR-v11 — "Local fallback in WiZ/Shelly adapters" — WILL NOT IMPLEMENT (by design) · 2026-07-21
Reviewed against DESIGN-PRINCIPLES.md §"On adapter-side fallback". This radar item, as
worded, describes adapter autonomy — an adapter acting on a device when the hub is
unavailable — which the project has ALREADY decided against, deliberately and in writing:
"A protocol cannot have a 'mode without the protocol.'" Adapter-side bypass would break the
tamper-evident audit chain (actions with no chained entry), the policy engine (the TVs
WOULD act in an emergency — the exact thing the care-facility example forbids), and the
semantic model (actions become commands again). The correct resilience model is hub
availability, not adapter autonomy: FailurePolicy.RETRY for transient failures, emergency
snapshots re-firing on restart, StateAwareResolver TTL for temporary unavailability, and
systemd Restart=always. Closing this item rather than implementing it. Note: the 16 WiZ
timeouts in drill int-1784606153-18aebe are NOT a fallback problem — the lamps were
powered off; no local fallback can command a lamp with no power. That is DEVICE-HEALTH-ACTIVE
(c) territory (distinguish powered-off from network-unreachable), not v11.

## RESOLVER-SCORING-VALIDATION — One scoring source, validated (radar v9) — CONFIRMED 2026-07-21 · effort S
The relevance scoring was computed in two places: `_relevance_score` (what the resolver
DECIDES with) and `explain()` (which recomputed the same arithmetic to tell the story),
with a comment promising they "must mirror exactly" — unenforced by the language. If one
drifted, the /v1/intents/{class}/explain endpoint would lie about why the resolver chose
what it chose: the worst failure for a transparency feature. Refactor: one computation,
`_score_breakdown`, returning a structured `ScoreBreakdown` whose `.total` is the score.
resolve() uses `.total` (via a thin `_relevance_score` wrapper — call sites unchanged);
explain() reads the components. The five weights are named constants referenced by both,
not magic numbers duplicated. Behavior is byte-for-byte unchanged (628→ full suite green,
including benchmark and resolution tests that depend on exact scores). Validation: the
explain==decision property is pinned, PLUS absolute-value tests (siren=52, location=25)
that anchor behavior to concrete numbers — the self-consistency check alone became
tautological once both paths read one source (caught by testing: a broken weight passed the
coincidence test but failed the absolute tests, which is the real regression guard).
**CONFIRMED in production 2026-07-21:** /v1/intents/ensure_safety/explain returns 20
evaluated / 17 included with coherent, decomposable scores (notifier-sms-01=42 = emergency
30 + notify actuator 12; WiZ bulbs=34), all now sourced from the single _score_breakdown
the resolver decides with. Internal refactor, no wire-format change; the explain endpoint's
numbers can no longer diverge from the decision.

## RADAR-v8 — "Consistency model for simultaneous intents" — ALREADY IMPLEMENTED · reviewed 2026-07-21
Reviewed before treating as pending work — and it is not pending. A consistency model for
concurrent intents already exists and is validated:

- **`dosync/device_arbiter.py`** (270 lines): per-device claims arbitrated by URGENCY rank.
  An emergency action asserts an open-ended claim on the devices it touches; a lower-urgency
  action targeting a claimed device yields. Claims are NOT fixed-duration locks (an earlier
  30s-TTL design was explicitly rejected for lingering after emergencies resolved) — a claim
  is HELD while the emergency intent is active, RELEASED on completion with a short `grace`
  window for straggler commands, and capped by `max_hold` so a wiring bug can never lock a
  device forever. Serialization is per-device; different devices stay fully parallel.
- **Same-urgency conflicts are deliberately the PolicyEngine's job, pre-dispatch** — a
  documented design decision, not a gap. Urgency is the arbitration axis at the executor
  boundary; class-priority arbitration belongs earlier in the pipeline.
- **`hub._active_intents` / `_active_intent_devices`**: active-intent registry for conflict
  detection, wired into execute_intent (claim asserted at dispatch, released in `finally`
  with a rank guard so a lower-urgency intent finishing first cannot start the grace on a
  higher-urgency claim).
- **Coverage: `tests/test_emergency_preemption.py` (12 tests)** — emergency-wins-over-routine
  in all orderings (before/during/after in-flight), claim held-until-released, grace
  expiry, max_hold cap, per-device isolation, both-routines-coexist (same-urgency), audit
  supersede hook, and the rank-guard release. All green.
- **`docs/BENCHMARK-CONCURRENT.md`**: zero timeouts at N=10 simultaneous intents on the Pi 5,
  CPU <15% — the event loop handles concurrent independent tasks without starvation.

Closing as already-satisfied. The one piece of refinement it named — a formal model of the
claim state machine for the paper — was DELIVERED 2026-07-21 (see CLAIM-STATE-MACHINE below).

## PANEL-POLISH-2026-07-21 — Four "effort S" items from the technical review — CONFIRMED 2026-07-21
Closed in-session, while the debt was fresh, per the panel's own recommendation.
- **#1 Heartbeat report size limit (Sosa).** POST /v1/heartbeat's `report` is now bounded:
  ≤32 keys, ≤4096 bytes serialized, rejected 422 otherwise. "No position on contents" is not
  "unbounded input" — a compromised device could otherwise persist a multi-MB report per
  heartbeat. Documented in spec §7.4. (Trap found: Pydantic v2 turns a `_`-prefixed class
  attribute into a ModelPrivateAttr, not an int — bounds live at module level.)
- **#3 Health endpoint cross-reference (Torres).** The three /v1/health/* endpoints now
  document which question each answers (historical success rate vs current reachability +
  heartbeat), so an integrator — or the team itself, which got it wrong once — picks right.
- **#4 Test taxonomy (Morales).** pytest.ini registers unit/e2e/asyncio markers; conftest
  auto-applies them by inspecting each test's source (TestClient → e2e, else unit) — no
  per-file tags to rot. 26 e2e / 604 unit, runnable as subsets (`pytest -m "not e2e"`).
- **#7 progress_cb failure counter (Paredes).** A failing progress callback is still
  swallowed (an observer can't break execution) but now counted (hub.progress_cb_failures),
  logged at WARNING not DEBUG, and surfaced in /v1/status — swallowed is no longer invisible.
All 637 tests green; #1 and #7 verified to fail with the bug reintroduced.
**CONFIRMED in production 2026-07-21:** normal heartbeat → 200, abusive 5000-byte report →
422, /v1/status reports progress_cb_failures: 0. The remaining
panel items (device-health (c), formal claim model for the paper, online archiving, light
heartbeat mode, third-party certification, end-user UI) are M/L or future-scope; they are now tracked in the HORIZON section at the end of
this file. They previously were not tracked anywhere in the repo — only inside a
meeting document outside it, which is this project's own recurring failure mode.


## CLAIM-STATE-MACHINE — Formal model of the arbiter claim FSM — SHIPPED 2026-07-21 · effort M
Panel #5 (parada técnica, Aguirre): the arbiter worked and was tested, but "works on my Pi"
needs a formal model to become "probably correct" — the difference that matters for the IEEE
paper. Delivered as spec §3.1 "Claim state machine (formal model)": four states
(ABSENT/HELD/RELEASING/EXPIRED) defined by predicates over the claim record, a full
transition table, and six invariants I1–I6 each annotated with the test that would fail if
violated. Crucially it is not prose-only: tests/test_claim_state_machine.py (8 tests) pins
the FSM directly against _Claim.is_active/release AND a meta-test verifies every invariant
the spec cites is backed by a real, present test — the formal model cannot drift from the
code or cite phantom tests (both verified to fail when a citation or a predicate is broken).
The two-emergencies-same-device open edge (Benítez #6) is recorded in the spec as future
work rather than silently omitted. 645/645.


## INDEPENDENT-OBSERVATION implementation notes — 2026-07-21
Designed by the expert panel in session (see DoSync-Panel-Diseno-IndependentObservation),
then built to that design.

**Panel decisions honored.** (D1) The binding is declarable in BOTH places — manifest for the
manufacturer's natural pairing, intent context for the deployment cross-device link, intent
wins (a manufacturer cannot know sensors it does not ship with). Structure stays declarative
and simple — one sensor, one expected reading, one deadline — explicitly NOT a rule language,
which would re-implement the policy engine inside verification. (D2) On `contradicted` the
protocol REPORTS and does not act: a first-class `action_contradicted` chain entry with
expected/observed/sensor/independence, no auto-retry (retrying can worsen an unseen physical
state) and no auto-escalation (a discrepancy is not an unsatisfiable emergency). The response
is deployment policy — consistent with the already-recorded no-automatic-compensation
position. (D3) Opt-in throughout; `verification` is a field SEPARATE from `success` (two
different questions: "accepted?" vs "did the effect happen?"); four states including
`unverifiable`, distinct from `contradicted` — the world did not disagree, we could not look.
Independence is graded (`independent_device` / `same_device`) so an auditor knows what
"verified" is worth.

**Two real bugs found and fixed during the work, both worth recording:**
1. A stray `@dataclass` decorator landed on the new `VerificationStatus` enum (an insertion
   split a decorator from the class it decorated). Dataclass then generated an `__eq__` that
   compared zero fields — so EVERY status compared equal to every other, and the type became
   unhashable. `contradicted == verified` was `True`. This is the exact class of silent
   failure the project most fears: the feature's core distinction, broken invisibly.
2. `pytest.ini` had no `asyncio_mode`, so pytest-asyncio in strict mode counted coroutine
   tests as passed WITHOUT RUNNING THEM. Every async test in the suite was a false green
   (6 tests across 3 files). Fixed with `asyncio_mode = auto`; all now genuinely execute.
Bug 1 was only exposed BECAUSE bug 2 was fixed and the tests started actually running — and
even then only surfaced when the "does the test fail with the bug reintroduced?" check kept
coming back green against expectation. The discipline caught what the green suite hid.

661/661. Spec §7.5.

**CONFIRMED in production 2026-07-21** (intent int-1784683484-82f1c0): a cross-device binding
declared in the intent context (WiZ bulb verified against the DHT22, deliberately impossible
expected value) resolved onto the actions, ran, and appended 4 first-class chain entries —
`action_unverifiable | wiz-living2-01 / turn_on | sensor: rpi-dht22-01:temperature |
expected: impossible-value | observed: None | independent_device`. Binding resolution,
verification execution, audit provenance, and the independence grading all work end to end.

**Named gap found by that drill — verifier reachability.** The result was `unverifiable`
rather than `contradicted` because verification reads the verifying sensor via
`adapter.get_state`, and the DHT22 arrives over a PUSH-ONLY path whose adapter does not
implement it (mqtt.py has no get_state; ble/ha/matter/mavlink/shelly/wiz do). So a push-only
sensor cannot serve as a verifier today: the hub cannot poll it on demand, and correctly
refuses to claim anything it could not observe. Possible follow-up (NOT decided — it is a
design question worth the panel): fall back to the hub's last-known reading for push-only
sensors, bounded by a freshness window. That raises a real question — is a cached reading
"independent observation"? — which is exactly the kind of thing to decide deliberately
rather than default into.


---

# HORIZON — raised, understood, not yet scheduled

Items that are real but deliberately not in flight. Recorded here because they were
previously "tracked" only inside a meeting document outside the repo — something declared
that nobody verified, which is the exact pattern this project keeps catching elsewhere. An
item here is not a promise; it is a decision to postpone WITH the reason attached, so a
future session inherits the thinking instead of rediscovering it.

## H1 — Two concurrent same-rank emergencies on one device
*Raised by Benítez (panel 2026-07-21). Also recorded in spec §3.1 as a known open edge.*
The claim FSM arbitrates strictly-lower rank (I1) and same-rank conflicts are resolved
pre-dispatch (§2) — but two emergencies DISPATCHED concurrently were never seen together
pre-dispatch, so they do not arbitrate against each other. The device takes both writes; the
last write is final. Rare in a home, less rare in a hospital or plant with multiple alarm
sources. Closing it means first deciding what "correct" even means when two equally urgent
truths target one device — a modeling question before a coding one.

## H2 — Verification via push-only sensors
*Found by the 2026-07-21 verify_with drill.*
Verification reads the verifier through `adapter.get_state`; adapters that only PUSH (mqtt,
and the GPIO path) cannot be polled on demand, so a push-only sensor cannot serve as a
verifier and the hub honestly returns `unverifiable`. A freshness-bounded fallback to the
hub's last-known reading would close it — but first someone must answer whether a CACHED
reading is "independent observation" at all, and what freshness bound keeps that honest.
A panel question, not a default to slide into.

## H3 — Online audit archiving (without stopping the hub)
*Raised by Sosa (panel 2026-07-21).*
`audit-archive` requires the hub stopped: fine at 28k entries, wrong at millions. Not a bug —
a named scaling limit. Closing it means safe concurrent segmentation under a single SQLite
writer.

## H4 — Lightweight heartbeat for TLS-incapable hardware
*Raised by Kim (panel 2026-07-21).*
`POST /v1/heartbeat` requires bearer auth over mTLS. An 8-bit MCU with 32KB of RAM cannot do
TLS, so today's hardware floor is "TLS-capable" — which excludes the cheapest tier of IoT.
The options (a pre-signed token over UDP, an aggregating gateway) all trade security for
reach; that trade should be chosen deliberately, not defaulted into.

## H5 — Third-party certification / public registry
*Raised by Nakamura (panel 2026-07-21).*
Certification is self-administered: an implementer runs certify.py and signs the report with
their own key. Cryptographically sound, but it attests "I ran the tests", not "an independent
authority verified me". A public registry of certified implementations, or a neutral
certifying party, is the v1.0-scale answer.

## H6 — End-user interface (FamilyOS)
*Raised by Ferreyra (panel 2026-07-21).*
Everything reachable today is curl, tokens and JSON. The protocol layer is the right place to
have spent the effort, but the stated destination — a private generational AI for the home —
has no front door yet. Out of scope for the protocol; in scope for what the protocol is for.

## H7 — Second independent implementation + language-independent wire format
*Standing item, predates the panel.*
The strongest remaining threat to credibility: one implementation in one language is a
program, not a protocol. A genuinely independent second implementation — ideally not in
Python, ideally not by the same author — is what would demonstrate that the specification is
actually a specification.


## LOOP-MIGRATION — get_event_loop() retirement, and a dead security alert it uncovered — CONFIRMED 2026-07-22 · effort S
Started as hygiene: `asyncio.get_event_loop()` is deprecated since Python 3.10 and scheduled
to raise when no loop is running. Classified all 9 call sites with AST (not grep): 6 sat
inside `async def` (harmless today, `get_running_loop()` is simply the correct call) and 3
sat in sync functions, where the deprecation actually bites. All migrated; a structural test
(AST) now fails if any `get_event_loop()` CALL reappears in the package or the server. The
long-standing DeprecationWarning on every test run is gone.

**What it uncovered — a security alert that had never fired.** `register_device` raises an
`alert_anomaly` intent when a device's capabilities change WITHOUT a firmware version bump
("may indicate compromise"). It called `self.execute_intent(alert_intent)` — but
`execute_intent` requires an `executor` argument. Every single invocation raised TypeError,
and the whole block sat inside `except Exception: pass`. The anomaly was always written to
the audit chain, so the evidence existed; the alert dispatch was simply dead, silently, for
as long as the code has been there. This is POL-2's lesson verbatim: a bare except hiding a
broken path. It only surfaced because removing the silent `pass` let the TypeError speak.

**Fix.** `DoSyncHub.default_executor` (wired by the server to the fully-wrapped executor, so
hub-initiated intents run through the SAME arbitration and auditing as any other intent —
not a side channel). When it is absent the hub logs that the alert was NOT dispatched
instead of failing quietly; when present, the alert genuinely executes. Failures in the
detached task are reported via a done-callback rather than vanishing. Registration is still
never blocked — best-effort stayed best-effort, it just stopped being silent.

667/667. Pinned by tests for: dispatch with and without a running loop, failure reported not
swallowed, missing-executor reported, the positive dispatch path that was dead, and the AST
guard. Verified to fail with each bug reintroduced.

**CONFIRMED in production 2026-07-22.** A probe device was registered, then re-registered
with a DIFFERENT actuator set under the SAME firmware version. The audit chain shows the
causal pair, in order: `device_capability_anomaly | anomaly-probe-01 | Capabilities changed
without firmware version change...` immediately followed by `intent_executed | alert_anomaly`.
The journal confirms execution end to end — `Executing intent: alert_anomaly [alert]` then
`Intent 'alert_anomaly' resolved to 7 actions across 7 devices` — and no `NOT dispatched`
line appears, so default_executor is wired. That second entry is an event this hub had never
produced before: the alert fired for the first time since the code was written.
## PACKAGING — `pip install dosync` — SHIPPED 2026-07-22 · effort M
The audit's unanimous first recommendation, and the cheapest one: the project was not
installable. No `pyproject.toml`, nothing on PyPI. Evaluating DoSync meant cloning a repo,
resolving dependencies by hand and setting PYTHONPATH — the largest friction sitting at the
very first step, exactly where an evaluator decides whether it is worth their time.

Delivered: `pyproject.toml` (name `dosync`, verified from PyPI as available), core deps
mirroring the audited floors in requirements.txt, optional extras per adapter so a core
install never pulls libraries for hardware the user does not own, and three console scripts —
`dosync-hub`, `dosync-manage`, `dosync-certify`.

For those scripts to exist after an install, the application had to live inside the package:
`server.py`, `manage.py` and `certify.py` moved to `dosync/`. The repo-root names remain as
module ALIASES (`sys.modules[__name__] = _impl`) rather than partial re-exports, because
several tests and repo scripts import symbols from them and one test mutates
`server.executor` — a re-export would have created a second module object whose mutations the
application never sees, which is the silent-divergence class this project refuses. So
`uvicorn server:app`, existing systemd units, `import server` and `python3 manage.py …` all
keep working unchanged.

Found while moving: two structural tests read implementation source BY PATH and were happily
reading the shims instead; repointed at the package.

Validated the way that actually matters — built the wheel and installed it into a clean
venv, as a third party would: `import dosync` works, the three console scripts exist,
`dosync-hub` serves `/v1/status` (DoSync Hub 0.4.0, protocol dosync/0.4), a device registers
(200), an intent is accepted, and the audit chain records it. 667/667.

Also: the Dockerfile now builds and installs the wheel instead of copying loose scripts (the
image runs exactly what a user's install produces), the README quickstart leads with
`pip install dosync` and a five-minute no-hardware walkthrough ending in the explain and
audit endpoints, badges corrected (certification said 44/44; it is 52/52), and a CHANGELOG
exists for the first time.

**Not done here, deliberately:** publishing to PyPI is the author's action (it needs an
account and an API token). The package is built and verified; the upload is one command.

## PACKAGING-FIXES — Three bugs the first install exposed — SHIPPED 2026-07-22 · effort S
Publishing to TestPyPI and installing the result as a stranger would is itself a test, and it
failed three things nothing else had:

1. **Docker data loss.** `Dockerfile` and `docker-compose.yml` set `DOSYNC_DB_PATH`; the hub
   reads `DOSYNC_DB`. No error, no warning — the database was written inside the image rather
   than the mounted `/data` volume, so every `docker compose down` silently destroyed the
   audit chain. The tamper-evident record, the project's central claim, did not survive a
   container restart in the shipped deployment. Compose files corrected; the hub accepts the
   old name as a deprecated alias WITH a warning, so a deployment already carrying it keeps
   its data instead of quietly falling back to the default path.
2. **Version declared three ways.** `dosync/__init__.py` said 0.1.0, `server.py` hardcoded
   0.4.0 in four places, `pyproject.toml` had its own copy. `import dosync; __version__`
   reported a number three releases stale. `__init__.py` is now the single source.
3. **The startup log lied about the port**, always announcing 47200. It now reports the real
   port and the database path (an installed `dosync-hub` defaults to the CURRENT DIRECTORY,
   which surprised even the author during the install test).

Pinned by `tests/test_deployment_env_contract.py` (7 tests), including a structural check
that every `DOSYNC_*` variable set in a deployment file is actually read somewhere in the
package — the test that would have caught #1 the day it was introduced. Verified to fail with
the old variable name restored. 674/674.

Version bumped to **0.4.1**: 0.4.0 was consumed on TestPyPI and carried the data-loss bug.

## DIRECT-ACTION-GOVERNANCE — Closing a bypass in the project's own central claim — SHIPPED 2026-07-25 · effort M
Found by auditing the five differentiators the project advertises AGAINST THE CODE instead
of trusting them. Two did not survive:

`POST /v1/device/action` called `executor.execute()` directly — no policy evaluation, no
audit entry. Demonstrated empirically: a lock was actuated (`unlock → status: unlocked`,
HTTP 200) and the chain recorded **nothing**. So "policies the AI cannot escape" was false
(the AI calls here instead of firing an intent) and "a tamper-evident record of what the
system did" was incomplete (this path did nothing to the record). Worse, the MCP server's
device-control tool uses this endpoint — including a branch that commands EVERY light at
once — so the bypass belonged to the agent, not to an operator.

DESIGN-PRINCIPLES §"On adapter-side fallback" already listed exactly these three
consequences (broken chain, unevaluated safety constraints, actions reduced to commands)
when rejecting adapter autonomy in the v11 review. The perimeter was closed and the same
hole shipped in the core.

Fix: a direct action is now a first-class protocol operation, not an escape hatch. It is
evaluated by the policy engine under the reserved intent class `direct_control` (so a
deployment writes `"intent_classes": ["direct_control"]` and binds this path like any
other), blocked actions return 403 naming the deciding policy, and EVERY outcome appends to
the chain — `direct_action_executed` or `direct_action_blocked`, both tagged
`source: direct_action_endpoint` so an auditor can separate operator actions from decisions
the system made. Urgency defaults to `info`: a direct action has no goal to infer urgency
from, and `info` never triggers emergency bypasses.

Validated live: excluded device → 403 + `direct_action_blocked`; permitted device → 200 +
`direct_action_executed`; chain verifies. Certification S12 still passes. 681/681, and the
tests fail with either the audit or the policy step removed — including a structural test,
because the original defect was a MISSING step, which no assertion on a passing request
would have caught.

**Honest scope.** This closes the policy/audit bypass. It does NOT make the chain resistant
to truncation or to full rewrite by an attacker with write access to the database — those
are inherent limits of a hash chain with no external anchor, and remain true. See HORIZON.

## AUDIT-CHAIN-INTEGRITY — Truncation detection, signed checkpoints, honest threat model — SHIPPED 2026-07-25 · effort M
Second finding of the strengths audit. The chain was tested against three attacks instead of
being trusted: altering an entry was caught; **truncating the tail was not**, and **rewriting
the chain wholesale was not**. The first is fixable, the second is fixable only by leaving
the machine, and both were being described by the single word "tamper-evident".

**Layer 1 (existing).** Hash links catch modification and insertion.

**Layer 2 — sequence + head record.** Entries now carry a monotonic `seq` inside the hashed
content, and the latest `(seq, hash)` is written to `audit_meta` — a different table — as
entries are appended. `audit-verify` compares the chain's actual head against it and reports
`MISMATCH ✗ — entries removed from the end`. Catches accidental deletion, truncated restores,
buggy code, and any compromise reaching the log but not the metadata. Does NOT stop an
attacker who writes to both; said so rather than implied.

**Layer 3 — signed exportable checkpoint.** `db audit-checkpoint` emits an Ed25519-signed
statement of the head, to be stored OFF the hub; `audit-verify --checkpoint FILE` proves the
chain still contains that history. Demonstrated end to end: after rewriting every entry,
recomputing all hashes AND updating the head record, local verification still reported
`Chain valid: yes ✓ / Head record: matches ✓` — and the exported checkpoint reported
`attested head NOT PRESENT — this history was rewritten or replaced`. That contrast is the
whole argument for the layer.

**Backward compatible.** Chains written before sequence numbers have none; verification
checks continuity only where the field exists, so the reference deployment's live chain and
its 26k-entry archived segment keep verifying across the upgrade.

**Two bugs found while building it.** (1) A test for sequence gaps edited `seq` on a sealed
entry, so it passed on the broken HASH rather than the gap — tautological, the v9 failure
mode again; rebuilt to construct valid hashes with a missing number. (2) Restoring the
sequence from the last entry broke after archiving: the `audit_archived` marker is written by
direct SQL and carries no `seq`, so the fallback used the row count, which after archiving is
far below the numbers survivors already hold — the series wound backwards and the next append
produced a chain that failed its own verification. Now the highest number present is used.

`docs/AUDIT-THREAT-MODEL.md` states the attacker model and the verification matrix,
including the rows that read "not detected" — a documented limit that is not tested is the
kind of claim this project exists to avoid. Spec §7.6. 695/695.

## AUDIT-INTEGRITY-PANEL-FIXES — Blockers found reviewing the solution before applying it — SHIPPED 2026-07-25
The chain-integrity work was submitted to the panel BEFORE being applied, and the panel
refused it: "the design is correct, the implementation is not ready". What exposed the
defects was not an attack but the LEGITIMATE operation — archiving after taking a
checkpoint made verification report tampering twice and exit non-zero.

**B1/B2/B3 — false alarms on a documented operation.** The root cause was one wrong
assumption in three places: that the chain only ever grows. Archiving shortens it.
- The head mark was compared to the tail by EQUALITY. It is now a HIGH-WATER MARK: the
  chain must still CONTAIN the marked entry with the marked hash. Growing past it is
  healthy, the marked entry missing below the chain's range means it was archived
  (informational), and the chain no longer reaching it means truncation (failure).
- `audit-archive` now advances the mark after appending its marker.
- Checkpoint verification accounts for entries archived since, instead of comparing the
  attested head's position against a stale entry count.
- Consequently the exit code is 0 after a legitimate archive and non-zero for both real
  attacks. Paredes' argument for treating this as blocking rather than cosmetic: a control
  that cries wolf during normal operation teaches operators to wave through the real alarm.

**R1 — head writes are batched** (`DOSYNC_AUDIT_HEAD_EVERY`, default 25, flushed at
shutdown and before log-rewriting operations). Writing it on every append cost 57% per
entry, which lands during an emergency when one intent produces dozens of entries on a Pi.
Batching is only safe BECAUSE of the high-water-mark semantics: a mark lagging behind means
the chain grew, which is never an attack.

**R2–R5** — a matrix row for events since the last checkpoint (with the note that checkpoint
frequency IS the editable window), a compliance runbook with a systemd timer that exports
off-host, and the `audit-verify` behavior change called out in the CHANGELOG rather than
buried among features.

**A test that did not test what it claimed.** The first attempt at pinning B1 asserted the
absence of a false alarm — but the high-water-mark change alone already guarantees that, so
removing the archive-side fix did not fail it. Rewritten to assert what the archive update
actually buys: the mark ADVANCES, so a truncation after an archive is still caught. Verified
to fail when the update is removed. Same lesson as the v9 tautology and the sequence-gap
test: assert the mechanism, not a symptom another mechanism also prevents.

702/702. Live: legitimate archive → exit 0, clean; truncation → `TRUNCATED ✗`, exit 1;
full rewrite with the head fixed up → local checks pass, exported checkpoint reports
`attested head NOT PRESENT`, exit 1.

## CHECKPOINT-EVIDENCE-PROTECTION — Two ways the runbook destroyed its own evidence — SHIPPED 2026-07-25
Both found by watching the reference deployment actually USE the feature, not by review.

1. **Same-day checkpoints overwrote each other.** The suggested filename used date-only
   granularity. Two checkpoints taken the same day — one before archiving, one after —
   collapsed into one, and the PRE-archive checkpoint, attesting to 16,223 entries, was
   lost. Older checkpoints are the valuable ones: each covers a longer stretch of history,
   so the one a careless filename clobbers narrows an attacker's window most.
2. **The runbook's systemd unit used `%i`** — systemd's INSTANCE NAME specifier, valid only
   in template units. In a plain unit it expands to nothing, so every daily run would have
   written the same file and destroyed the previous day's evidence silently. A compliance
   runbook that quietly overwrites its own evidence is worse than none: it produces
   confidence without the artifact.

Fixed: `audit-checkpoint` REFUSES to overwrite (`--force` is the deliberate escape hatch),
and the runbook derives a unique UTC timestamp through a shell with `%%` escaping.

**And the newly-added principle failed on its first outing.** The structural test written to
pin #2 scanned lines beginning with `ExecStart` — but the filename sits on a CONTINUATION
line, so reintroducing `%i` did not fail it. That is exactly "assert the mechanism, not a
symptom", committed in the same session the rule was added to DESIGN-PRINCIPLES. Rewritten
to scan whole `ini` blocks; both defects now verified to fail when reintroduced. The rule
works — it just has to be applied to the test one is writing at that moment, which is the
hard part.

## CHECKPOINT-BY-DEFAULT — The hub generates checkpoints itself — SHIPPED 2026-07-25
Raised by the operator, and correctly: if a checkpoint is what makes the chain's central
guarantee real, leaving the schedule entirely to each deployment means most deployments will
not have it. The earlier position — "frequency is a risk trade-off, so the protocol takes no
position" — was **inconsistent with this project's own precedent**: the spec already assigns
defaults to comparable risk parameters (`DOSYNC_UNREACHABLE_TTL` 1800s,
`DOSYNC_INTENT_TIMEOUT` 5000/10000ms, `IntentRateLimitPolicy` 60/min). "It depends on the
deployment" was not a reason; it was an inconsistency.

Two things had been conflated. **Generating** a checkpoint is entirely within the hub's
power. **Exporting** it is the one thing the hub cannot do — and that is precisely what
makes the copy meaningful. Separated:

- The hub now runs a checkpoint scheduler by default: `DOSYNC_CHECKPOINT_INTERVAL` (86400s,
  daily), `DOSYNC_CHECKPOINT_DIR` (`checkpoints/`), `0` to disable as a deliberate choice.
  Filenames are UTC-unique per run.
- `/v1/status` reports `checkpoint_age_s`, so a scheduler that has quietly stopped is
  visible to monitoring rather than discovered during an audit.
- The document builder moved to `audit_backup.build_checkpoint`, shared by the CLI and the
  scheduler — a checkpoint whose meaning depended on which code path wrote it would be
  worthless as evidence.
- Shutdown flushes the head mark, since it is written in batches and a stop between batches
  would leave it behind the chain.
- Spec §7.6 states the default with SHOULD language and the rationale: the interval IS the
  window an attacker can still edit, and daily bounds it at one day for ~1.4s of CPU
  (measured — Ed25519 signing in pure Python; the same cost makes per-minute checkpoints
  wasteful at ~2000s/day).

Export remains the deployment's, stated plainly rather than implied.

**And the principle failed a fifth time, on the change that motivated it.** Every test
written for the scheduler passed `interval=1` explicitly, so hardcoding the default to `0`
left all of them green — the tests proved the scheduler CAN run, never that it runs BY
DEFAULT, which was the entire point of the change. Fixed with a test that calls the
scheduler with no configuration and asserts the interval it actually uses. Worth recording
plainly: the rule is easy to state and hard to apply to the test one is writing at that
moment, because the passing test always feels like evidence. 714/714.

## CHECKPOINT-EXPORT-STANDARD — Where checkpoints go is a protocol setting — SHIPPED 2026-07-25
Operator's follow-up to CHECKPOINT-BY-DEFAULT, and the same argument applied one level out:
if generating checkpoints deserves a default, then WHERE they go deserves a standard
configuration point rather than a sentence in a runbook telling each operator to invent one.

The destination cannot have a universal default — no path is right everywhere. But its
ABSENCE is not neutral, and that is the part the protocol can standardise:
`DOSYNC_CHECKPOINT_EXPORT_DIR` is now the configuration point, the hub copies every
checkpoint there, and leaving it unset produces a warning at STARTUP (not only when the
first checkpoint lands a day later) and on every checkpoint, plus
`checkpoint_export: not_configured` in `/v1/status`. A hub quietly producing artifacts
nobody collects is the exact failure this layer exists to prevent.

Export failures are errors, not shrugs: a silent failure leaves an operator believing they
hold evidence they do not have, which is worse than not exporting at all. The local
checkpoint survives a failed export.

Spec §7.6 states the gradation instead of implying that any export is equivalent: unset
protects nothing beyond local corruption; a directory the hub can write to survives loss of
the database and may benefit from remote snapshots, but root here can usually delete there;
only a pull-based transfer where the hub holds no credentials makes "the hub cannot reach
it" literally true. 718/718.
