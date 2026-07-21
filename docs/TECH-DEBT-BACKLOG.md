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
  TODO (c) distinguish "powered off" from "network-unreachable" where the transport allows it.

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

## INDEPENDENT-OBSERVATION — Device ack ≠ observed reality · design exploration · effort M/L
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
