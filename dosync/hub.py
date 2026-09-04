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
from typing import Callable, Optional

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



class CapabilityRegistry:
    """
    Stores device manifests and answers capability queries.
    In production this would persist to disk / SQLite.
    """

    def __init__(self):
        self._devices: dict[str, CapabilityManifest] = {}
        self._listeners: list[Callable] = []
        # Inverted tag index for O(1) device lookup by tag.
        # Maps tag -> set of device_ids that declare that tag.
        self._tag_index: dict[str, set[str]] = {}
        # Emergency-capable device ids for O(1) emergency lookup
        self._emergency_ids: set[str] = set()

    def register(self, manifest: CapabilityManifest) -> None:
        old = self._devices.get(manifest.device_id)
        if old:
            for tag in old.tags:
                self._tag_index.get(tag, set()).discard(manifest.device_id)
            self._emergency_ids.discard(manifest.device_id)
        self._devices[manifest.device_id] = manifest
        for tag in manifest.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(manifest.device_id)
        if manifest.emergency_capable:
            self._emergency_ids.add(manifest.device_id)
        log.info("Registered device: %s (%s)", manifest.device_id, manifest.device_name)
        for cb in self._listeners:
            cb(manifest)

    def unregister(self, device_id: str) -> None:
        manifest = self._devices.pop(device_id, None)
        if manifest:
            for tag in manifest.tags:
                self._tag_index.get(tag, set()).discard(device_id)
            self._emergency_ids.discard(device_id)
        log.info("Unregistered device: %s", device_id)

    def get(self, device_id: str) -> Optional[CapabilityManifest]:
        return self._devices.get(device_id)

    def all(self) -> list[CapabilityManifest]:
        """Every registered device, including quarantined ones.

        This is INVENTORY: what the hub knows about. Status pages, exports and
        audits want this — a device the operator can no longer act on is still a
        thing they need to see, and hiding it is how it gets forgotten.
        """
        return list(self._devices.values())

    def active(self) -> list[CapabilityManifest]:
        """Devices eligible to PARTICIPATE in an intent.

        Separate from `all()` because the two questions are different and were
        being answered by one method. A device whose declarative file was
        deleted is still in the inventory — the operator must see it to decide —
        but it must not be planned into an emergency, because the operator
        already believes it is gone.

        Quarantine is deliberately not deletion: a directory that failed to
        mount looks exactly like a directory whose files were removed, and a hub
        that reacts to the first by deregistering a building is worse than one
        that asks.
        """
        return [m for m in self._devices.values() if not is_quarantined(m)]

    def find_by_tags(self, tags: list[str]) -> list[CapabilityManifest]:
        """Return devices that have at least one of the given tags.
        O(|tags| + |candidates|) with the inverted index, not O(n).
        """
        candidate_ids: set[str] = set()
        for tag in tags:
            candidate_ids |= self._tag_index.get(tag, set())
        return [self._devices[did] for did in candidate_ids if did in self._devices]

    def find_by_required_tags(self, required_tags: set[str]) -> list[CapabilityManifest]:
        """Return devices that have ALL of the required tags (intersection index).
        O(|result|) — starts with the smallest tag set and intersects progressively.
        Significantly faster than union-based lookup when tags are specific.
        """
        if not required_tags:
            return self.all()
        # Sort by set size ascending — smallest set first minimizes intersection cost
        sets = sorted(
            [self._tag_index.get(t, set()) for t in required_tags],
            key=len,
        )
        result_ids = sets[0].copy()
        for s in sets[1:]:
            result_ids &= s
            if not result_ids:
                break
        return [self._devices[did] for did in result_ids if did in self._devices]

    def find_emergency_capable(self) -> list[CapabilityManifest]:
        """Return emergency-capable devices. O(|emergency_devices|) with index."""
        return [self._devices[did] for did in self._emergency_ids if did in self._devices]

    def find_by_actuator(self, actuator_type: str) -> list[CapabilityManifest]:
        return [
            d for d in self._devices.values()
            if any(a.type == actuator_type for a in d.actuators)
        ]

    def on_register(self, cb: Callable) -> None:
        self._listeners.append(cb)

# ── Audit log ─────────────────────────────────────────────────────────────────
#
# Moved to dosync/audit.py. Re-exported here so that every existing import —
# manage.py, audit_backup.py, the tests — keeps working unchanged: an
# extraction that forces callers to move at the same time is a rewrite
# wearing an extraction's clothes.

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
        # v13 hygiene (parada técnica 2026-07-21, Paredes): progress_cb failures
        # are swallowed so an observer can't break execution — but swallowed !=
        # invisible. Count them so a real callback bug surfaces in /v1/status
        # instead of hiding in debug logs forever.
        self.progress_cb_failures: int = 0
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
        self._checkpoints   = CheckpointKeeper(self.db, self.audit_log)
        # Load persisted state now that db is ready
        if hasattr(self, "resolver"):
            self.resolver._load_state_from_db()
        self.health.load_from_db()
        self.audit_log._persist_cb = self.db.append_audit
        self._restore_from_db()

    # ── Family profile ───────────────────────────────────────────────────────

    # ── DB restore ──────────────────────────────────────────────────────────





    async def start_state_refresh(
        self,
        executor: "DeviceExecutor",
        interval: float = None,
    ) -> None:
        """Hub-owned background state refresher — ACTIVE device health probing.

        Periodically queries get_state() on every device whose adapter supports
        it, WITHOUT executing any action. Two effects:
          * hub.health.mark_reachable() on every responder — this is the active
            probing that makes recovery detectable within one interval, instead
            of waiting for the unreachable TTL to lapse or for some action to
            happen to succeed.
          * the resolver's own state cache is refreshed if it keeps one
            (StateAwareResolver.update_state), preserving redundancy detection
            for deployments that use it.

        Until 2026-07-14 this lived on StateAwareResolver and server.py started
        it behind `isinstance(hub.resolver, StateAwareResolver)` — always False
        in production (which runs ExternalResolver), so it NEVER ran, silently,
        behind a log.debug. State refresh is a hub concern, not a resolver's:
        the same reasoning that moved device health to the hub.

        POSITIVE SIGNAL ONLY, by deliberate design (preserved from the original):
        a device that does not answer get_state() is skipped, NOT marked
        unreachable. A failing get_state is weaker evidence than an action
        timing out (adapters implement it unevenly), and marking on weak
        evidence would manufacture false "dead device" reports.

        Args:
            executor: AdapterExecutor to source adapters from
            interval: seconds between cycles. Defaults to the
                      DOSYNC_STATE_REFRESH_INTERVAL env var (default 60).
        """
        if interval is None:
            interval = float(os.environ.get("DOSYNC_STATE_REFRESH_INTERVAL", "60"))

        log.info("Hub: background state refresh started (interval=%.0fs)", interval)

        while True:
            try:
                await asyncio.sleep(interval)
                await self._state_refresh_cycle(executor)
            except asyncio.CancelledError:
                log.info("Hub: background state refresh stopped")
                break
            except Exception as e:
                log.warning("Hub: state refresh cycle error: %s", e)

    async def _state_refresh_cycle(self, executor: "DeviceExecutor") -> None:
        """One refresh cycle — probe every device whose adapter supports get_state()."""
        from .adapters import AdapterExecutor
        if not isinstance(executor, AdapterExecutor):
            return

        refreshed = 0
        skipped = 0
        recovered: list[str] = []

        for device in self.registry.all():
            adapter = executor.get_adapter(device.adapter)
            if adapter is None or not hasattr(adapter, "get_state"):
                skipped += 1
                continue

            try:
                state = await asyncio.wait_for(
                    adapter.get_state(device.device_id), timeout=3.0)
            except Exception:
                skipped += 1          # positive-signal only: no unreachable mark
                continue

            if state is None:
                skipped += 1
                continue

            # The device answered: it is reachable, right now, without us acting.
            was_unreachable = self.health.is_unreachable(device.device_id)
            self.health.mark_reachable(device.device_id)
            if was_unreachable:
                recovered.append(device.device_id)

            # Keep a state-caching resolver coherent, if one is plugged in.
            _update = getattr(self.resolver, "update_state", None)
            if callable(_update):
                try:
                    _update(device.device_id, state)
                except Exception as e:
                    log.debug("Hub: resolver update_state failed for %s: %s",
                              device.device_id, e)
            _clear = getattr(self.resolver, "clear_unreachable", None)
            if was_unreachable and callable(_clear):
                try:
                    _clear(device.device_id)
                except Exception:
                    pass
            refreshed += 1

        for device_id in recovered:
            log.info("Hub: %s back online (detected by state refresh)", device_id)
        if refreshed:
            log.debug("Hub: state refresh done — %d probed, %d skipped, %d recovered",
                      refreshed, skipped, len(recovered))

    def _restore_from_db(self) -> None:
        """
        Restore state from SQLite when the hub starts.
        Los dispositivos, perfil y audit log sobreviven reinicios.
        """
        from .models import (
            ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
            ContextSignalType, DeviceCategory, EventSpec, SensorSpec, Severity,
        )

        # Restore devices
        for manifest_dict in self.db.load_devices():
            try:
                # Rebuild CapabilityManifest from persisted dict
                caps = manifest_dict.get("capabilities", {})

                sensors = [
                    SensorSpec(
                        id=s["id"], type=s["type"],
                        description=s.get("description", ""),
                        unit=s.get("unit"),
                        poll_interval_ms=s.get("poll_interval_ms", 30000),
                        kind=s.get("kind", "environment"),   # legacy manifests default
                    )
                    for s in caps.get("sensors", [])
                ]
                actuators = [
                    ActuatorSpec(
                        id=a["id"], type=a["type"],
                        description=a.get("description", ""),
                    )
                    for a in caps.get("actuators", [])
                ]
                events = [
                    EventSpec(
                        id=e["id"],
                        severity=Severity(e.get("severity", "info")),
                        description=e.get("description", ""),
                    )
                    for e in caps.get("events", [])
                ]
                context_signals = [
                    ContextSignal(
                        type=ContextSignalType(c["type"]),
                        description=c.get("description", ""),
                        confidence_weight=c.get("confidence_weight", 1.0),
                    )
                    for c in caps.get("context_signals", [])
                ]

                manifest = CapabilityManifest(
                    device_id=manifest_dict["device_id"],
                    device_name=manifest_dict["device_name"],
                    manufacturer=manifest_dict["manufacturer"],
                    model=manifest_dict["model"],
                    firmware=manifest_dict["firmware"],
                    category=DeviceCategory(manifest_dict["category"]),
                    tags=manifest_dict["tags"],
                    sensors=sensors,
                    actuators=actuators,
                    events=events,
                    context_signals=context_signals,
                    emergency_capable=manifest_dict.get("emergency_capable", False),
                    cert_tier=CertTier(manifest_dict["cert_tier"]) if manifest_dict.get("cert_tier") else None,
                )
                # Restore adapter fields — critical for physical device control
                if manifest_dict.get("adapter"):
                    manifest.adapter        = manifest_dict["adapter"]
                    manifest.adapter_config = manifest_dict.get("adapter_config", {})
                self.registry.register(manifest)
            except Exception as e:
                log.warning("Could not restore device %s: %s",
                            manifest_dict.get("device_id", "?"), e)

        # Restore audit log. If older entries were archived to a segment file,
        # the chain starts at the stored anchor, not at genesis — both the
        # append continuity (_prev_hash) and verification (anchor_prev_hash)
        # must honor it.
        _anchor = self.db.get_audit_anchor()
        if _anchor:
            self.audit_log.anchor_prev_hash = _anchor.get("anchor_prev_hash", "0" * 64)
            self.audit_log._prev_hash = self.audit_log.anchor_prev_hash
        for entry in self.db.load_audit_log():
            self.audit_log._entries.append(entry)
            self.audit_log._prev_hash = entry.get("hash", "0" * 64)
        # Continue the sequence rather than restart it: a restart must not make
        # two entries share a number, nor hand out a number BELOW one already
        # used. Take the highest `seq` present, not the row count — after
        # archiving, the surviving entries keep their original high numbers
        # while the row count is small, and counting rows would wind the series
        # backwards. Chains written before sequence numbers have none at all, so
        # the row count is the right starting point only in that case.
        _seqs = [e["seq"] for e in self.audit_log._entries if e.get("seq") is not None]
        self.audit_log._next_seq = (max(_seqs) + 1) if _seqs \
            else len(self.audit_log._entries)
        self.audit_log._checkpoint_cb = self.db.set_audit_head

        # Restore family profile. Until 2026-07-14 this was MISSING: the profile
        # was persisted by set_family_profile() and db.load_family_profile()
        # existed, but nothing ever called it — so every restart silently dropped
        # the profile while this method's docstring promised it survives.
        try:
            profile_dict = self.db.load_family_profile()
            if profile_dict:
                from .models import FamilyProfile
                self.family_profile = FamilyProfile.from_dict(profile_dict)
                log.info("Restored family profile: %s", self.family_profile.family_name)
        except Exception as e:
            log.warning("Could not restore family profile: %s", e)

        # Restore presence signals
        from .models import PresenceSignal
        for signal_dict in self.db.load_presence_signals():
            try:
                signal = PresenceSignal(
                    device_id=signal_dict["device_id"],
                    signal_type=ContextSignalType(signal_dict["signal_type"]),
                    present=signal_dict["present"],
                    confidence=signal_dict["confidence"],
                    member_id=signal_dict.get("member_id"),
                    timestamp=signal_dict.get("timestamp", time.time()),
                )
                self.occupancy._signals.append(signal)
            except Exception as e:
                log.warning("Could not restore presence signal: %s", e)

        log.info(
            "Hub restored: %d device(s), %d audit entries",
            len(self.registry.all()),
            len(self.audit_log.entries()),
        )


    # ── Checkpoints ───────────────────────────────────────────────────────────
    #
    # The work is in CheckpointKeeper. These stay because server.py and the
    # audit tests call them on the hub, and an extraction that forces its
    # callers to move is a rewrite with better manners.

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

    async def _execute_with_policy_cb(self, plan, executor, intent, progress_cb=None):
        """MCP-V13: wrap the executor so each completed action can be published as
        partial progress WITHOUT changing any strategy signature. The wrapper
        fires progress_cb(result) as each action resolves; the strategies below
        are untouched. progress_cb is best-effort — a failing callback must never
        affect execution (an observer cannot break the observed)."""
        if progress_cb is not None:
            _inner = executor
            _hub = self

            class _ProgressExecutor:
                def __getattr__(self, n): return getattr(_inner, n)
                async def execute(self, action, urgency):
                    r = await _inner.execute(action, urgency)
                    try:
                        progress_cb(r)
                    except Exception as _cb_e:
                        _hub.progress_cb_failures += 1
                        log.warning("progress_cb raised (ignored, count=%d): %s",
                                    _hub.progress_cb_failures, _cb_e)
                    return r
            executor = _ProgressExecutor()
        return await self._execute_with_policy(plan, executor, intent)

    async def _execute_with_policy(self, plan, executor, intent):
        """Dispatch to the correct execution strategy based on failure_policy.
        Emergency intents always force CONTINUE — protocol-level guarantee."""
        from .models import FailurePolicy, Urgency
        policy = plan.failure_policy or FailurePolicy.CONTINUE
        if intent.urgency == Urgency.EMERGENCY:
            if policy == FailurePolicy.ABORT:
                log.info("FailurePolicy.ABORT overridden to CONTINUE for emergency '%s'", intent.intent)
            policy = FailurePolicy.CONTINUE
        if policy == FailurePolicy.ABORT:
            r, f, a = await self._execute_abort(plan.actions, executor, intent)
            return r, f, a, "abort"
        elif policy == FailurePolicy.RETRY:
            max_r = plan.max_retries if plan.max_retries else 1
            if intent.urgency == Urgency.EMERGENCY:
                max_r = 1
            r, f, a = await self._execute_retry(plan.actions, executor, intent, max_r)
            return r, f, a, "retry"
        else:
            r, f, a = await self._execute_parallel(plan.actions, executor, intent)
            return r, f, a, "continue"

    async def _execute_parallel(self, actions, executor, intent):
        """CONTINUE: execute all actions in parallel, failures never stop execution."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))
        tasks = {asyncio.ensure_future(executor.execute(a, intent.urgency)): a for a in actions}
        results = []
        if tasks:
            done, pending = await asyncio.wait(tasks.keys(), timeout=_t)
            for fut in done:
                results.append(fut.result())
            for fut in pending:
                action = tasks[fut]
                log.warning("Timeout: %s/%s after %.1fs", action.device_id, action.action, _t)
                self.health.mark_unreachable(action.device_id)
                if hasattr(self.resolver, "mark_unreachable"):
                    self.resolver.mark_unreachable(action.device_id)
                results.append(ActionResult(device_id=action.device_id, action=action.action,
                                            success=False, error=f"timeout after {_t}s"))
                fut.cancel()
        return results, [r.device_id for r in results if not r.success], []

    async def _execute_abort(self, actions, executor, intent):
        """ABORT: execute in batches by relevance_score. Cancel remaining if any batch fails."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))
        sorted_actions = sorted(actions, key=lambda a: a.relevance_score, reverse=True)
        batches = [sorted_actions[i:i+5] for i in range(0, len(sorted_actions), 5)]
        all_results, aborted = [], []
        abort_triggered = False
        for idx, batch in enumerate(batches):
            if abort_triggered:
                for a in batch:
                    aborted.append(a.device_id)
                    all_results.append(ActionResult(device_id=a.device_id, action=a.action,
                        success=False, error="aborted — prior batch failed", aborted=True))
                continue
            tasks = {asyncio.ensure_future(executor.execute(a, intent.urgency)): a for a in batch}
            done, pending = await asyncio.wait(tasks.keys(), timeout=_t)
            batch_results = [fut.result() for fut in done]
            for fut in pending:
                a = tasks[fut]
                self.health.mark_unreachable(a.device_id)
                if hasattr(self.resolver, "mark_unreachable"):
                    self.resolver.mark_unreachable(a.device_id)
                batch_results.append(ActionResult(device_id=a.device_id, action=a.action,
                    success=False, error=f"timeout after {_t}s"))
                fut.cancel()
            all_results.extend(batch_results)
            if any(not r.success for r in batch_results):
                log.warning("ABORT triggered after batch %d/%d — failures: %s",
                    idx+1, len(batches), [r.device_id for r in batch_results if not r.success])
                abort_triggered = True
        failed = [r.device_id for r in all_results if not r.success and not r.aborted]
        return all_results, failed, aborted

    async def _execute_retry(self, actions, executor, intent, max_retries):
        """RETRY: retry each failed action up to max_retries with exponential backoff."""
        import os as _os
        from .models import ActionResult
        _t = float(_os.environ.get("DOSYNC_INTENT_TIMEOUT",
                   "5.0" if intent.urgency.value == "emergency" else "10.0"))

        async def _with_retry(action):
            last = None
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    log.info("RETRY %d/%d for %s (backoff %.1fs)", attempt, max_retries,
                             action.device_id, backoff)
                    await asyncio.sleep(backoff)
                try:
                    r = await asyncio.wait_for(executor.execute(action, intent.urgency), timeout=_t)
                    r.retries = attempt
                    if r.success:
                        return r
                    last = r
                except asyncio.TimeoutError:
                    self.health.mark_unreachable(action.device_id)
                    if hasattr(self.resolver, "mark_unreachable"):
                        self.resolver.mark_unreachable(action.device_id)
                    last = ActionResult(device_id=action.device_id, action=action.action,
                        success=False, error=f"timeout (attempt {attempt+1}/{max_retries+1})",
                        retries=attempt)
            log.warning("RETRY exhausted for %s after %d attempt(s)", action.device_id, max_retries+1)
            return last or ActionResult(device_id=action.device_id, action=action.action,
                success=False, error=f"exhausted {max_retries} retries", retries=max_retries)

        results = list(await asyncio.gather(*[_with_retry(a) for a in actions]))
        return results, [r.device_id for r in results if not r.success], []

    def _validate_plan_params(self, plan, intent):
        """Validate each action's params against its actuator's JSON Schema.

        Returns (filtered_plan, rejected). Valid actions stay in the plan; each
        invalid action is dropped and recorded — both in the returned `rejected`
        list and in the audit log — so nothing is silently discarded.

        Rejection here means "the mind asked for something the actuator declared
        it does not accept" — distinct from a device that fails to respond at
        execution time. The audit entry type makes that distinction explicit.
        """
        from .models import ActionPlan as _AP
        from .validation import validate_params

        valid, rejected = [], []
        for action in plan.actions:
            device = self.registry.get(action.device_id)
            schema = None
            if device:
                for act in device.actuators:
                    if act.type == action.action:
                        schema = getattr(act, "params_schema", None)
                        break
            # No device or no schema for this action → nothing to validate against.
            if not schema:
                valid.append(action)
                continue

            ok, err = validate_params(schema, action.params or {})
            if ok:
                valid.append(action)
            else:
                rejected.append((action, err))
                log.warning(
                    "Action rejected by param validation: %s.%s — %s",
                    action.device_id, action.action, err,
                )
                self.audit_log.append({
                    "type":      "action_rejected_invalid_params",
                    "intent_id": intent.intent_id,
                    "intent":    intent.intent.value,
                    "device_id": action.device_id,
                    "action":    action.action,
                    "params":    action.params,
                    "reason":    err,
                    "source":    getattr(intent, "source", "api"),
                })

        filtered = _AP(intent_id=plan.intent_id, actions=valid, urgency=plan.urgency,
                       failure_policy=getattr(plan, "failure_policy", None),
                       max_retries=getattr(plan, "max_retries", 1))
        return filtered, rejected

    # ── Long-running operations: split + write-ahead (execution_model) ────────
    # This is a SELF-CONTAINED sub-protocol layered on top of intent execution.
    # The instant path (every existing action) is untouched: these helpers only
    # ever handle actions whose actuator declares execution_model == "long_running".
    # An implementer in another language can read this block as one unit.

    def _action_execution_model(self, action: "DeviceAction") -> tuple[str, bool]:
        """Return (execution_model, emits_telemetry) for an action by looking up
        its actuator in the device manifest. Defaults to ("instant", False) when the
        device/actuator is unknown — an unknown action is treated as instant, never
        long-running, so a missing manifest can never strand an operation."""
        manifest = self.registry.get(action.device_id)
        if not manifest:
            return ("instant", False)
        for act in manifest.actuators:
            if act.type == action.action:
                return (getattr(act, "execution_model", "instant"),
                        getattr(act, "emits_telemetry", False))
        return ("instant", False)

    def _split_plan_by_execution_model(self, plan):
        """Split a plan's actions into (instant_actions, long_running_actions).
        Each action goes to exactly one list — no action is ever in both."""
        instant, long_running = [], []
        for action in plan.actions:
            model, _ = self._action_execution_model(action)
            (long_running if model == "long_running" else instant).append(action)
        return instant, long_running

    async def _start_long_running_actions(self, long_running_actions, executor, intent):
        """For each long-running action: WRITE-AHEAD (create + persist the operation
        in `pending` BEFORE dispatching), then dispatch, then transition by the
        dispatch result. Returns a list of {operation_id, device_id, state} for the
        IntentResult. Never blocks waiting for the action to finish — it only starts.

        Panel rules honored here:
          - write-ahead: persist `pending` before dispatch, so a crash mid-dispatch
            never leaves a running device with no operation record.
          - silence != success: a successful DISPATCH means the device ACCEPTED the
            command, not that it finished. For a telemetry device the operation waits
            in `in_progress` for telemetry to confirm/advance; for a core device
            (no telemetry) the successful dispatch is the only signal there will be,
            so it goes to `in_progress` and a later positive signal completes it.
          - graceful degradation: an adapter that doesn't cooperate just returns a
            normal ActionResult; the operation is resolved from it. No adapter is
            required to understand operations.
        """
        from .operations import Operation, OperationState

        started = []
        for action in long_running_actions:
            _, emits_telemetry = self._action_execution_model(action)
            op = Operation(
                device_id=action.device_id,
                action=action.action,
                telemetry_capable=emits_telemetry,
            )
            # WRITE-AHEAD: persist in `pending` before we touch the device.
            self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
            self.audit_log.append({
                "type":         "operation_created",
                "operation_id": op.operation_id,
                "intent_id":    intent.intent_id,
                "device_id":    action.device_id,
                "action":       action.action,
                "state":        op.state.value,
            })

            # Dispatch (start the action). We do NOT wait for it to finish.
            try:
                result = await executor.execute(action, intent.urgency)
                dispatch_ok = bool(getattr(result, "success", False))
                err = getattr(result, "error", None)
            except Exception as e:  # an adapter blowing up must not strand the op
                dispatch_ok = False
                err = str(e)

            if dispatch_ok:
                # Accepted by the device. NOT completed — started.
                op.transition_to(OperationState.IN_PROGRESS,
                                 reason="dispatch accepted by device")
            else:
                # The device refused / dispatch failed → the operation never ran.
                op.transition_to(OperationState.FAILED,
                                 reason=f"dispatch failed: {err}" if err else "dispatch failed")

            self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
            self.audit_log.append({
                "type":         "operation_transition",
                "operation_id": op.operation_id,
                "intent_id":    intent.intent_id,
                "from_state":   "pending",
                "to_state":     op.state.value,
            })
            started.append({
                "operation_id": op.operation_id,
                "device_id":    action.device_id,
                "state":        op.state.value,
            })
        return started

    def apply_telemetry(self, device_id: str, event, reason: str = "",
                        phase: str = None, now: float = None) -> dict:
        """Apply one telemetry fact to a device's active operation.

        This is the GENERIC bridge between any telemetry-emitting adapter and the
        operation state machine — the first thing in the hub to actually drive the
        reconciler. It is deliberately device-agnostic: it speaks only the abstract
        TelemetryEvent vocabulary, knows nothing of MAVLink, drones, or flight. An
        oven that one day emits telemetry would call this identically. The adapter's
        job is to translate its device-native signal into a TelemetryEvent; the
        hub's job (here) is to find the operation, reconcile, persist, and audit.

        Flow:
          1. Find the device's single active (non-terminal) operation.
          2. Rehydrate it faithfully from the DB (preserving time_in_state/history).
          3. Let the stateless reconciler apply the fact (telemetry wins; illegal
             or duplicate facts are no-ops, never crashes).
          4. Persist the (possibly unchanged) operation and audit the outcome.

        Returns a small dict describing what happened — changed/no-op, the
        from/to states, and a note — so the caller (and tests) can see the result
        without re-reading the DB. Returns {"matched": False} when the device has
        no active operation (a stray telemetry packet for an idle device is not an
        error — it is simply ignored).

        Safety note: a telemetry fact is the ONLY thing that advances an operation
        toward completion. Silence never does. A disconnection is not a fact and
        must not be passed here as one — it does not advance anything.
        """
        from .operations import Operation
        from .reconciler import OperationReconciler, TelemetryEvent

        if isinstance(event, str):
            event = TelemetryEvent(event)

        # 1. Find the device's active operation. There should be at most one
        #    non-terminal operation per device at a time; if several exist (a
        #    pathological state), reconcile the most recent, which get_active_
        #    operations returns first (ORDER BY created_at DESC).
        active = [o for o in self.db.get_active_operations()
                  if o.get("device_id") == device_id]
        if not active:
            log.debug("apply_telemetry: no active operation for %s (event '%s' ignored)",
                      device_id, event.value)
            return {"matched": False, "device_id": device_id, "event": event.value}

        op_dict = active[0]
        op = Operation.from_dict(op_dict)

        # Carry an updated sub-phase if the adapter reported one (e.g. "arming").
        # The protocol never interprets this string; it is domain detail.
        if phase is not None:
            op.phase = phase

        # 2 + 3. Reconcile — pure logic, decides the state change (or no-op).
        reconciler = OperationReconciler()
        result = reconciler.reconcile(op, event, reason=reason, now=now)

        # 4. Persist (even a no-op may have updated `phase`) and audit.
        self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
        self.audit_log.append({
            "type":         "operation_telemetry",
            "operation_id": op.operation_id,
            "device_id":    device_id,
            "event":        event.value,
            "changed":      result.changed,
            "from_state":   result.from_state.value,
            "to_state":     result.to_state.value,
            "note":         result.note,
        })

        return {
            "matched":      True,
            "operation_id": op.operation_id,
            "device_id":    device_id,
            "event":        event.value,
            "changed":      result.changed,
            "from_state":   result.from_state.value,
            "to_state":     result.to_state.value,
            "note":         result.note,
        }

    # ── Composite intents (Level 2: the nervous system) ───────────────────────
    # A composition intent (e.g. inspect_area) does not resolve to a flat parallel
    # ActionPlan. It composes an ORDERED SEQUENCE of atomic operations that the
    # OperationSupervisor drives in a closed loop — dispatch a step, wait for its
    # confirmed arrival via reconciled telemetry, then the next, reacting to guards
    # each tick. This is the brain coordinating the body, not fire-and-forget.
    #
    # The pieces (all built and tested independently):
    #   RouteComposer        -> the ordered steps (geometry)
    #   CompositeOperation   -> the structure that holds them
    #   OperationSupervisor  -> the closed-loop driver
    #   OperationGuards      -> real-time in-flight monitoring
    # This method wires them to the live hub (PolicyEngine + executor + telemetry).

    async def _dispatch_composite_step(self, step, executor, intent):
        """Dispatch ONE composite step as a long-running atomic operation, AFTER the
        PolicyEngine admits it. Returns the operation_id the supervisor will watch.

        CRITICAL (panel): the composition path does NOT bypass admission security.
        Every step is evaluated by the PolicyEngine first — the admission geofence,
        rate limits, manual-control lockout, everything — exactly as a normal intent
        is. A blocked step raises, the supervisor sees the failed dispatch and aborts.

        Reuses the write-ahead pattern of _start_long_running_actions: persist the
        operation in `pending` BEFORE touching the device, dispatch, transition by the
        result. The operation then advances to `completed` later, when telemetry
        confirms arrival (apply_telemetry) — which is what the supervisor polls for.
        """
        from .operations import Operation, OperationState
        from .models import DeviceAction, ActionPlan as _AP

        action = DeviceAction(
            device_id=step.device_id, action=step.action, params=dict(step.params or {}))

        # ── PolicyEngine admission for THIS step ──────────────────────────────
        if self.policy_engine:
            from .policies import PolicyDecision
            single_plan = _AP(intent_id=intent.intent_id, actions=[action],
                              urgency=intent.urgency)
            result = self.policy_engine.evaluate(intent, single_plan)
            if result.decision == PolicyDecision.BLOCK:
                self.audit_log.append({
                    "type":      "composite_step_blocked",
                    "intent_id": intent.intent_id,
                    "device_id": step.device_id,
                    "action":    step.action,
                    "policy":    result.policy_name,
                    "reason":    result.reason,
                })
                raise PermissionError(
                    f"step '{step.action}' blocked by policy '{result.policy_name}': "
                    f"{result.reason}")
            if result.decision == PolicyDecision.MODIFY and result.modified_actions:
                action = result.modified_actions[0]

        # ── Write-ahead: persist `pending` before dispatch ───────────────────
        _, emits_telemetry = self._action_execution_model(action)
        op = Operation(device_id=action.device_id, action=action.action,
                       telemetry_capable=emits_telemetry)
        self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
        self.audit_log.append({
            "type": "operation_created", "operation_id": op.operation_id,
            "intent_id": intent.intent_id, "device_id": action.device_id,
            "action": action.action, "state": op.state.value,
            "composite": True,
        })

        # ── Dispatch (start the step). Do NOT wait for it to finish here ──────
        try:
            res = await executor.execute(action, intent.urgency)
            dispatch_ok = bool(getattr(res, "success", False))
            err = getattr(res, "error", None)
        except Exception as e:
            dispatch_ok, err = False, str(e)

        if dispatch_ok:
            op.transition_to(OperationState.IN_PROGRESS, reason="dispatch accepted")
        else:
            op.transition_to(OperationState.FAILED,
                             reason=f"dispatch failed: {err}" if err else "dispatch failed")
        self.db.save_operation(op.to_dict(), terminal=op.is_terminal)
        self.audit_log.append({
            "type": "operation_transition", "operation_id": op.operation_id,
            "intent_id": intent.intent_id, "from_state": "pending",
            "to_state": op.state.value, "composite": True,
        })
        return op.operation_id

    def _read_operation_state(self, operation_id):
        """Read the reconciled atomic state of an operation (the state telemetry
        keeps current). The supervisor polls THIS — never the raw telemetry queue."""
        row = self.db.get_operation(operation_id)
        return row.get("state") if row else None

    def _build_guard_context_provider(self, device_id):
        """Build the GuardContext provider the supervisor calls each tick.

        HONEST SCOPE (panel): it fills what the CURRENT data allows and leaves the
        rest None. The guards abstain on None (verified), so a guard whose signal is
        not yet flowing simply does not fire — no dead code, no false promise.

        Active with real data TODAY:
          - manual_control_active: from the device's operation reaching `interrupted`
            (a human took control — apply_telemetry sets this from MANUAL_CONTROL_TAKEN)
          - seconds_in_step: from the active operation's time_in_state

        Waiting on continuous position/battery telemetry (guards already built+tested,
        activate automatically once the provider starts filling these):
          - lat/lon/alt  -> GeofenceGuard in flight
          - battery_percent -> BatteryGuard
        A future step publishes a per-device telemetry snapshot; until then these stay
        None and their guards correctly abstain.
        """
        from .operation_guards import GuardContext

        def provider(comp):
            manual = False
            seconds_in_step = None
            try:
                for op in self.db.get_active_operations():
                    if op.get("device_id") != device_id:
                        continue
                    if op.get("state") == "interrupted":
                        manual = True
                    entered = op.get("state_entered_at")
                    if entered is not None:
                        import time as _t
                        seconds_in_step = _t.time() - entered
            except Exception:
                pass  # never let context-building crash the supervisor
            return GuardContext(
                device_id=device_id,
                manual_control_active=manual,
                seconds_in_step=seconds_in_step,
                # lat/lon/alt/battery/seconds_since_telemetry: None until a
                # continuous telemetry snapshot feeds them (guards abstain on None).
            )
        return provider

    async def execute_composite_intent(self, intent, executor, context, guard_set=None,
                                       config=None):
        """Execute a composition intent (e.g. inspect_area) as a supervised, closed-
        loop sequence. Composes the route, builds the CompositeOperation, wires the
        supervisor to the live hub (PolicyEngine-gated dispatch, reconciled-state
        polling, guard provider), and runs it.

        Args:
          intent:   the composition Intent (its .context carries center/radius/etc.)
          executor: the device executor (dispatches atomic steps)
          context:  the resolution context for the composer (center, radius_m, ...)
          guard_set: an optional OperationGuards.GuardSet; if None, no guards beyond
                     the supervisor's own stall backstop.
          config:   optional SupervisorConfig (poll cadence, step timeout).

        Returns the terminal CompositeState.
        """
        from .route_composer import RouteComposer
        from .composite_operations import CompositeOperation
        from .operation_supervisor import OperationSupervisor

        device_id = context.get("device_id")
        if not device_id:
            raise ValueError("execute_composite_intent requires context['device_id']")

        # 1. Compose the ordered steps (geometry) — the cerebellum's translation.
        steps = RouteComposer().compose_inspect_area(device_id, context)

        # 2. The structure that holds and tracks them.
        comp = CompositeOperation(device_id=device_id, intent=intent.intent.value,
                                  steps=steps, context=context)
        self.audit_log.append({
            "type": "composite_started", "composite_id": comp.composite_id,
            "intent_id": intent.intent_id, "intent": intent.intent.value,
            "device_id": device_id, "steps": len(steps),
        })

        # 3. Wire the supervisor to the live hub.
        async def dispatch(step):
            return await self._dispatch_composite_step(step, executor, intent)

        guard_fn = None
        if guard_set is not None:
            guard_fn = guard_set.make_guard_fn(
                self._build_guard_context_provider(device_id))

        supervisor = OperationSupervisor(
            dispatch=dispatch,
            read_state=self._read_operation_state,
            guard=guard_fn,
            config=config,
        )

        # 4. Run the closed loop to a terminal mission state.
        final = await supervisor.run(comp)

        self.audit_log.append({
            "type": "composite_finished", "composite_id": comp.composite_id,
            "intent_id": intent.intent_id, "final_state": final.value,
            "steps_completed": sum(1 for s in comp.steps if s.done),
            "steps_total": len(comp.steps),
        })
        return final

    async def _route_composite_intent(self, intent, executor, composition_kind):
        """Route a composition intent to the right composer and run it as a closed-
        loop supervised sequence, wrapping the terminal CompositeState in the
        IntentResult the caller expects.

        The selection is a simple `if`, not a generic registry (panel: no premature
        abstraction with one composer). An UNKNOWN kind fails EXPLICITLY — a declared
        composition the hub cannot compose is a configuration error that must shout,
        never a silent fall-through to the flat path.

        The composition context (center, radius_m, device_id, ...) travels in
        intent.context — the same place every intent carries its context.
        """
        if composition_kind != "perimeter":
            # Explicit failure — never silently degrade to the flat path.
            log.error("Intent '%s' declares composition_kind='%s' but no composer "
                      "handles it.", intent.intent.value, composition_kind)
            self.audit_log.append({
                "type": "composite_unknown_kind",
                "intent_id": intent.intent_id,
                "intent": intent.intent.value,
                "composition_kind": composition_kind,
            })
            return IntentResult(
                intent_id=intent.intent_id, success=False, results=[],
                failed_devices=[], status="failed",
            )

        from .composite_operations import CompositeState
        try:
            final_state = await self.execute_composite_intent(
                intent, executor, context=intent.context)
        except ValueError as e:
            # e.g. missing device_id / center in the context.
            log.error("Composite intent '%s' rejected: %s", intent.intent.value, e)
            self.audit_log.append({
                "type": "composite_rejected",
                "intent_id": intent.intent_id,
                "intent": intent.intent.value,
                "reason": str(e),
            })
            return IntentResult(
                intent_id=intent.intent_id, success=False, results=[],
                failed_devices=[], status="failed",
            )

        # Map the terminal CompositeState to the IntentResult contract.
        success = final_state == CompositeState.COMPLETED
        return IntentResult(
            intent_id=intent.intent_id, success=success, results=[],
            failed_devices=[], status=final_state.value,
        )

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

    async def execute_intent(
        self,
        intent: Intent,
        executor: "DeviceExecutor",
        progress_cb=None,
    ) -> IntentResult:
        log.info("Executing intent: %s [%s]", intent.intent.value, intent.urgency.value)

        # Wrap the executor once to time every device action regardless of the
        # execution path (parallel/abort/retry/long-running). A timing wrapper
        # here means we don't have to instrument each call site — and it never
        # changes behavior: it awaits the real execute and records the elapsed
        # time by result. Metrics are optional; a failure to record is swallowed.
        if _M is not None and not getattr(executor, "_dosync_timed", False):
            executor = _TimedExecutor(executor, hub=self)

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


# ── Occupancy Engine ──────────────────────────────────────────────────────────

class OccupancyEngine:
    """
    Infers home occupancy state by aggregating signals from multiple
    context providers. Never relies on a single source — combines and weights them.

    Supported signals and their default weights:
      Phone GPS outside perimeter     → absence, weight 0.9
      Phone WiFi disconnected         → absence, weight 0.7
      No PIR motion for 30+ min       → absence, weight 0.4
      Smartwatch GPS outside perimeter → absence, weight 0.8
      Smart TV off                    → absence, weight 0.2
    """

    def __init__(self):
        self._signals: list[PresenceSignal] = []
        self._signal_ttl_seconds = 300      # signals expire after 5 minutes

    def update(self, signal: PresenceSignal) -> None:
        """Register or update a presence signal."""
        # Replace any previous signal from the same device
        self._signals = [
            s for s in self._signals
            if not (s.device_id == signal.device_id and
                    s.signal_type == signal.signal_type)
        ]
        self._signals.append(signal)
        log.info(
            "Presence signal: %s / %s → present=%s (confidence=%.2f)",
            signal.device_id, signal.signal_type.value,
            signal.present, signal.confidence,
        )

    def _active_signals(self) -> list[PresenceSignal]:
        """Filter out expired signals."""
        cutoff = time.time() - self._signal_ttl_seconds
        return [s for s in self._signals if s.timestamp >= cutoff]

    def get_occupancy(self) -> OccupancyState:
        """
        Calcula el estado de ocupacion actual.
        Returns occupied=True when weighted presence confidence >= 0.5.
        """
        signals = self._active_signals()
        if not signals:
            # No signals = unknown state; default to occupied for safety
            return OccupancyState(
                occupied=True,
                confidence=0.0,
                members_home=[],
                signals_used=0,
            )

        # Calculate weighted presence confidence
        total_weight = sum(s.confidence for s in signals)
        presence_weight = sum(
            s.confidence for s in signals if s.present
        )

        confidence_present = presence_weight / total_weight if total_weight > 0 else 0.5
        occupied = confidence_present >= 0.5

        members_home = list({
            s.member_id for s in signals
            if s.present and s.member_id
        })

        return OccupancyState(
            occupied=occupied,
            confidence=abs(confidence_present - 0.5) * 2,  # 0=incertidumbre, 1=certeza
            members_home=members_home,
            signals_used=len(signals),
        )

    def all_signals(self) -> list[dict]:
        return [
            {
                "device_id":   s.device_id,
                "signal_type": s.signal_type.value,
                "present":     s.present,
                "confidence":  s.confidence,
                "member_id":   s.member_id,
                "timestamp":   s.timestamp,
                "age_seconds": round(time.time() - s.timestamp, 1),
            }
            for s in self._active_signals()
        ]