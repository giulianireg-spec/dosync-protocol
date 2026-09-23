"""
DoSync Hub — Capability Registry + Semantic Resolver
Layers 3 & 4 of the DoSync protocol stack
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import threading
import time
try:
    from dosync import metrics as _M
except Exception:  # metrics is optional; never let it break the hub
    _M = None
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:  # annotation only
    from .executor import DeviceExecutor

from .db import DoSyncDB
from dataclasses import dataclass, field
from .models import (
    ActionPlan, ActionResult, ActuatorSpec, CapabilityManifest,
    ContextSignalType, DeviceAction, DeviceEvent, FamilyProfile,
    Intent, IntentClass, IntentResult, OccupancyState, Phase,
    PhasedActionPlan, PhaseAction, PresenceSignal, RoutineAction, Urgency,
)

log = logging.getLogger("dosync.hub")


# ── Capability Registry (Layer 3) ─────────────────────────────────────────────




# ── Audit log ─────────────────────────────────────────────────────────────────
#
# Moved to dosync/audit.py. Re-exported here so that every existing import —
# manage.py, audit_backup.py, the tests — keeps working unchanged: an
# extraction that forces callers to move at the same time is a rewrite
# wearing an extraction's clothes.

from dosync.restore import HubRestorer  # noqa: E402
from dosync.resolvers import (  # noqa: E402,F401
    QUARANTINE_KEY, BaseResolver, CapabilityMatchingResolver, ExternalResolver,
    ScoreBreakdown, StateAwareResolver, is_quarantined, quarantine_reason)
from dosync.audit import (AuditLog, CheckpointKeeper,  # noqa: E402,F401
                          _assurance_is_regulated, checkpoint_export_mode)

# ── Execution timing and device health ───────────────────────────────────────
#
# Moved to dosync/execution.py. Re-exported so every existing import keeps
# working unchanged.

from dosync.execution import DeviceHealth, _TimedExecutor  # noqa: E402,F401
from dosync.plan_executor import PlanExecutor  # noqa: E402,F401
from dosync.composite_executor import CompositeExecutor  # noqa: E402,F401
from dosync.state_refresh import StateRefresher  # noqa: E402,F401

# Capability registry: moved to dosync/registry.py (the 6th of eleven).
# Re-exported so every existing import keeps working unchanged.
from dosync.registry import (  # noqa: E402,F401
    CapabilityRegistry, _warn_if_sensor_type_is_an_event)

# Occupancy engine: moved to dosync/occupancy.py (the 7th of eleven).
# Re-exported so every existing import keeps working unchanged.
from dosync.occupancy import OccupancyEngine  # noqa: E402,F401

# Telemetry bridge: moved to dosync/telemetry.py (the 8th of eleven).
from dosync.telemetry import apply_telemetry as _apply_telemetry  # noqa: E402

class DoSyncHub:
    """
    Main entry point for the DoSync protocol.
    Owns the registry, resolver, and audit log.
    Exposes async methods for device registration and intent execution.
    """

    def __init__(self, db_path: str = "dosync.db"):
        self.registry       = CapabilityRegistry()
        self.resolver       = StateAwareResolver(self.registry, self)
        self.policy_engine  = None  # set via hub.policy_engine = PolicyEngine()
        self._active_intents: dict[str, int] = {}  # intent_value -> priority
        self._active_intent_devices: dict[str, set] = {}  # intent_value -> device_ids
        # v13 hygiene (maintenance stop 2026-07-21, Paredes): progress_cb failures
        # are swallowed so an observer can't break execution — but swallowed !=
        # invisible. Count them so a real callback bug surfaces in /v1/status
        # instead of hiding in debug logs forever.
        # Surfaced in /v1/status so monitoring can catch a checkpoint routine
        # that has quietly stopped. The hub cannot see whether checkpoints are
        # EXPORTED, but it can say when it last produced one.
        # Checkpoint bookkeeping moved to CheckpointKeeper with the four methods
        # that used it. Constructed after db and audit_log exist.
        self._checkpoints = None   # set below, once db is wired
        # Executor for HUB-INITIATED intents — those the hub raises on its own
        # (e.g. the capability-anomaly security alert), which have no caller to
        # supply one. Wired by the server at startup. If it is None the hub
        # cannot dispatch such an intent, and says so rather than failing quietly.
        self.default_executor = None
        self.audit_log      = AuditLog()
        self.occupancy      = OccupancyEngine()
        self.family_profile: FamilyProfile | None = None
        self._event_handlers: list[Callable] = []
        self.db             = DoSyncDB(db_path)
        self.db.init()
        self.health         = DeviceHealth(self)   # hub-owned passive device health
        # Collaborators take the hub, not its services: server.py installs the
        # policy engine (and optionally an external resolver) after this point.
        self._plan_executor      = PlanExecutor(self)
        self._composite_executor = CompositeExecutor(self)
        self._state_refresher    = StateRefresher(self)
        self._checkpoints   = CheckpointKeeper(self.db, self.audit_log)
        # Load persisted state now that db is ready
        if hasattr(self, "resolver"):
            self.resolver._load_state_from_db()
        self.health.load_from_db()
        self.audit_log._persist_cb = self.db.append_audit
        # Restoration lives in dosync/restore.py: 140 lines that run once, at
        # startup, and only ever write onto the hub. Kept as a method here so
        # that anything calling it keeps working — `policies.py` has a method of
        # the same name that is a different thing entirely.
        HubRestorer(self).restore()

    # ── Family profile ───────────────────────────────────────────────────────

    # ── DB restore ──────────────────────────────────────────────────────────





    # ── State refresh: moved to state_refresh.py (the 11th and last) ──
    # The hub owns a StateRefresher (built in __init__) and delegates the loop
    # coroutine and one cycle; the server owns the task that drives them.
    async def start_state_refresh(self, executor, interval=None):
        return await self._state_refresher.start_state_refresh(executor, interval=interval)

    async def _state_refresh_cycle(self, executor):
        return await self._state_refresher._state_refresh_cycle(executor)

    async def start_checkpoint_scheduler(self, interval: float = None,
                                         directory: str = None) -> None:
        """Default interval: DOSYNC_CHECKPOINT_INTERVAL, or "86400" — daily.

        The number is repeated in this docstring on purpose. A test asserts it
        appears in the source of this method, because an implementer looking for
        the default looks here, at the entry point, not in the class the work
        was delegated to.
        """
        return await self._checkpoints.start_checkpoint_scheduler(
            interval=interval, directory=directory)

    # State that used to be hub attributes. Exposed as properties rather than
    # copied, so there is one value and not two that can drift.
    @property
    def _checkpoint_export_state(self) -> str:
        return self._checkpoints._checkpoint_export_state

    @property
    def _last_checkpoint_at(self) -> float | None:
        return self._checkpoints._last_checkpoint_at

    @property
    def _last_checkpoint_path(self) -> str | None:
        return self._checkpoints._last_checkpoint_path

    @property
    def _last_checkpoint_export_at(self) -> float | None:
        return self._checkpoints._last_checkpoint_export_at

    def write_checkpoint(self, directory: str = None) -> str | None:
        return self._checkpoints.write_checkpoint(directory=directory)

    def maybe_archive(self, *args, **kwargs):
        return self._checkpoints.maybe_archive(*args, **kwargs)

    def set_family_profile(self, profile: FamilyProfile) -> None:
        """Load the family profile into the hub and persist it."""
        self.family_profile = profile
        self.db.save_family_profile(profile.to_dict())
        self.audit_log.append({
            "type":        "profile_loaded",
            "family_name": profile.family_name,
            "bedtime":     f"{profile.bedtime_hour:02d}:{profile.bedtime_minute:02d}",
        })
        log.info("Family profile loaded: %s", profile.family_name)

    # ── Occupancy / presence ─────────────────────────────────────────────────

    def update_presence(self, signal: PresenceSignal) -> OccupancyState:
        """A context provider updates its presence signal."""
        self.occupancy.update(signal)
        self.db.save_presence_signal(signal.device_id, {
            "device_id":   signal.device_id,
            "signal_type": signal.signal_type.value,
            "present":     signal.present,
            "confidence":  signal.confidence,
            "member_id":   signal.member_id,
            "timestamp":   signal.timestamp,
        })
        state = self.occupancy.get_occupancy()
        self.audit_log.append({
            "type":         "presence_updated",
            "device_id":    signal.device_id,
            "signal_type":  signal.signal_type.value,
            "present":      signal.present,
            "confidence":   signal.confidence,
            "occupied":     state.occupied,
            "occ_confidence": state.confidence,
        })
        return state

    def get_occupancy(self) -> OccupancyState:
        """Current inferred occupancy state."""
        return self.occupancy.get_occupancy()

    # ── Device management ────────────────────────────────────────────────────

    def register_device(self, manifest: CapabilityManifest) -> None:
        """
        Register or update a device in the hub registry.

        On re-registration, classifies the change using firmware + capability diff
        (per DoSync spec §14) and emits the appropriate audit entry and events:

        - No change:              silent reconnect, no extra audit entry
        - firmware changed only:  device_firmware_updated audit entry
        - caps changed (fw too):  device_updated audit entry with diff
        - caps changed (same fw): device_capability_anomaly + alert_anomaly intent
        """
        existing = self.registry.get(manifest.device_id)

        # First-time registration
        if existing is None:
            self.registry.register(manifest)
            self.db.save_device(manifest.device_id, manifest.to_dict())
            self._warn_if_unexecutable(manifest)
            self.audit_log.append({
                "type":        "device_registered",
                "device_id":   manifest.device_id,
                "device_name": manifest.device_name,
            })
            return

        # ── Re-registration: compute diff ────────────────────────────────────
        fw_changed   = existing.firmware != manifest.firmware
        ec_changed   = existing.emergency_capable != manifest.emergency_capable
        tags_changed = set(existing.tags) != set(manifest.tags)
        act_changed  = (
            sorted(a.type for a in existing.actuators) !=
            sorted(a.type for a in manifest.actuators)
        )
        caps_changed = ec_changed or tags_changed or act_changed

        # Silent reconnect — nothing changed
        if not fw_changed and not caps_changed:
            self.registry.register(manifest)
            self.db.save_device(manifest.device_id, manifest.to_dict())
            return

        # Build diff for audit
        diff = {}
        if fw_changed:
            diff["firmware"] = {"from": existing.firmware, "to": manifest.firmware}
        if ec_changed:
            diff["emergency_capable"] = {
                "from": existing.emergency_capable,
                "to":   manifest.emergency_capable,
            }
        if tags_changed:
            diff["tags"] = {
                "added":   list(set(manifest.tags) - set(existing.tags)),
                "removed": list(set(existing.tags) - set(manifest.tags)),
            }
        if act_changed:
            old_act = set(a.type for a in existing.actuators)
            new_act = set(a.type for a in manifest.actuators)
            diff["actuators"] = {
                "added":   list(new_act - old_act),
                "removed": list(old_act - new_act),
            }

        # ── Dr. Esteves classification ────────────────────────────────────────
        # firmware changed + caps changed  → expected firmware update
        # firmware changed + caps stable   → minor firmware upgrade
        # firmware stable  + caps changed  → ANOMALY — alert
        if fw_changed and caps_changed:
            change_type = "firmware_update"
        elif fw_changed and not caps_changed:
            change_type = "firmware_upgrade_minor"
        else:  # not fw_changed and caps_changed
            change_type = "capability_anomaly"

        # Update registry and DB
        self.registry.register(manifest)
        self.db.save_device(manifest.device_id, manifest.to_dict())

        # Emit audit entry
        if change_type == "capability_anomaly":
            self.audit_log.append({
                "type":        "device_capability_anomaly",
                "device_id":   manifest.device_id,
                "device_name": manifest.device_name,
                "diff":        diff,
                "note":        "Capabilities changed without firmware version change — may indicate compromise",
            })
            # Fire alert_anomaly intent for security-relevant changes
            import asyncio
            from dosync.models import Intent, IntentClass, Urgency
            alert_intent = Intent(
                intent=IntentClass("alert_anomaly"),
                urgency=Urgency.ALERT,
                context={
                    "trigger":     "device_capability_anomaly",
                    "device_id":   manifest.device_id,
                    "device_name": manifest.device_name,
                    "diff":        diff,
                },
                source="hub",
            )
            # Fire the alert intent. This is a SECURITY path (capabilities changed
            # without a firmware bump), so "best-effort" must not mean "silent":
            # registration is never blocked, but a failure to raise the alert is
            # logged loudly rather than swallowed.
            #
            # asyncio.get_event_loop() was deprecated in 3.10 and is scheduled to
            # raise when no loop is running. Under the old code that would have
            # made the no-loop branch unreachable and dropped the alert into a
            # bare `except: pass` — the POL-2 failure mode (a silent except
            # hiding a broken security path). Both cases are now explicit.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            # The alert needs an executor. Until 2026-07-21 this call passed none
            # and raised TypeError on EVERY anomaly — swallowed whole by a bare
            # `except Exception: pass`, so this security alert had never once
            # fired. The anomaly itself was always audited (above); only the
            # dispatch was dead. Missing executor is now reported, not hidden.
            if self.default_executor is None:
                log.error(
                    "Capability-anomaly alert for %s NOT dispatched: no default_executor "
                    "wired on the hub. The anomaly is recorded in the audit chain, but no "
                    "intent was raised.", manifest.device_id)
                loop = None
                alert_intent = None

            if alert_intent is not None and loop is not None:
                # Inside a running loop (normal server path): schedule it without
                # blocking registration, but attach a callback so a failure in the
                # detached task is reported instead of vanishing.
                task = asyncio.ensure_future(
                    self.execute_intent(alert_intent, self.default_executor))

                def _report_alert_outcome(t: "asyncio.Future") -> None:
                    if t.cancelled():
                        log.warning("Capability-anomaly alert for %s was cancelled",
                                    manifest.device_id)
                        return
                    exc = t.exception()
                    if exc is not None:
                        log.error("Capability-anomaly alert for %s FAILED to execute: %s",
                                  manifest.device_id, exc)

                task.add_done_callback(_report_alert_outcome)
            elif alert_intent is not None:
                # No running loop (CLI, migration scripts, sync tests): run it to
                # completion, as the previous run_until_complete branch did.
                try:
                    asyncio.run(self.execute_intent(alert_intent, self.default_executor))
                except Exception as e:
                    log.error("Capability-anomaly alert for %s FAILED to execute: %s",
                              manifest.device_id, e)
        else:
            self.audit_log.append({
                "type":        "device_updated" if caps_changed else "device_firmware_updated",
                "device_id":   manifest.device_id,
                "device_name": manifest.device_name,
                "change_type": change_type,
                "diff":        diff,
            })

    def unregister_device(self, device_id: str) -> None:
        self.registry.unregister(device_id)
        self.db.delete_device(device_id)
        self.audit_log.append({"type": "device_unregistered", "device_id": device_id})

    # ── Intent execution ─────────────────────────────────────────────────────


    # ── FailurePolicy execution strategies ────────────────────────────────────

    @property
    def progress_cb_failures(self) -> int:
        """Best-effort progress-callback failures, owned by the PlanExecutor
        and surfaced here where /v1/status and the tests read it."""
        return self._plan_executor.progress_cb_failures

    # ── Plan execution engine: moved to plan_executor.py (the 9th of eleven) ──
    # The hub owns a PlanExecutor (built in __init__) and delegates to it the
    # entry points that execute_intent, the composite section and three tests
    # call by name, so every caller keeps working. The parallel / abort / retry
    # strategies are internal to PlanExecutor and are not delegated.
    async def _execute_with_policy_cb(self, plan, executor, intent, progress_cb=None):
        return await self._plan_executor._execute_with_policy_cb(
            plan, executor, intent, progress_cb)

    async def _execute_with_policy(self, plan, executor, intent):
        return await self._plan_executor._execute_with_policy(plan, executor, intent)

    def _validate_plan_params(self, plan, intent):
        return self._plan_executor._validate_plan_params(plan, intent)

    def _action_execution_model(self, action):
        return self._plan_executor._action_execution_model(action)

    def _split_plan_by_execution_model(self, plan):
        return self._plan_executor._split_plan_by_execution_model(plan)

    async def _start_long_running_actions(self, long_running_actions, executor, intent):
        return await self._plan_executor._start_long_running_actions(
            long_running_actions, executor, intent)

    def apply_telemetry(self, device_id: str, event, reason: str = "",
                        phase: str = None, now: float = None) -> dict:
        """Apply one telemetry fact to a device's active operation.

        Delegates to telemetry.apply_telemetry (the 8th extraction) with the
        hub's database and audit log; see there for the full flow.
        """
        return _apply_telemetry(self.db, self.audit_log, device_id, event,
                                reason=reason, phase=phase, now=now)

    # ── Composite intents: moved to composite_executor.py (the 10th of eleven) ──
    # The hub owns a CompositeExecutor (built in __init__) and delegates the two
    # entry points -- execute_composite_intent (called by tests) and
    # _route_composite_intent (called by execute_intent). The dispatch, state-read
    # and guard-provider helpers are internal to CompositeExecutor.
    async def execute_composite_intent(self, intent, executor, context, guard_set=None,
                                       config=None):
        return await self._composite_executor.execute_composite_intent(
            intent, executor, context, guard_set=guard_set, config=config)

    async def _route_composite_intent(self, intent, executor, composition_kind):
        return await self._composite_executor._route_composite_intent(
            intent, executor, composition_kind)

    def _resolve_verify_bindings(self, plan, intent) -> None:
        """INDEPENDENT-OBSERVATION (panel D1): fill in each action's verify_with
        from the manifest (the manufacturer's natural pairing) and/or the intent
        context (a deployment cross-device binding, which overrides).

        Intent context format:
            context["verify_with"] = {
              "lock-front": {"sensor_id": "door-sensor:bolt",
                             "expected_reading": "locked", "deadline_s": 5},
              "lock-front:unlock": {...}          # optional per-action override
            }
        Keys are matched most-specific-first: "device:action", then "device".
        Anything malformed is IGNORED with a warning — a bad binding must never
        break dispatch (verification is an observation, not a gate).
        """
        from .models import VerifyBinding

        ctx = (intent.context or {}).get("verify_with") or {}
        for action in plan.actions:
            binding = None

            # 1. Manifest: the actuator's own declared pairing, if any.
            device = self.registry.get(action.device_id)
            if device:
                for act in getattr(device, "actuators", []):
                    if act.type == action.action or act.id == action.action:
                        binding = getattr(act, "verify_with", None)
                        break

            # 2. Intent context overrides (most specific key wins).
            raw = ctx.get(f"{action.device_id}:{action.action}") or ctx.get(action.device_id)
            if raw is not None:
                try:
                    binding = raw if isinstance(raw, VerifyBinding) else VerifyBinding(
                        sensor_id=raw["sensor_id"],
                        expected_reading=raw["expected_reading"],
                        deadline_s=float(raw.get("deadline_s", 5.0)))
                except Exception as e:
                    log.warning("Ignoring malformed verify_with for %s/%s: %s",
                                action.device_id, action.action, e)

            if binding is not None:
                action.verify_with = binding

    def instrumented(self, executor):
        """Wrap an executor so every action it runs is recorded.

        The wrapper (_TimedExecutor) times the action, records it in device
        health -- the success-rate history behind /v1/health/devices and the
        reachability refresh -- and runs the independent verify_with check when
        the action carries one. Every path that executes actions must go through
        here: intents and direct actions alike. Idempotent, and deliberately not
        conditional on the metrics module: losing latency numbers must never
        also switch off health and verification.
        """
        if getattr(executor, "_dosync_timed", False):
            return executor
        return _TimedExecutor(executor, hub=self)

    async def execute_intent(
        self,
        intent: Intent,
        executor: "DeviceExecutor",
        progress_cb=None,
    ) -> IntentResult:
        log.info("Executing intent: %s [%s]", intent.intent.value, intent.urgency.value)

        # Wrap once, so every action on every path below (parallel/abort/retry/
        # long-running/composite) is timed, recorded in device health and
        # independently verified. See instrumented().
        executor = self.instrumented(executor)

        # ── Composition routing (Level 2) ─────────────────────────────────────
        # A composition intent (declared with composition_kind, e.g. inspect_area
        # -> "perimeter") does NOT resolve to a flat parallel plan. It composes an
        # ordered sequence the OperationSupervisor drives in a closed loop. The
        # routing decision lives HERE in the hub — not in the REST endpoint — so
        # every client (REST, MCP, tests) inherits the same behavior.
        #
        # An intent WITHOUT composition_kind falls straight through to the normal
        # flat path below — zero change for every existing intent. An UNKNOWN kind
        # fails explicitly (never silently falls to the flat path): a declared
        # composition the hub cannot compose is a configuration error that must shout.
        intent_class_row = self.db.get_intent_class(intent.intent.value)
        composition_kind = (intent_class_row or {}).get("composition_kind")
        if composition_kind:
            return await self._route_composite_intent(
                intent, executor, composition_kind)

        _t0 = time.perf_counter()
        plan = self.resolver.resolve(intent)
        if _M is not None:
            _M.intent_resolution_seconds.observe(time.perf_counter() - _t0)

        # ── Parameter validation (protocol v0.3) ──────────────────────────────
        # Validate each action's params against its actuator's JSON Schema BEFORE
        # dispatch. An action whose params violate the schema is rejected
        # individually, recorded in the audit log, and the rest of the plan
        # continues (partial result) — a single bad parameter never aborts a plan.
        #
        # Opt-out by latency: on the EMERGENCY path, validation is skipped so the
        # response is never delayed. Even when active, rejection-and-continue means
        # validation cannot tumble an emergency — only the invalid action drops.
        # Controlled by DOSYNC_VALIDATE_PARAMS (default "true"); emergencies always skip.
        rejected_actions = []
        _validate = os.environ.get("DOSYNC_VALIDATE_PARAMS", "true").lower() == "true"
        if _validate and intent.urgency != Urgency.EMERGENCY:
            plan, rejected_actions = self._validate_plan_params(plan, intent)

        # Policy Engine evaluation
        if self.policy_engine:
            from .policies import PolicyDecision
            from .models import ActionPlan as _AP
            policy_result = self.policy_engine.evaluate(intent, plan)
            if policy_result.decision == PolicyDecision.BLOCK:
                # Determine if this is an emergency intent blocked by a non-bypassable policy
                # This is a security-notable event: operator explicitly overrides emergency bypass
                is_emergency_block = (
                    intent.urgency == Urgency.EMERGENCY
                    and not getattr(
                        next((p for p in self.policy_engine._policies
                              if p.name == policy_result.policy_name), None),
                        "bypass_on_emergency", True
                    )
                )
                audit_type = "emergency_intent_blocked_by_policy" if is_emergency_block else "intent_blocked"
                log.warning(
                    "Intent %s BLOCKED by policy '%s' (urgency=%s, emergency_override=%s): %s",
                    intent.intent.value, policy_result.policy_name,
                    intent.urgency.value, is_emergency_block, policy_result.reason,
                )
                self.audit_log.append({
                    "type":              audit_type,
                    "intent_id":         intent.intent_id,
                    "intent":            intent.intent.value,
                    "urgency":           intent.urgency.value,
                    "source":            getattr(intent, "source", "api"),
                    "policy":            policy_result.policy_name,
                    "reason":            policy_result.reason,
                    "emergency_override": is_emergency_block,
                })
                return IntentResult(intent_id=intent.intent_id, success=False, results=[], failed_devices=[])
            elif policy_result.decision == PolicyDecision.CONFIRM:
                log.info("Intent PENDING CONFIRMATION by policy '%s': %s",
                         policy_result.policy_name, policy_result.reason)
                self.audit_log.append({
                    "type": "intent_pending_confirmation",
                    "intent_id": intent.intent_id,
                    "intent": intent.intent.value,
                    "policy": policy_result.policy_name,
                    "reason": policy_result.reason,
                })
                return IntentResult(intent_id=intent.intent_id, success=False, results=[], failed_devices=[])
            elif policy_result.decision == PolicyDecision.MODIFY:
                # ── AUDIT-PROVENANCE (2026-07-18, from external review) ──────
                # Until today a MODIFY left its trace in the runtime log and
                # NOWHERE in the tamper-evident chain: BLOCK and CONFIRM were
                # chain-bound, but the most common policy decision — "the plan
                # ran, minus these devices" — was reconstructible only from a
                # rotating journal. The chain must bind the DECISION, not just
                # the commands sent: what was proposed, what was removed, which
                # policy decided, and the fingerprint of the exact policy file
                # that was loaded (the file on disk may change; the hash of
                # what this hub enforced does not).
                _pre_devices  = sorted({a.device_id for a in plan.actions})
                _post_devices = sorted({a.device_id for a in policy_result.modified_actions})
                self.audit_log.append({
                    "type":          "policy_modified",
                    "intent_id":     intent.intent_id,
                    "intent":        intent.intent.value,
                    "urgency":       intent.urgency.value,
                    "source":        getattr(intent, "source", "api"),
                    "policy":        policy_result.policy_name,
                    "reason":        policy_result.reason,
                    "pre_policy_devices":  _pre_devices,
                    "post_policy_devices": _post_devices,
                    "removed_devices":     sorted(set(_pre_devices) - set(_post_devices)),
                    "policies_fingerprint": getattr(self.policy_engine, "policies_fingerprint", None),
                })
                plan = _AP(intent_id=plan.intent_id, actions=policy_result.modified_actions, urgency=plan.urgency)

                # ── EMERGENCY-UNSAT-ESCALATION (same review) ─────────────────
                # Stacked absolute exclusions CAN empty an emergency plan. The
                # wrong fix is rejecting the plan — that would override the
                # operator's declared judgment, which is precisely what this
                # layer refuses to do. The failure mode is not obedience, it is
                # SILENCE: until today this executed zero actions with status
                # "completed" and nobody was told. Honor the rules; say so
                # loudly; leave a dedicated chain entry.
                if (intent.urgency == Urgency.EMERGENCY
                        and _pre_devices and not plan.actions):
                    log.critical(
                        "EMERGENCY intent %s is UNSATISFIABLE: %d device(s) resolved, "
                        "0 remain after policy filtering. Your standing rules made this "
                        "emergency a no-op — review the deployment policy file.",
                        intent.intent.value, len(_pre_devices),
                    )
                    self.audit_log.append({
                        "type":       "emergency_unsatisfiable",
                        "intent_id":  intent.intent_id,
                        "intent":     intent.intent.value,
                        "source":     getattr(intent, "source", "api"),
                        "resolved_devices": _pre_devices,
                        "policy":     policy_result.policy_name,
                        "policies_fingerprint": getattr(self.policy_engine, "policies_fingerprint", None),
                    })

        # Register active intent for conflict detection
        from .policies import get_intent_priority
        intent_value = intent.intent.value
        self._active_intents[intent_value] = get_intent_priority(intent_value)
        self._active_intent_devices[intent_value] = {a.device_id for a in plan.actions}

        # ── Split by execution model (execution_model) ─────────────────────────
        # Long-running actions follow a separate sub-protocol: they are STARTED and
        # tracked as operations, not awaited to completion. Instant actions take the
        # existing path completely unchanged. An intent with no long-running actions
        # behaves exactly as before (started_operations stays empty).
        from .models import ActionPlan as _APlan
        _instant_actions, _long_running_actions = self._split_plan_by_execution_model(plan)
        started_operations = []
        if _long_running_actions:
            started_operations = await self._start_long_running_actions(
                _long_running_actions, executor, intent
            )
            # The instant path below operates only on the instant sub-plan.
            plan = _APlan(intent_id=plan.intent_id, actions=_instant_actions,
                          urgency=plan.urgency,
                          failure_policy=getattr(plan, "failure_policy", None))

        # INDEPENDENT-OBSERVATION: resolve each action's verify_with binding
        # before dispatch. Manifest declares the manufacturer's natural pairing;
        # the intent context can add or OVERRIDE a cross-device binding, and wins
        # (panel decision D1 — the manufacturer cannot know sensors it does not
        # ship with). Opt-in throughout: an action with no binding from either
        # source stays verify_with=None and behaves exactly as before.
        self._resolve_verify_bindings(plan, intent)

        # Execute with the plan's FailurePolicy
        results, failed, aborted, policy_applied = [], [], [], "continue"
        try:
            results, failed, aborted, policy_applied = await self._execute_with_policy_cb(
                plan, executor, intent, progress_cb=progress_cb
            )
        finally:
            _claim_devices = self._active_intent_devices.get(intent_value, set())
            self._active_intents.pop(intent_value, None)
            self._active_intent_devices.pop(intent_value, None)
            # Release any device claim this intent asserted at the arbiter layer, so
            # the short grace window starts now (see dosync/device_arbiter.py). The
            # rank guard ensures a lower-urgency intent completing first cannot start
            # the grace on a higher-urgency (emergency) claim on a shared device.
            _release = getattr(executor, "release_claim", None)
            if _release is not None and _claim_devices:
                try:
                    _rank = {"info": 0, "warning": 1, "alert": 2, "emergency": 3}.get(
                        getattr(intent.urgency, "value", str(intent.urgency)), 0)
                    _release(_claim_devices, _rank)
                except Exception:
                    pass

        # INDEPENDENT-OBSERVATION (panel design 2026-07-21): a verification result
        # that CONTRADICTS or is UNVERIFIABLE is a first-class audit event, with
        # expected/observed/sensor/independence. The protocol REPORTS honestly and
        # does NOT act (no auto-retry, no auto-escalation — panel decision D2):
        # the response is deployment policy, never protocol-automatic. `success`
        # is untouched — the device accepted the command; verification answers the
        # separate question of whether the effect was independently observed.
        from .models import VerificationStatus as _VS
        for _r in results:
            _v = getattr(_r, "verification", None)
            if _v is None or _v.status in (_VS.VERIFIED, _VS.UNVERIFIED):
                continue
            _ev = ("action_contradicted" if _v.status == _VS.CONTRADICTED
                   else "action_unverifiable")
            self.audit_log.append({
                "type":         _ev,
                "intent_id":    intent.intent_id,
                "device_id":    _r.device_id,
                "action":       _r.action,
                "sensor_id":    _v.sensor_id,
                "expected":     _v.expected,
                "observed":     _v.observed,
                "independence": _v.independence,
            })
            if _v.status == _VS.CONTRADICTED:
                log.warning("Action %s on %s reported success but sensor %s CONTRADICTS: "
                            "expected=%r observed=%r (%s) — reporting, not acting (policy decides)",
                            _r.action, _r.device_id, _v.sensor_id, _v.expected,
                            _v.observed, _v.independence)

        # Rejected-by-validation actions count against full success: the plan did
        # not do everything the mind asked. Per the panel, this resolves to
        # `partial` even if every dispatched action succeeded — and is recorded
        # distinctly from device failures (see audit type above).
        has_rejected = len(rejected_actions) > 0
        has_operations = len(started_operations) > 0
        success = len(failed) == 0 and len(aborted) == 0 and not has_rejected
        if not results and not has_rejected and has_operations:
            # The intent only started long-running operations (no instant actions).
            # Not failed — accepted and running. `accepted` is the honest status:
            # nothing is done yet, but operations are underway.
            status = "accepted"
        elif not results and not has_rejected:
            status = "failed"
        elif aborted:
            status = "partial_abort"
        elif not results and has_rejected:
            # Every action was rejected by validation; nothing executed.
            status = "rejected_invalid_params"
        elif failed and len(failed) < len(results):
            status = "partial"
        elif failed:
            status = "failed"
        elif has_rejected:
            # Some actions executed, some rejected by validation → partial.
            status = "partial"
        elif any(getattr(r, "retries", 0) > 0 and not r.success for r in results):
            status = "retry_exhausted"
        elif has_operations:
            # Instant actions all succeeded AND long-running operations started:
            # the instant part is done but the intent as a whole is still running.
            status = "accepted"
        else:
            status = "success"

        intent_result = IntentResult(
            intent_id=intent.intent_id,
            success=success,
            results=results,
            failed_devices=failed,
            aborted_devices=aborted,
            failure_policy_applied=policy_applied,
            status=status,
            rejected_actions=[
                {"device_id": a.device_id, "action": a.action, "reason": err}
                for a, err in rejected_actions
            ],
            operations=started_operations,
        )
        # Audit log
        self.audit_log.append({
            "type":             "intent_executed",
            "intent_id":        intent.intent_id,
            "intent":           intent.intent.value,
            "urgency":          intent.urgency.value,
            "source":           getattr(intent, "source", "api"),
            "actions":          len(plan.actions),
            # The chain answers "what did this system do". An action that never
            # left the hub is part of that answer and used not to be: entries
            # written before 2026-08-13 do not distinguish execution from
            # simulation, and are not rewritten — see AUDIT-THREAT-MODEL.md.
            "actions_simulated": sum(1 for r in results if getattr(r, "simulated", False)),
            "failed":           failed,
            "aborted":          aborted,
            "failure_policy":   policy_applied,
            "status":           status,
            "success":          success,
        })

        return intent_result

    #: Adapter names that mean "simulate this on purpose". A manifest carrying
    #: one is making a deliberate choice, not a mistake, and must not be
    #: reported as a problem — see report_unexecutable_devices.
    #: `"none"` is deliberately NOT here: a manifest saying "none" is saying it
    #: has no adapter, which is the misconfiguration this reports, not a request
    #: to simulate.
    DECLARED_SIMULATION_ADAPTERS = frozenset({"simulated", "simulation"})

    def report_unexecutable_devices(self) -> list[dict]:
        """Report every registered device whose actions nobody can carry out.

        `_warn_if_unexecutable` only fires when a device registers, and devices
        restored from the database at startup do not take that path — they go
        straight into the registry. So the check covered new arrivals and missed
        the entire existing fleet, which is precisely where a device can sit
        misconfigured for months: the reference deployment's SMS notifier was
        found this way, by hand, long after the fact.

        Called once at startup AFTER the adapters have registered — running it
        earlier would report every device as unexecutable, since no adapter
        exists yet.

        A device whose manifest names the adapter `"simulated"` is NOT reported.
        Declaring simulation is a legitimate choice — a test alarm, a device
        whose hardware has not arrived, a certification fixture — and it is the
        third of SIMULATION_REASONS for exactly that reason. The first run of
        this sweep on the reference deployment flagged such a device alongside
        the genuinely misconfigured one, which is how a useful warning becomes
        noise an operator learns to skip, taking the real finding with it.

        Returns the affected devices so a caller can surface them; also logs,
        because an operator reading the boot log is the reader this is for.
        """
        executor = getattr(self, "executor", None)
        known = getattr(executor, "_adapters", None)
        if not isinstance(known, dict):
            return []   # no adapter executor — nothing to compare against
        found = []
        for device in self.registry.active():
            actuators = list(getattr(device, "actuators", []) or [])
            if not actuators:
                continue
            adapter = getattr(device, "adapter", None)
            if adapter in self.DECLARED_SIMULATION_ADAPTERS:
                continue          # simulation was asked for; nothing is wrong
            if not adapter:
                reason = "no_adapter_declared"
            elif adapter not in known:
                reason = "adapter_unavailable"
            else:
                continue
            found.append({"device_id": device.device_id, "reason": reason,
                          "adapter": adapter, "actuators": len(actuators)})
        if found:
            log.warning(
                "%d registered device(s) declare actuators that nothing can "
                "execute — their actions will be simulated: %s",
                len(found), ", ".join(f"{d['device_id']} ({d['reason']})"
                                      for d in found))
        return found

    def _warn_if_unexecutable(self, manifest) -> None:
        """Say so at registration when nothing can carry out this device's actions.

        A device that declares actuators and names no adapter is registered,
        resolved, selected and reported as acting — and every one of its actions
        is simulated. That is discoverable at the moment of registration and was
        being discovered, when at all, by an operator reading a log months
        later. The reference deployment's SMS notifier was in exactly this state.

        A warning, not a rejection: registering a device before its adapter is
        installed is a legitimate order of operations, and the protocol does not
        get to refuse a manifest it merely cannot serve yet.
        """
        actuators = list(getattr(manifest, "actuators", []) or [])
        if not actuators:
            return
        adapter = getattr(manifest, "adapter", None)
        if adapter in self.DECLARED_SIMULATION_ADAPTERS:
            return              # simulation was asked for; nothing is wrong
        executor = getattr(self, "executor", None)
        known = getattr(executor, "_adapters", None)
        if not adapter:
            log.warning(
                "%s declares %d actuator(s) and no adapter — its actions will be "
                "simulated, not executed. Set 'adapter' on the manifest.",
                manifest.device_id, len(actuators))
        elif isinstance(known, dict) and adapter not in known:
            log.warning(
                "%s declares adapter '%s', which is not registered — its actions "
                "will be simulated, not executed.", manifest.device_id, adapter)

    # ── Event handling (device → AI) ─────────────────────────────────────────

    def on_event(self, handler: Callable[[DeviceEvent], None]) -> None:
        self._event_handlers.append(handler)

    async def receive_event(self, event: DeviceEvent) -> None:
        log.info("Event received: %s from %s [%s]",
                 event.event_id, event.device_id, event.severity.value)

        self.audit_log.append({
            "type":      "device_event",
            "device_id": event.device_id,
            "event_id":  event.event_id,
            "severity":  event.severity.value,
            "data":      event.data,
        })

        for handler in self._event_handlers:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)

    # ── Phased intent execution ──────────────────────────────────────────────

    async def execute_phased(
        self,
        plan: PhasedActionPlan,
        executor: "DeviceExecutor",
    ) -> list[IntentResult]:
        """
        Runs a PhasedActionPlan: each phase in parallel, the phases in
        sequence, with a delay between them.
        Suited to emergencies where ordering matters.
        """
        all_results = []

        for i, phase in enumerate(plan.phases):
            log.info(
                "Executing phase %d/%d: '%s' (%d actions)",
                i + 1, len(plan.phases), phase.name, len(phase.actions),
            )

            from .models import ActionPlan as _PhAP, DeviceAction as _PhDA
            from .models import Intent as _PhI, IntentClass as _PhIC
            phase_plan = _PhAP(
                intent_id=f"{plan.intent_id}-phase{i+1}",
                actions=[_PhDA(device_id=a.device_id, action=a.action, params=a.params)
                         for a in phase.actions],
                urgency=plan.urgency,
                failure_policy=getattr(plan, "failure_policy", None),
                max_retries=getattr(plan, "max_retries", 1),
            )
            phase_intent = _PhI(intent=_PhIC("report_status"), urgency=plan.urgency, context={})
            p_res, p_fail, p_abort, p_pol = await self._execute_with_policy(
                phase_plan, executor, phase_intent
            )
            p_ok = len(p_fail) == 0 and len(p_abort) == 0
            p_st = "success" if p_ok else ("partial_abort" if p_abort else "partial")
            phase_result = IntentResult(
                intent_id=f"{plan.intent_id}-phase{i+1}",
                success=p_ok,
                results=p_res,
                failed_devices=p_fail,
                aborted_devices=p_abort,
                failure_policy_applied=p_pol,
                status=p_st,
            )
            all_results.append(phase_result)
            self.audit_log.append({
                "type":           "phase_executed",
                "intent_id":      plan.intent_id,
                "phase":          phase.name,
                "phase_num":      i + 1,
                "actions":        len(phase.actions),
                "failed":         p_fail,
                "aborted":        p_abort,
                "failure_policy": p_pol,
                "success":        p_ok,
            })
            # ABORT propagation: cancel remaining phases if this one failed
            if not p_ok and getattr(plan, "failure_policy", None) and \
                    getattr(plan.failure_policy, "value", "") == "abort":
                log.warning("ABORT: phase %d/%d failed — cancelling %d remaining",
                    i+1, len(plan.phases), len(plan.phases)-i-1)
                for rp in plan.phases[i+1:]:
                    all_results.append(IntentResult(
                        intent_id=f"{plan.intent_id}-phase{plan.phases.index(rp)+1}",
                        success=False, results=[], failed_devices=[],
                        aborted_devices=[a.device_id for a in rp.actions],
                        failure_policy_applied="abort", status="partial_abort",
                    ))
                break

            if phase.delay_after_ms > 0 and i < len(plan.phases) - 1:
                log.info("Waiting %dms before next phase...", phase.delay_after_ms)
                await asyncio.sleep(phase.delay_after_ms / 1000)

        return all_results

