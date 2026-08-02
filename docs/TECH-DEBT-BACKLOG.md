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

## H1 — Two concurrent same-rank emergencies — CLOSED 2026-07-31
*Raised by Benítez (panel 2026-07-21).*
Reproduced first: `unlock` and `lock` on one door, both emergency urgency, both returning
success, the final state decided by arrival order, and **zero audit events**. The arbiter
ranks urgencies so emergency-over-routine was solved; same-rank was deferred to "the
PolicyEngine, pre-dispatch", which cannot help — by the time both reach the arbiter both have
already passed policy.

Closed by making it visible rather than by picking a winner. Blocking one would require
inventing a priority the protocol does not have, and dropping the later action is as likely
to be wrong as keeping it. Two emergency plans contending for one device is a fact about the
DEPLOYMENT — usually two intents that should have been one — so it is now reported as
`concurrent_same_rank_claims` with the note that the later action determines the final state.

Two subtleties, both found by testing rather than by reasoning: the count has to happen
BEFORE the per-device lock (the lock is what serialises them, so from inside, the first has
already left and the contention is invisible), and it is released in a `finally` outside the
lock covering the supersede return too, because a counter decremented on one path only would
eventually report contention that is not happening — a false alarm in an audit trail is worse
than no alarm.

## H2 — Verification from pushed readings — CLOSED 2026-08-01
*Found by the 2026-07-21 verify_with drill; the item explicitly asked for a panel before an
implementation, and that was the right instinct.*

The question was whether a cached reading is independent observation. The panel's answer was
that it is not the same thing: a reading the hub asks for after acting is causally posterior
to the action, while one the device happened to send may predate it — and then it describes
the world before we did anything. Arriving AFTER dispatch and recently, it is evidence:
weaker, because we did not ask for it, but evidence.

**A finding reordered the work.** `update_state()` stored values with no arrival time at all,
so the freshness bound the item proposed was unimplementable — not by design, but because the
data was not being retained. Timestamps first, policy second.

Implemented per the panel's five decisions:
1. Readings are stamped on arrival, in a map parallel to the state cache so nothing that
   reads state sees a stamp beside a sensor.
2. **The window is measured against the ACTION, not the clock.** `DeviceAction.dispatched_at`
   is set by the executor at dispatch; a reading older than that is rejected however fresh.
3. Opt-in per binding (`accept_cached_within_s`), with **no default**: no single value is
   right for a thermometer and a door sensor at once, and silently accepting stale readings
   as `verified` is exactly what this protocol refuses.
4. `VerificationResult.evidence` records `polled` or `pushed`, because `verified` cannot mean
   two different things (Torres) — plus `observed_at`, since when the evidence arrived is
   part of what the evidence is.
5. New status `no_change_reported` for a change-reporting sensor that stayed silent (Kim):
   healthy and quiet is not the same as absent, and conflating them sends an operator after a
   working sensor.

**A test that passed for the wrong reason**, caught by deleting the code it protected: the
"without opt-in" case used a reading that predated the action, so the timing guard rejected
it anyway and removing the opt-in check left the test green. It proved the timing check
worked, not the opt-in. Now the reading arrives after dispatch and the test asserts that a
perfectly good pushed reading is still ignored. Fourth instance of this pattern; the rule in
DESIGN-PRINCIPLES exists for a reason and still has to be applied consciously each time.

Spec §7.5.1. 831/831, with three separate reintroductions verified to fail.

## H3 — Online audit archiving — CLOSED 2026-07-25
*Raised by Sosa (panel 2026-07-21).*
The constraint turned out to be an artifact of the tool, not of archiving: `manage.py db
audit-archive` needs the hub stopped because it is a SECOND process contending for a
single-writer database. In-process there is no second writer, so the hub now archives
ITSELF while running (`DOSYNC_AUDIT_MAX_LIVE`, default 10000), refusing on a chain that does
not verify. The CLI keeps its warning because the CLI still is a second process.

The lesson worth keeping: a limitation attributed to a system was a limitation of the one
way we happened to invoke it.

## H4 — Heartbeats without TLS — CLOSED 2026-08-01
*Raised by Kim (panel 2026-07-21); designed by the panel 2026-08-01 before any code.*

The item said every option trades security for reach and that the trade must be chosen, not
defaulted into. The panel found the trade is narrower than stated: **authenticity does not
require TLS.** TLS provides channel confidentiality and channel authenticity; a heartbeat
needs MESSAGE authenticity, and an HMAC-SHA256 fits comfortably on an 8-bit MCU.

Why that is acceptable here and would not be for an action (Sosa and Benítez): a heartbeat is
positive signal only — it marks a device reachable and never marks one unreachable — so a
forged one cannot switch anything on. The attack worth preventing is SUPPRESSION: replay a
captured heartbeat and a burnt-out smoke sensor reports healthy forever, blinding failure
detection exactly when it matters. Replay protection is therefore not optional.

**Nothing new was needed for credentials.** The project already had `device_tokens`, hashed
storage and `POST /v1/devices/provision`. The HMAC key is `sha256(device_token)`: the device
derives it, the hub already holds it, and the deliberate decision not to store tokens
recoverably stays intact.

Implemented per the panel's six decisions: HMAC over the existing token; mandatory replay
protection (narrow window plus single-use signatures, with the seen-set pruned rather than
grown); opt-in via `DOSYNC_LIGHTWEIGHT_HEARTBEAT`, off by default because a hub that starts
accepting unencrypted messages without the operator choosing it is wrong even when it is safe
(Ferreyra); the device marked `report_channel: signed_plaintext` and that field exposed in
health, since two devices in different security positions must not look identical (Aguirre);
a row in the threat model rather than a footnote; and plain HTTP rather than UDP, because the
real floor of the market is an ESP8266 that speaks HTTP and not TLS (Torres, Kim).

Clock errors are reported as clock errors. Cheap hardware has no NTP and drifts, and "your
clock is off by 400 seconds" is a different problem from "your signature is wrong" — both
look like "rejected" from outside.

**Found while building it:** `mark_channel` wrote the field and `snapshot()` did not return
it, so the marking existed and was invisible — the same shape as the dashboard that shipped
outside the package. And the H8 configuration-reference test caught the new variable being
undocumented within minutes of it existing, which is what that test is for.

848/848.

## H5 — Third-party certification / public registry
*Raised by Nakamura (panel 2026-07-21).*
Certification is self-administered: an implementer runs certify.py and signs the report with
their own key. Cryptographically sound, but it attests "I ran the tests", not "an independent
authority verified me". A public registry of certified implementations, or a neutral
certifying party, is the v1.0-scale answer.

## H6 — Usable by someone who is not a developer
*Raised by Ferreyra (panel 2026-07-21). Sharpened by the operator 2026-07-26: the target is
"a common user, someone with basic knowledge, at most Home Assistant experience" — and
DoSync is not only for home automation, so the same bar applies to a small shop or a
workshop.*

**Closed since it was raised** (2026-07-26, all found by trying to USE the thing):
- `pip install dosync` exists; the hub runs with one command.
- The dashboard SHIPS with the package. It never had before — it sat at the repo root, so no
  install ever carried it, and the packaging move broke its path in clones too.
- It no longer lies about what the project is: the launcher renders the deployment's own
  intent classes instead of eight hardcoded home scenarios, and the version comes from the
  API instead of saying v0.1 for three releases.
- It works over TLS. It hardcoded `http://`, so on any HTTPS hub the browser blocked it
  silently — the hub nagged operators to enable TLS and then its own UI stopped working.
- Access is manageable from the browser: choose a password, or turn authentication off, with
  the change recorded in the audit chain.
- The warning Chrome shows on a self-signed certificate is explained, with per-platform
  instructions — previously the hub said "run setup_pki.sh" and abandoned the operator at
  the consequence.

**Still open, and this is the substance of it:**
- **Registering a device means POSTing a JSON manifest with curl.** This is the single
  biggest wall. Home Assistant is tolerated by non-developers largely because it DISCOVERS
  devices; DoSync makes you describe each one by hand.
- **No onboarding.** A fresh hub has zero devices and no path from there that does not
  involve reading the spec.
- **Configuration is environment variables and `systemctl edit`** for everything except
  access.
- **No packaged install for a non-technical machine** — no OS image, no add-on, no installer.

The pattern worth carrying: none of what got fixed was a redesign. Each was a place where the
system knew the answer and did not say it, or worked in the author's setup and nowhere else.
The remaining items are different in kind — they are product, not polish, and discovery is
the one that decides whether the rest matters.

## H8 — Configuration in one place — CLOSED 2026-07-31
*Noticed 2026-07-31 while auditing what is open.*
48 `DOSYNC_*` settings, each documented where it was introduced and nowhere together. The
entry asking for this mistyped one while being written, which was the argument in miniature.

`docs/CONFIGURATION.md` is now GENERATED from the source
(`python3 -m dosync.config_reference --write`) and a test fails when it drifts — a
hand-maintained table would have been the fifth thing here to hold one fact in two places.
Grouped by what an operator is trying to do rather than alphabetically, because they arrive
with a question and not with a variable name.

Found while building it: the generator scanned its own docstring — which shows the pattern it
searches for — and reported `DOSYNC_X` as a real setting. A generator that hallucinates is
worse than a hand-written table.

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

## CHECKPOINT-EXPORT-PULL — The warning fired loudest at the best configuration — SHIPPED 2026-07-25
Raised by the operator asking a plain question: "I don't know where to configure the
export, can I leave it as is?" — which exposed that the answer for HIS deployment was yes,
and that the warning was wrong about it.

The strongest arrangement in the spec's own table is PULL: something outside fetches
checkpoints and the hub holds no credentials to it. In that arrangement
`DOSYNC_CHECKPOINT_EXPORT_DIR` is correctly unset — pointing it at a mount the hub can
write to would be a DOWNGRADE. So the warning shipped one release earlier fired hardest at
the best possible setup, which is how operators learn to ignore warnings.

Added `DOSYNC_CHECKPOINT_EXPORT_EXTERNAL`: a declaration that collection happens elsewhere.
The hub then logs the arrangement instead of warning, and reports
`checkpoint_export: external`. With NEITHER setting the warning stands — the declaration is
a statement of fact, not a mute button.

Spec §7.6 now defines both settings and states why the second exists: a protocol that
cannot distinguish "nobody collects these" from "someone else collects these" will
mis-advise every deployment that got it right.

## CHECKPOINT-RESTART-STARVATION — A daily schedule that never fired — SHIPPED 2026-07-25
Found by pulling `cp-*.json` from the reference deployment after a day of work and getting
nothing back — the only file present was one created by hand earlier.

The scheduler slept BEFORE its first write. With a daily interval and a hub restarted for
each deploy, the 24-hour timer reset every time and never elapsed: **a hub that restarts
more often than the interval produced no evidence at all**, silently, while logging that
checkpoints were scheduled. The log said the right thing and nothing happened — the failure
mode this project keeps finding, this time in a feature written to prevent exactly that.

Fixed: on start the hub writes immediately if a checkpoint is OVERDUE, then settles into the
interval. "Overdue" comes from a timestamp persisted in `audit_meta`, not from files on
disk, because in a pull arrangement the collector removes them once fetched and an empty
directory would otherwise read as "never checkpointed" and produce one on every restart.

Pinned by three tests: a short-lived hub still produces a checkpoint, repeated restarts
produce only one, and deleting the files does not fake a missed interval. 723/723.

Worth stating as a general lesson: a periodic task that sleeps before its first action has a
hidden requirement — uptime longer than its period. Deployments restart for updates, power
and configuration. An interval that only survives uninterrupted uptime is not an interval.

## CHECKPOINT-GITIGNORE — Evidence was one `git add -A` from a public repo — SHIPPED 2026-07-25
`DOSYNC_CHECKPOINT_DIR` defaults to `checkpoints/`, relative to the working directory — so a
hub started from a clone writes its audit evidence INSIDE the repository, and `checkpoints/`
was not ignored. This project's own workflow runs `git add -A` on every patch, and the hub
has been started from the Mac clone during testing, so the two only had to coincide once.

Added to `.gitignore`, with the reasoning inline and a pointer to setting
`DOSYNC_CHECKPOINT_DIR` outside the clone. Not a secrets leak — a checkpoint holds hashes,
counts and a signature — but operational evidence accumulating in a public source repository
by accident is not a thing to discover later.

## ASSURANCE-PROFILE + AUTO-ARCHIVE — Lowering the bar, and a live bug found doing it — SHIPPED 2026-07-25
Three operator questions, one root: **who are you proving things to?**

*"Shouldn't this be automatable?"* — Yes, and the constraint was an artifact. `manage.py db
audit-archive` requires the hub stopped because it is a SECOND process contending for a
single-writer database; in-process there is no second writer. The hub now archives ITSELF
while running (`DOSYNC_AUDIT_MAX_LIVE`, default 10000, `0` disables), on the same timer as
checkpoints, refusing on a chain that does not verify. The reference deployment went from
2,000 to 16,258 live entries in five days — an unbounded chain is not something to leave to
someone's memory, least of all in a home or a factory where nobody watches entry counts.

*"Is it mandatory?"* — No, and the docs implied otherwise. `DOSYNC_ASSURANCE` defaults to
`standard` and gates the export nagging; `regulated` turns it on. Spec §7.6 now states
plainly that a home installation configuring none of this is conforming and loses nothing it
needed.

*"Are we setting the technical bar too high?"* — We were. A household starting the hub
received four alarming lines about an adversary who controls the host — which is to say,
about themselves. Unactionable warnings teach operators to ignore the actionable ones. Gone
by default.

**And a live bug in production, found by this work.** The `anchor_prev_hash` initialisation
ended up inside `flush_head()` instead of `__init__` when this file was rewritten after a
lost working copy. Every checkpoint write therefore reset an archived chain's anchor to
genesis IN MEMORY: `verify()` failed and `/v1/status` reported `audit_integrity: false` on a
chain that was perfectly intact — a false accusation, the exact failure mode the panel
blocked earlier for a different reason. The value is per-chain state; nothing recomputes it.
Pinned by a test that fails if any operation resets it. 728/728.

## CHECKPOINT-EXPORT-MODE — Status said "unknown" about a setting it could read — SHIPPED 2026-07-26
Spotted in production output: a hub with `DOSYNC_CHECKPOINT_EXPORT_EXTERNAL=true` reported
`checkpoint_export: unknown` right after a restart. The field was only assigned when a
checkpoint was WRITTEN, so between a restart and the next interval the hub claimed ignorance
about configuration sitting in its own environment — and a monitor watching that field was
blind for a whole interval after every restart, which is precisely when someone is looking.

Now derived from configuration at status time (`not_configured` / `configured` / `external`),
with the last attempt's OUTCOME kept separately as `checkpoint_export_last`. Two different
questions — where copies should go, and whether the most recent one got there — that were
being answered by one field. 730/730.

## DASHBOARD-SHIPPING — The only non-developer entry point was broken and unpackaged — SHIPPED 2026-07-26
The operator pushed back on H6 ("everything is curl and tokens") being mentioned and then
dropped. Checking before building found the gap was not what the horizon list said: a
936-line browser dashboard already existed and was served at `/`. It was simply unreachable.

Three faults, compounding:
1. **It sat at the repository root**, so `pip install dosync` never carried it — the one
   thing a non-developer can open was absent from every install the project ever produced.
2. **The packaging move broke it in clones too.** The handler resolves
   `Path(__file__).parent / "dashboard.html"`, and `__file__` became `dosync/server.py` when
   the application moved into the package. Introduced by this project's own packaging work.
3. **The fallback was `FileResponse.__new__(FileResponse)`** — an uninitialised object that
   raises AttributeError inside the framework. Someone opening the hub in a browser got a
   stack trace, which is the worst possible greeting for the one visitor who arrived without
   a terminal.

Fixed: `dashboard.html` moved into `dosync/`, declared as package-data, Dockerfile no longer
copies it separately, and a missing file now returns an honest page pointing at `/docs` and
`/api`. Verified the way it matters — built the wheel, installed it into a clean venv, ran
`dosync-hub`, and fetched `/`: 200, 28,433 bytes of HTML. A stranger who runs
`pip install dosync && dosync-hub` and opens a browser now sees something.

The package-data test initially passed with the declaration deleted, because the comment
above it also names the file — sixth instance of asserting a symptom rather than the
mechanism. Now it parses the declaration line. 733/733.

## DASHBOARD-DOMAIN-AGNOSTIC — The only visual artifact contradicted the project's central claim — SHIPPED 2026-07-26
Opening the freshly-shipped dashboard in a browser showed two things worth more than the fix:

**It displayed "Hub v0.1".** A FOURTH hardcoded version source, three releases stale, and the
only one a visitor ever sees. The project had just consolidated three disagreeing sources in
code; the one on screen was missed because nobody looked at the screen.

**The intent launcher was eight hardcoded home scenarios** — Good Morning, Bedtime, blinds
up, coffee on, laundry done — with zero calls to `/v1/intent-classes`. So the single visual
artifact of a protocol whose non-negotiable flag is *"this is not a smart-home project"*
presented itself as a smart-home app. Anyone evaluating DoSync for a plant, a hospital or a
care facility opened this page and saw a house. The positioning problem the integral audit
identified was not only in the README; it was rendered, in the product.

Now the launcher is built from what THIS deployment has registered, sorted with emergency
first (in an incident the button you need must not be the one you scroll to), and the
version comes from `/v1/status` like every other number on the page. A factory sees factory
intents. Nothing about a house survives in the markup.

Tests strip HTML comments before asserting, because the explanation above the grid names
those scenarios deliberately and a substring search passes on it — the same trap that made
the package-data test green a few hours earlier. 736/736.

**Not a closure of H6.** A browser page that renders the deployment is a real improvement
over one that lied about it, but registering a device still means POSTing a JSON manifest
with curl, and configuration is still environment variables. The gap the operator described
— "a common user, someone with basic knowledge, at most Home Assistant experience" — is
about installation and onboarding, and remains open.

## ACCESS-USABILITY — Two walls at the first screen, and an undocumented escape — SHIPPED 2026-07-26
The operator could not get into his own dashboard. Both reasons were real defects, and the
questions he asked afterwards exposed a third.

**The dashboard could not talk to a TLS hub at all.** It hardcoded
`const HUB = http://…:47200` and `ws://`. A browser blocks an HTTPS page from fetching
`http://` — mixed content — so the request never left the tab and the UI sat at
"disconnected" with no error anywhere. The hub warns at every single startup that TLS is not
configured, and the moment an operator complied, its own interface stopped working. It was
also broken on any port other than 47200. Now derived from `window.location`.

**Nothing told anyone where a token comes from.** The tooling has always existed
(`keys list/create/revoke/reset`); the dashboard mentioned it in zero words. And the obvious
guidance would have been wrong: keys are stored hashed and shown once, so a lost token
cannot be looked up.

**You could not choose your own.** `generate_key` always produced 43 random characters, so
the only way into the dashboard was a string nobody memorises — it ends up in a note, a
password manager, or lost, which is what happened here. `keys create --token` now accepts a
chosen value with a 12-character floor and a warning below 20, because a bearer token is
checked with no rate limit or lockout and is therefore guessed offline at full speed.

**And `DOSYNC_AUTH=false` appeared in ZERO documentation files.** It works, it has always
worked, and no user could discover it. For a hub on a home network behind a router with no
port forwarding, requiring a token protects against nobody already outside the house — the
right default for a clinic, an unnecessary obstacle for a workshop. Now documented in the
README beside the other two options, stated as a legitimate choice rather than a trap door.

741/741. This is the texture of H6: not a redesign, but the places where the system knows
the answer and does not say it.

## ACCESS-FROM-THE-BROWSER — Managing your own password without a shell — SHIPPED 2026-07-26
Operator's request, and correct: setting a password or turning authentication off required
`systemctl edit` and CLI commands — putting the obstacle in front of exactly the person
least able to clear it. Someone running DoSync at home, behind a router, for whom a token
guards against nobody already inside the house, had to learn systemd to say so.

New: `GET/POST /v1/auth/mode` and `POST /v1/auth/token`, plus a ⚙ control in the dashboard.
Set a passphrase, or switch the requirement off, from the browser.

Designed with the project's own habits rather than around them:
- **Precedence is stated, not emergent.** `DOSYNC_AUTH` in the environment WINS and the hub
  says so (409 with an explanation, and the dashboard explains before asking) — a click in a
  browser must not quietly override what a unit file declares. Otherwise the stored setting
  applies; failing both, authentication is ON. The alternative was a fifth value living in
  two places, which this project has already been bitten by four times.
- **Turning auth off is treated as the security act it is:** authenticated, explicitly
  confirmed, logged at WARNING, and appended to the tamper-evident chain as
  `auth_mode_changed`. "When did this hub become open, and who did it" now has an answer.
  The token VALUE never enters the chain — a chain is readable by whoever can read it.
- **`reset_keys` moved onto AuthManager.** `manage.py keys reset` opened its own sqlite
  connection to delete keys; a second caller would have meant the same deletion written
  twice in two files, free to drift.

Documented in the README (the three options, with running open presented as a legitimate
choice rather than a trap door) and normatively in spec §7.7.

**And the first version of these tests broke five unrelated ones.** The helper reloaded
`dosync.server` to rebuild auth state from the environment and never put it back, leaving
authentication switched on for everything that ran afterwards. Reloading a module in a test
is fine; not restoring it is not. Now a fixture with teardown. 747/747.

## TLS-BROWSER-WARNING — An instruction the project never finished — SHIPPED 2026-07-26
The hub warns at every startup: "TLS/PKI: NOT CONFIGURED — Run: bash setup_pki.sh". An
operator complies, opens the dashboard, and the browser says **"Not secure"** with `https`
struck through. Nothing in the README, the docs or the script explained why, what it means,
or how to finish — the project told people to do something and left them at the consequence.

Worse, the natural reading is the wrong one: the strike-through does not mean the connection
is unencrypted. It means no third party vouches for the certificate, because the signer is
the operator themselves. A public CA cannot issue for `192.168.x.x`, so every hub on a
private network lands here — this is not an edge case, it is the default outcome of
following the advice.

Documented in the README: what the warning does and does not mean, the two honest options
(accept it, or trust your own CA), per-platform commands for macOS, Linux and Windows, the
note that `certs/ca.crt` is the only file needed and holds no secret — and, importantly,
when NOT to click through, since the warning does exist for a real reason on networks one
does not control. The hub now says the same thing on the line right before announcing TLS is
active, which is roughly ninety seconds before the operator meets the warning.

748/748. Found by the operator saying "it works, but the crossed-out https bothers me" — a
question nobody had answered because nobody had asked.

## POSITIONING-REFRAME — Answering the comparison instead of avoiding it — SHIPPED 2026-07-26
Recommendation #3 of the integral audit, and the one with the highest return per hour: the
README led with *"the semantic layer between AI agents and physical devices"* and never
mentioned W3C Web of Things. That is the closest thing to a competitor — Thing Description
is a finished W3C Recommendation with Oracle, Siemens, Intel, Microsoft and Hitachi behind
it — so "semantic layer" is the ONE part of the pitch that is genuinely contested. An
informed evaluator makes that comparison regardless; the only choice was whether they made
it with our answer or without it.

Headline is now **"Governance and accountability for AI that acts on physical devices"**,
and a new section places DoSync against transport (Matter/MQTT), description (W3C WoT) and
agent protocols (MCP) — above them, not against them. On WoT specifically: a Thing
Description says a lock exposes a `lock` action and how to invoke it, which is the right way
to describe a device; what it cannot do is decide the lock should answer *"there is an
emergency"*, refuse because this deployment forbids it, arbitrate two intents wanting it at
once, or leave evidence that survives root access. MCP is named as a distribution channel,
which is what shipping an MCP server makes it.

The five differentiators are stated with the evidence that now exists behind each — and only
because it exists: two of them were FALSE when the panel first asserted them, and the
section says so about #2 rather than quietly presenting the fixed version.

Deliberately absent: any absolute security claim. Oracle's "unbreakable" was broken within
days and became a case study; for a protocol whose value is honesty — `unverifiable`,
`indeterminate`, a threat model with rows reading "not detected" — an absolute claim would
be self-refuting. The README says so outright.

Three tests guard it: the comparison is present, no absolute claims appear, and every local
link resolves. That last one caught a real defect immediately — the claim state machine was
cited as living in the protocol spec when it is in the consistency model, and a citation
that does not resolve is worse than none in front of an evaluator. The absolutes test also
failed on its first run, on the project's own disclaimer sentence: an assertion arguing with
itself. 751/751.

## DISCOVERY-SCAN-AND-ADOPT — H6 priority #1 — SHIPPED 2026-07-26
Panel session on H6 inverted the diagnosis. "Everything must be registered by hand" implied
discovery had to be built; measuring found the opposite. **The expensive half was already
done**: six of eight adapters know how to construct a manifest — knowing that a device is a
dimmable lamp and expressing that in DoSync's model is the hard, technology-specific work.
`discovery.py` (207 lines), `Discovery.run()`, `run_periodic()` and two endpoints all
existed. **The dashboard called none of them**, so a hub with no devices was a dead end whose
only exit was a hand-written JSON manifest — the same defect as the dashboard itself living
outside the package, one week later.

Two design decisions from the session, both implemented:

**Scanning does not register.** `GET /v1/discovery/scan` lists candidates and changes
nothing; new `POST /v1/discovery/adopt` registers one, with a name the operator chose. Torres'
argument for treating this as a correctness issue rather than a preference: in a protocol
whose central claim is accountability, devices appearing in the registry because they
answered a broadcast — approved by nobody — contradicts the premise. Twenty bulbs in a house
is convenient; twenty unapproved devices in a plant is not.

**The operator names them.** `wiz-a4c138` is what the bulb calls itself; "Kitchen light" is
what makes every later screen readable. Approval and naming are the same step because they
are the same decision.

`POST /v1/discovery/run` keeps auto-registering — legitimate for a scripted setup, where
invoking it IS the approval — but now appends `devices_auto_adopted` with
`approved_by_operator: false`, distinguishing it from a deliberate choice. "How did this
device get here" belongs to the same family as "who turned authentication off".

Adapters that cannot build a manifest from a scan say so and point at
`POST /v1/devices/register`, because per Aguirre discovery is an adapter capability and not
a protocol promise: a drone does not answer a UDP broadcast and a clinical device sits on a
proprietary bus. Manual registration is a first-class path, not a failure.

A scan that finds nothing says so explicitly — the hub being alive and reachable is itself
information, and silence leaves a user unable to tell it apart from a broken page.

758/758; name handling and audit both verified to fail when removed.

**Still open from the same session** (panel priorities 2–8): discovery as an adapter method
rather than a hardcoded `if adapter == "wiz"`, a guided first minute, device delete/rename
from the UI, explaining "intent" where it is used, policies from the interface, visible
intent outcomes, and a non-pip install path.

## H6-DEVICE-MANAGEMENT + MULTI-TRANSPORT-DISCOVERY — SHIPPED 2026-07-26
Operator's two objections, both correct, and the second exposed a limit nobody had named.

**"Unregistering and renaming must also work from the panel."** Right, and the contradiction
was immediate: the previous message told him to remove a device with curl, one message after
claiming the bar was lower. Renaming had no endpoint at all — fixing a name meant
re-registering the entire manifest, reconstructing every capability to change one string,
and a device adopted from a scan arrives called `wiz-a4c138`. Added `PATCH /v1/devices/{id}`
(presentation fields only: capabilities describe what a device CAN DO and come from the
device, so letting an operator edit them would let the registry drift from the hardware and
the resolver plan against a fiction), plus rename and remove controls on every card. Both
audited. Remove says plainly that the device keeps working and only DoSync stops knowing
about it — someone expecting the light to go off would otherwise think it failed.

**"Does this only work for WiFi? What about BLE, or radio?"** It did, and nothing said so.
Discovery meant UDP broadcast because the only implementation was WiZ's, living in a central
module behind `if adapter == "wiz"` — which quietly made an IP assumption in a protocol that
claims no such limit. Now `discover()`/`can_discover()` are optional methods on
`DoSyncAdapter`, each transport answering in its own terms, and **BLE implements it** over
Bluetooth radio with no broadcast address involved. A BLE candidate is deliberately
incomplete: an advertisement carries a name and an address, not capabilities — GATT says how
to write bytes, not what they mean — so it is offered for adoption with no actions and the
operator supplies them. Presenting a guess as a capability would be worse than admitting the
transport cannot tell us.

`GET /v1/discovery/scan` now asks every adapter and reports `searched` and `not_searchable`,
because "nothing found" means something different when Bluetooth was never scanned.

**Two defects found on the way.** `NotificationAdapter` did not inherit `DoSyncAdapter` — it
duck-typed with a matching `adapter_name` and `execute`, which worked until the base class
gained methods it silently lacked. A structural test now fails if any adapter stops
inheriting. And the new tests polluted the suite by popping `DOSYNC_DB` on teardown instead
of restoring its original value, sending later tests to an on-disk database — the same
failure as the auth fixture, through a different door.

An empty hub now names the Scan button instead of showing an empty list, which is panel
priority #3 arriving for free. 768/768.

## DISCOVERY-DEPENDENCIES — A circle in the packaging rule — SHIPPED 2026-07-26
The operator: "bleak should come by default in the standard; whether the user has Bluetooth
is a separate matter — otherwise the bar stays high." Correct, and for a sharper reason than
convenience.

The optional-extras rule — *do not pull in libraries for hardware you do not own* — is right
for CONTROL libraries: you install `dosync[wiz]` because you know you have WiZ bulbs.
**Discovery is how you find out what you have**, so its dependency is needed BEFORE the
knowledge that would justify installing it. Left optional, the failure is not friction but a
false belief: a user scans, sees nothing, and concludes DoSync does not support Bluetooth.
The costs are asymmetric — ~20 MB on a server that will never use it, against an invisible
capability for everyone else.

`bleak` moved to core dependencies. And the same circle existed one level up: the BLE adapter
was registered only with `DOSYNC_BLE_ENABLED=true`, so even WITH the library installed the
scan did not search Bluetooth. Verified by installing the wheel into a clean venv and
scanning: `searched: ['wiz (udp broadcast)']` — the library was there and unused. Now the
adapter registers whenever the library imports, with the variable inverted to opt out.

After the fix, a clean `pip install dosync` scans WiFi and Bluetooth radio with no
configuration: `searched: ['wiz (udp broadcast)', 'ble']`.

Version 0.4.2 — adding a core dependency changes what an install produces, which is a real
change for anyone who runs it. 771/771.

## SCAN-HONESTY + PEP668-INSTALL — Two walls the reference Pi found — SHIPPED 2026-07-27
Deploying the previous change to the reference hub exposed both immediately.

**The scan would have lied.** The Pi logged "BLEAdapter registered" on a host where `bleak`
had failed to install — the adapter registers fine because the library import is lazy — so
`can_discover()` answered True and the scan would have reported Bluetooth as SEARCHED when
it never touched the radio. That is precisely the false "nothing found" that reporting
searched transports exists to prevent, shipped inside the feature meant to prevent it.
Implementing `discover` is necessary and not sufficient; BLE now overrides `can_discover()`
to check that its library actually imports.

**`pip install dosync` fails on the target platform.** Raspberry Pi OS, Debian 12+ and
Ubuntu 23.04+ refuse system-wide pip installs (PEP 668) and answer with a wall of text about
externally-managed environments. The Raspberry Pi is the machine most likely to be running a
hub, and the project's own author hit this — an install instruction that fails on the target
platform is the first wall in front of exactly the user H6 is about.

The README now leads with `pipx install dosync`, which is the correct tool rather than a
workaround: DoSync is an application with commands you run, not a library you import, and
pipx gives it a private environment while keeping `dosync-hub` on PATH. The venv path is
documented for people writing Python against it, and `--break-system-packages` is named
along with why it is the option not to choose. The error string itself appears in the README
so that searching for it finds the answer.

772/772.

## ADAPTER-CLASSIFICATION — What ships, and who answers for it — SHIPPED 2026-07-27
Operator: "the adapters we write here, like wiz, should not be in the package — that is a
personal configuration for MY installation, and we do not know what a future user will
have." Correct, and the argument is not size: vendor code is 3% of the package. It is what
shipping it COMMUNICATES — that the project privileges those brands and is a smart-home
product. Both legible in the file tree, neither true.

The panel resolved it as reclassify, not delete: WiZ is the only executable answer to "how
do I write an adapter", and deleting it leaves that question unanswered. So the claim is
declared (`adapter_kind` on the adapter class), exposed (`GET /v1/adapters`) and tested,
rather than left to inference:

- **ecosystem** — an open standard or open project (MQTT, Matter, BLE, MAVLink, the HA
  bridge). Belongs in a protocol the way HTTP belongs in a web framework.
- **reference** — one vendor's product, shipped as a worked example. Not endorsement, not
  partnership, not a promise to track their firmware.
- **infrastructure** — not a device technology (notifications).

Moving the modules was considered and rejected: ten files import them and any third party
already using `dosync.adapters.wiz` would break, which is a real cost for a statement that a
declared, tested, API-visible attribute makes just as well. A test requires any new adapter
to choose a kind, since inheriting a flattering default is how a classification stops
meaning anything.

**Remote adapter loading is now ruled out in DESIGN-PRINCIPLES**, alongside adapter-side
fallback and for the same reason: the protocol's whole argument is that nothing actuates
hardware without a policy and a record, and fetching executable code from the internet puts
the largest possible hole exactly where the guarantee lives. A one-line bypass was closed
here in July because it let an agent skip the policy engine; a remote plugin loader is that
hole with whole packages through it. Three supported paths instead — ecosystem adapters in
the package, declarative adapters the operator writes, third-party packages installed
deliberately via entry points — distinguished by consent and attribution: someone chose to
install it, and someone's name is on it.

777/777.

## DECLARATIVE-ADAPTERS — Describe a device instead of programming one — SHIPPED 2026-07-27
Panel decisions 3, 4 and 6 from the adapters session. Most of what a hub needs to reach a
device is not interesting code — "send this request, read this field" — and requiring Python
for it made "domain-agnostic" mean "agnostic across the domains we already wrote". An
operator whose device was not among the eight bundled adapters had no path that did not
involve a pull request.

**The design constraint that decides whether this is useful** (Torres, on the panel): the
file must produce a capability MANIFEST, not a command table. A file that only said "POST
/on turns it on" would let DoSync switch the device and leave it invisible to everything
that matters — no intent could select it, no policy could name it, an emergency would pass
it by. So every action declares a `type` (what it MEANS in DoSync's vocabulary) and the
device declares `tags` and `emergency_capable`. A device with no tags loads with a warning
saying it will never be selected by an intent, which is almost never what the author wanted.

**Five worked examples ship**, and per Ferreyra they are the deliverable rather than the
appendix — "if one looks like what I have, I copy it and change the IP". Chosen so that two
are NOT household devices: a 3D printer (emergency_capable, because a hot end runs
unattended for hours) and a floor lighting controller in JSON (a commercial device, and the
case the format handles worst — one endpoint controlling many fixtures). The television
example exists to show what `emergency_capable: false` buys: a screen CAN display a warning,
and in a care facility waking a sleeping resident with an alarm meant for staff is exactly
wrong. The device could; whether it should is the deployment's call.

Verified end to end: the five load at startup, register as devices, and the resolver selects
them for `ensure_safety` with emergency-capable ones scoring higher (34 and 24 against 22
and 12) — the claim the example files make about `emergency_capable` holds.

Placeholders are SUBSTITUTED, never evaluated. A template language in a device description
is a scripting language, and a scripting language read by a process that actuates hardware
is a way to run code without anyone deciding to.

**Stated limits**: HTTP only. No Zigbee, Z-Wave, BLE pairing, OPC-UA sessions, or anything
needing a handshake or vendor SDK. `pyyaml` joins core dependencies for the same reason
`bleak` did — the examples are YAML, and a user who must first discover they need a YAML
library has been handed the problem instead of the answer.

789/789. Still pending from that session: third-party adapters via entry points.

## DECLARATIVE-PANEL-FIXES — Three blockers found before applying — SHIPPED 2026-07-27
Submitted to the panel before applying, as with the audit chain. Refused again, and again
the defects were behaviours that appear on the first day of real use rather than flaws in
the design.

**B1 — `aiohttp` sat in the `ha` extra.** A declarative adapter's only transport is HTTP, so
a user's first declarative device failed at EXECUTION — during an intent, possibly an
emergency — rather than at load. Third appearance of this circle in two days, and this time
inside the feature built to remove it. Moved to core, and the pattern is now a rule in
DESIGN-PRINCIPLES: *a dependency needed to use a capability the project offers by default
cannot be optional; extras are for hardware a deployment may or may not own, and an
advertised capability is not hardware.* The rule notes the shape to watch for — a missing
dependency that fails at startup is an inconvenience, one that fails when an emergency
reaches a device is a different category, and the difference is only visible if someone asks
WHEN the failure lands.

**B2 — the format was half-declarative.** Measured: editing a file UPDATED the device,
deleting it did NOT remove it. Torres: "the worst of both worlds — the user learns from the
first half that the file is the source of truth and discovers in the second that it was
not", leaving phantom devices that emergency intents keep planning around. Benítez named the
danger in the obvious fix before it was written: a directory that failed to mount looks
exactly like a directory whose files were removed, and a hub that reacts to the first by
deregistering a building is worse than one that asks.

Resolved as QUARANTINE. A device whose file is gone leaves resolution, stays in the
inventory, appears as quarantined in the API and the dashboard, and is recorded in the
chain; removal remains an operator's act. Restoring the file returns it to service, also
recorded. An empty directory quarantines nothing.

This needed a distinction the registry did not have: `all()` is INVENTORY (status, exports,
audits — a device the operator can no longer act on is still something they must see) and
`active()` is PARTICIPATION. One method was answering both questions.

**B3 — an unsupported `kind:` loaded silently** and failed only when an intent reached the
device. The project already separates "searched" from "not searchable" in scanning so that
"found nothing" cannot be mistaken for "did not look"; this was the same confusion with
worse timing. Now refused at load, naming the supported transports and pointing at code
adapters.

**R1** duplicate device ids across files are reported instead of the later one winning by
alphabetical accident — an operator could otherwise edit the losing file forever with no
effect. **R2** unedited `REPLACE_WITH_…` values are flagged at load, since copying an example
and forgetting the token is the most likely first mistake.

**Two defects found while fixing these.** Re-registering a device from its restored file
overwrites `adapter_config`, so by the time the un-quarantine check ran there was nothing
left to detect: the device returned to service correctly and SILENTLY, and "when did this
come back" is as much an audit question as "when did it go". Fixed by capturing the
quarantined set before registration. And the new fixture omitted `DOSYNC_AUTH=false`, so the
API returned 401 and the failure surfaced as a KeyError that said nothing about
authentication — it looked like a quarantine bug for several minutes.

798/798; all three blockers verified to fail when reintroduced.

## DECLARATIVE-MQTT + THIRD-PARTY-ADAPTERS — The last two adapter paths — SHIPPED 2026-07-27
Closes the adapters panel session: three ways a technology reaches a deployment, all built.

**MQTT in the declarative format** (Nakamura's recommendation). The most common transport in
industry after HTTP, and the adapter for it already existed — only the declarative format
could not name it. `paho-mqtt` joins core dependencies: 616 KB, thirty times smaller than
bleak, so the dependency rule applies with no size argument to weigh against it.

The MQTT path is deliberately honest about what a publish means: success says the BROKER
accepted the message, not that the device acted on it, and at QoS 0 not even that. For a
light the distinction is pedantic; for the conveyor in the shipped example, with someone
standing near it, it is the whole question — which is why `verify_with` exists and why the
result says so in its response rather than reporting a bare success. The example also shows
per-action QoS overriding the device default, with the reasoning written down: a duplicated
stop is harmless, a lost one is not, and a duplicated START would not be.

**Third-party adapters via entry points** (group `dosync.adapters`). A vendor publishes
`dosync-adapter-x`, the operator installs it, the hub finds it — no pull request here and no
promise from this project to maintain code for hardware it has never seen.

The security posture is the point of the design. DESIGN-PRINCIPLES rules out fetching adapter
code remotely; an entry point differs in that someone chose to install it and someone's name
is on it. But it still runs inside the hub with the hub's permissions, so: logged at WARNING
on load, appended to the audit chain, and its `adapter_kind` set to `third_party` **by the
loader**. Verified with a real installed plugin that declared itself `ecosystem` — a package
claiming to be first-party code of this project — and was overridden. Where code came from is
not the code's to assert.

A broken plugin is skipped rather than fatal: one vendor's bad release must not take a
building offline. Verified with three entry points where two fail — the working one still
loads.

**Found while testing:** `entry_points` was imported inside the function, so no test could
substitute it and the suite tested against whatever happened to be installed in the
environment running it. Moved to module level. A dependency reached for at call time is one a
test cannot replace.

**And a test that stopped testing what it named:** the "unsupported transport" case used
`kind: mqtt`, which this same change made valid — it then passed for the wrong reason. Now
uses zigbee, which is genuinely out of scope.

810/810.

## CI-DEPENDENCY-DRIFT — Red for three commits, green everywhere else — SHIPPED 2026-07-31
Spotted by the operator looking at the commit list on GitHub: the last fully green build was
four days old, and the three since read 3/4, 1/4, 1/4 — getting worse. The suite passed
locally every time.

**Dependencies lived in two places and drifted.** CI ran `pip install -r requirements.txt`
while the package declared its dependencies in `pyproject.toml`. Adding bleak, pyyaml,
aiohttp and paho-mqtt to the package installed them for every user and for nobody in CI, so
four tests failed there and passed everywhere else — the most confusing shape a failure can
take, and the reason it survived three commits. It is also the fourth appearance of this
exact failure mode in this project: the version in four disagreeing places, DOSYNC_DB vs
DOSYNC_DB_PATH, the auth setting, and now this.

Reproduced before fixing rather than assumed: a venv with only `requirements.txt` produced
exactly the four failures CI reported.

**CI now installs the package** in all three jobs. What CI exercises must be what
`pip install dosync` produces; installing a hand-maintained mirror is how they came apart.
`requirements.txt` is KEPT, because a CI job derives minimum versions from it to test
against the declared floor — but it is kept as the floor's input, not as a second opinion
about what the package needs, and a test now fails if the two lists disagree in either
direction.

Verified: a clean venv with `pip install -e .` runs 812/812, against 4 failures with the old
installation path.

**Worth stating as the pattern**, since it keeps recurring in different clothes: two places
holding the same fact will diverge, and the divergence is usually discovered by whoever is
NOT looking at the place that is wrong. Here the tests were right, the package was right, and
the only wrong thing was a list nobody had reason to re-read.

## CI-FLOOR-VERSIONS — Two declared minimums that could not be installed — SHIPPED 2026-07-31
The dependency-drift fix took CI from 1/4 to 3/4. The remaining red job was `floor`, which
installs each dependency at its DECLARED minimum and runs the suite against it — and it was
doing exactly its job.

Two of the four dependencies added this week declared floors nobody can install:
- **`pyyaml>=6.0`** — 6.0 fails to build on modern Python with the Cython
  `cython_sources` error; 6.0.1 is the first version that builds.
- **`aiohttp>=3.8.0`** — 3.8.x has no Python 3.12 support and dies on
  `longintrepr.h`.

Both were written from memory rather than checked, which is the whole reason the floor job
exists: **a minimum version nobody can install is not a minimum, it is a wish.** Isolated by
installing each floor separately rather than guessing from the traceback — the combined
install failed on pyyaml and the error looked like aiohttp's.

Corrected to `pyyaml>=6.0.1` and `aiohttp>=3.9.0`, verified by installing all eight floors
into a clean venv and running the suite against them: 812 passed.

**And the extras contradicted core.** `dosync[ha]` and `dosync[all]` still said
`aiohttp>=3.8.0` while core said `>=3.9.0`. pip resolves the intersection so nothing would
have broken, but two numbers for one fact is precisely how the previous four divergences
started — the version in four places, DOSYNC_DB vs DOSYNC_DB_PATH, the auth setting, and
requirements.txt vs pyproject. A test now fails when an extra names a different floor than
core for the same package.

Both specific versions are pinned by a test as well, recording the fact rather than leaving
the lesson to be re-derived: the floor job catches this, but only after it is merged.

814/814.

## SESSION-AUDIT-FIXES — What the session review found — SHIPPED 2026-08-01
A critical audit of the whole session, run by searching the repository for gaps rather than
confirming successes. Five findings, all real, and the measurement was worse than the
estimate: not seven undocumented event types but **32**, and not five undocumented endpoints
but **27 of 41**.

The gap did not come from this session — it accumulated. What this session did was make it
visible, by adding enough surface at once that the omission stopped being deniable.

**H-1, the blocker: the 0.4.2 changelog was incomplete.** Six entries, all about discovery,
omitting declarative adapters, quarantine, third-party entry points, signed heartbeats and
browser-managed access — two of those with security implications: an endpoint that accepts
unencrypted messages, and a loader for third-party code. Sosa's argument for treating it as
blocking rather than cosmetic: an operator has a right to know what enters their hub.
Rewritten into 21 entries across six areas, leading with the two advertised properties that
were FALSE and are now true.

**H-2: 32 audit event types, none in the specification, which had no table of event types at
all.** Spec §7.8 now lists every one, grouped by what an operator is looking for. This is not
tidiness — the chain's value is that somebody who did not write the hub can read it, and a
second implementation has to know what to emit for the same situation. A chain of names only
its author understands is a log, not evidence.

**H-3: 27 endpoints outside the spec.** §7.9 is the complete HTTP surface. §7.10 specifies
signed heartbeats normatively, including that they MUST be off by default and MUST NOT be
described as secure transport.

**H-4: certification covered none of the new protocol surface.** C09–C12 added: adapters
declare a valid kind, scanning is side-effect free and reports which transports it searched,
the device inventory separates active from quarantined, and the signed-heartbeat channel is
closed unless enabled. 51/56 — the remaining five are the known environmental ones.

C09 failed on its first run, correctly: in certification mode no adapters are registered, and
the check demanded that some exist. Zero adapters is legitimate — what conformance requires
is that whatever IS reported declares a valid basis. Demanding their existence would test the
deployment rather than the protocol.

**H-5: `report_channel` was written and exposed only in health**, not in the device list
where an operator actually looks. Fifth instance this session of writing a value and
half-exposing it.

`python3 -m dosync.spec_coverage --check` now fails when the implementation grows past the
specification, and three tests enforce it plus changelog coverage. 851/851.

**Worth recording as the lesson**, in Benítez's words: what was overlooked was not code but
protocol documentation, and the reason is worth facing — a session dominated by making the
system usable is exactly when a specification feels like bureaucracy, and exactly when it is
most needed.

## DECLARATIVE-LAST-FILE — The quarantine that did not fire — SHIPPED 2026-08-01
Found on the reference deployment, by the drill the second session audit said was missing:
declarative adapters and quarantine were validated by tests only, and Paredes noted that this
project has a habit of finding things when hardware enters. It did.

The operator copied an example, restarted, saw the device register (21 active), deleted the
file, restarted — **and the device stayed active**. Silently.

**Cause: the guard protecting one legitimate case was blocking another.** The quarantine pass
only ran `if _declared` — if at least one file had loaded — which is Benítez's protection
against a failed mount deregistering a building. But an operator removing their LAST
declarative device produces exactly the same empty directory, and the guard blocked that too.
The tests missed it because every one of them left a second file behind; the "empty directory
changes nothing" test asserted the very behaviour that turned out to be wrong for this case.

**Resolved by remembering.** The hub stores how many declarative files it saw last time.
Going from some to none is a change it WITNESSED, and quarantine is the safe response to a
witnessed disappearance because quarantine is not deletion: the device stays in the
inventory, leaves intent resolution, and returns the moment the file does — verified in both
directions. A first start that finds nothing, or a directory that was already empty, has
witnessed nothing and does not act.

A directory that vanishes entirely now also quarantines. That is defensible on the same
grounds: a hub planning emergencies around devices whose definitions it cannot read is worse
than one that sets them aside reversibly, and the device comes back with the disk.

Two conflicting requirements, both reasonable, that could not both hold under the old rule.
The distinction that reconciles them is not "empty versus non-empty" but "did the hub see
this change happen". 853/853, verified to fail when the old guard is restored.

## PUBLICATION-READINESS — Two things the package was about to ship without — SHIPPED 2026-08-02
A third audit, run against the state of the repository at the moment of publishing rather
than against the work done. Two findings, both invisible to the previous two passes because
neither had asked "what does a stranger receive".

**Two `[Unreleased]` sections, both holding shipped work.** One contained 0.4.1's contents
nine days after 0.4.1 was published; the other held part of 0.4.2. A reader of the changelog
saw published functionality marked as not released — and one of those entries was a
BEHAVIOUR CHANGE to `audit-verify` that can break a cron job. Closing a version means moving
a heading, and it was missed twice. Now a test fails when an `[Unreleased]` section sits
below the current version, and when the version about to ship has no section at all.

**The declarative examples did not travel in the wheel.** They lived only at the repository
root, so anyone who installed from PyPI and never cloned had none — and the panel had called
the examples the deliverable rather than the appendix, on the grounds that the format is
learned by finding one that resembles your device and changing the address. Moved into
`dosync/examples/declarative/` and declared as package-data; verified by installing the
wheel into a clean venv and counting six.

Same shape as the dashboard shipping outside the package in July, and as `report_channel`
being written without being exposed: the work existed and did not reach the person it was
for. Third instance of that pattern this week, which suggests the question worth asking after
any feature is not "does it work" but "does it arrive".

853/853; clean install verified end to end — version, dashboard, examples, hub, adapters.
