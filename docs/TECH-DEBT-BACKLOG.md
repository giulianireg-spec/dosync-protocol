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


## DEVICE-HEALTH-ACTIVE — Device heartbeat + cause attribution · effort M
PARTIALLY DELIVERED 2026-07-14. The wiring audit found that active probing already existed:
a background refresher polling get_state() every 60s. It had never run in production because
server.py gated it on `isinstance(hub.resolver, StateAwareResolver)` — always False under the
ExternalResolver production runs — and said so only at debug level. It is now hub-owned
(DoSyncHub.start_state_refresh), runs under any resolver, and marks devices reachable on a
successful probe, so recovery is detected within one interval WITHOUT executing any action.

  DONE (a) periodic lightweight probe so health is known without acting.
  TODO (b) device-initiated heartbeat (a device reports its own health proactively) — needs a
           protocol surface (endpoint + spec), not just wiring.
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

## HA-BRIDGE-HYGIENE — Housekeeping entities imported as devices · effort S
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

## AUDIT-PROVENANCE — Chain must bind decisions, not only commands · effort M
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

## EMERGENCY-UNSAT-ESCALATION — A silent no-op emergency is the worst state · effort S
Origin: same review. Verified: stacked absolute exclusions can empty an emergency plan
and today that executes zero actions silently (status completed). Rejecting the plan is
the WRONG fix — it would override the operator's declared judgment, which this layer
refuses to do; the failure is silence, not obedience. Work: (a) executing an EMERGENCY
intent whose post-policy plan is empty emits a dedicated audit entry + operator
notification ("emergency fired, 0 actions after policy filtering — your standing rules
made this intent unsatisfiable"); (b) config-load lint against the live registry warning
when declared exclusions can empty an emergency-class intent. Validation: the drill
produces the loud path, and the lint fires the day the rule is written.

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
