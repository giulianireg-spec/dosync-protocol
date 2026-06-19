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
import time
from typing import Callable, Optional

from .db import DoSyncDB
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
        return list(self._devices.values())

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


# ── Semantic Resolver (Layer 4) ───────────────────────────────────────────────

# Maps intent classes to the tags and actuator types we look for
# ── Intent class resolution ──────────────────────────────────────────────────
# DoSync v0.4+: Intent classes are stored in the database, not hardcoded here.
# The protocol defines the FORMAT of an intent name, not its vocabulary.
# Resolution tags and actuators are registered via POST /v1/intent-classes.
# Universal intents (ensure_safety, alert_anomaly, control_access,
# report_status, notify) are seeded automatically at hub init.



# ── Resolver interface ────────────────────────────────────────────────────────

class BaseResolver:
    """
    Formal interface for DoSync semantic resolvers.

    The protocol defines WHAT a resolver must do, not HOW.
    Third-party implementations can be dropped in by subclassing this
    and passing the instance to DoSyncHub.

    A resolver receives an Intent and returns an ActionPlan.
    It has read-only access to the CapabilityRegistry.

    To implement a custom resolver:
        class MyResolver(BaseResolver):
            async def resolve(self, intent: Intent) -> ActionPlan:
                ...
    """

    def __init__(self, registry: 'CapabilityRegistry'):
        self.registry = registry

    def resolve(self, intent: Intent) -> ActionPlan:
        raise NotImplementedError(
            'Subclasses must implement resolve(intent) -> ActionPlan'
        )


class ExternalResolver(BaseResolver):
    """
    HTTP-based external resolver. Delegates intent resolution to an external
    service implementing the DoSync External Resolver Protocol.

    The external service receives an Intent + the full CapabilityRegistry and
    returns an ActionPlan. This enables resolvers implemented in any language
    (Go, Node.js, Rust, etc.) or backed by a local LLM.

    Configuration:
        DOSYNC_RESOLVER_URL=http://my-resolver:8080

    Wire format (POST /resolve):
        Request body:
            {
              "intent":   <Intent.to_dict()>,
              "registry": [<CapabilityManifest.to_dict()>, ...],
              "hub_id":   "<hub identifier>"
            }
        Response body:
            {
              "intent_id": "<same intent_id from request>",
              "urgency":   "info" | "warning" | "alert" | "emergency",
              "actions":   [
                {
                  "device_id":       "<str>",
                  "action":          "<str>",
                  "params":          {},
                  "relevance_score": 0.0
                }
              ]
            }
        Timeout: 500ms (spec §6 requirement for non-LLM resolvers)

    If the external resolver is unreachable or times out, falls back to
    the CapabilityMatchingResolver automatically. Fallback is logged.

    Full protocol definition: spec/RESOLVER-SPEC-v0.3.md §5 External Resolver
    """

    TIMEOUT_S = 0.5  # 500ms — spec requirement for non-LLM resolvers

    def __init__(self, registry: 'CapabilityRegistry', url: str, hub_id: str = ""):
        super().__init__(registry)
        self._url     = url.rstrip("/") + "/resolve"
        self._hub_id  = hub_id
        self._fallback = CapabilityMatchingResolver(registry)
        log.info("ExternalResolver configured: %s (fallback: CapabilityMatchingResolver)", self._url)

    def resolve(self, intent: Intent) -> ActionPlan:
        """Resolve intent via external HTTP service.

        Runs the blocking HTTP call in a ThreadPoolExecutor so it never
        blocks the asyncio event loop, regardless of whether resolve() is
        called from sync or async context.

        Supports HTTPS: set DOSYNC_RESOLVER_URL=https://... and optionally
        DOSYNC_RESOLVER_CA_CERT=/path/to/ca.crt for self-signed certificates.
        """
        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._http_resolve, intent)
                return future.result(timeout=self.TIMEOUT_S + 1.0)
        except concurrent.futures.TimeoutError:
            log.warning("ExternalResolver timeout after %.1fs — falling back", self.TIMEOUT_S)
            return self._fallback.resolve(intent)
        except Exception as exc:
            log.warning("ExternalResolver error (%s) — falling back", exc)
            return self._fallback.resolve(intent)

    def _http_resolve(self, intent: Intent) -> ActionPlan:
        """Synchronous HTTP call — runs in thread pool, never in event loop."""
        import json, ssl, urllib.request, urllib.error, os

        payload = {
            "intent":   intent.to_dict(),
            "registry": [m.to_dict() for m in self.registry.all()],
            "hub_id":   self._hub_id,
        }
        data = json.dumps(payload).encode()

        # TLS context — use CA cert if provided, or system trust store
        ctx = None
        if self._url.startswith("https://"):
            ctx = ssl.create_default_context()
            ca_cert = os.environ.get("DOSYNC_RESOLVER_CA_CERT", "")
            if ca_cert and os.path.exists(ca_cert):
                ctx.load_verify_locations(ca_cert)

        req = urllib.request.Request(
            self._url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_S, context=ctx) as resp:
                body = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise RuntimeError(f"URLError: {exc}") from exc

        # Parse response → ActionPlan
        actions = [
            DeviceAction(
                device_id=a["device_id"],
                action=a["action"],
                params=a.get("params", {}),
                relevance_score=float(a.get("relevance_score", 0.0)),
            )
            for a in body.get("actions", [])
        ]
        return ActionPlan(
            intent_id=intent.intent_id,
            actions=actions,
            urgency=intent.urgency,
        )

        # Parse the response into an ActionPlan
        try:
            actions = [
                DeviceAction(
                    device_id=a["device_id"],
                    action=a["action"],
                    params=a.get("params", {}),
                    relevance_score=float(a.get("relevance_score", 0.0)),
                )
                for a in body.get("actions", [])
            ]
            return ActionPlan(
                intent_id=intent.intent_id,
                actions=actions,
                urgency=intent.urgency,
            )
        except (KeyError, TypeError, ValueError) as e:
            log.warning("ExternalResolver returned invalid ActionPlan (%s) — falling back", e)
            return self._fallback.resolve(intent)

    def explain(self, intent: Intent) -> dict:
        """
        ExternalResolver delegates resolution to the external service.
        For explain(), falls back to CapabilityMatchingResolver so the
        scoring breakdown reflects the local tag-matching algorithm.
        """
        result = self._fallback.explain(intent)
        result["resolver"] = "ExternalResolver"
        result["external_url"] = self._url
        result["note"] = (
            "Scoring shown is from local CapabilityMatchingResolver (fallback). "
            "Actual resolution is delegated to the external service."
        )
        return result


class CapabilityMatchingResolver(BaseResolver):
    """
    Layer 4: resolves an Intent into an ActionPlan by matching
    against registered device capabilities.
    """

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def _relevance_score(
        self,
        device: CapabilityManifest,
        intent: Intent,
        resolution: dict,
    ) -> float:
        score = 0.0

        # Tag overlap
        target_tags = set(resolution.get("tags", []))
        device_tags = set(device.tags)
        # If resolution uses specific non-generic tags, require exact match
        generic_tags = {"light", "climate", "communication", "sensor", "appliance", "display"}
        specific_tags = target_tags - generic_tags
        if specific_tags and not (specific_tags & device_tags):
            return 0.0
        score += len(target_tags & device_tags) * 10.0

        # Location match (if context has location, prefer devices with matching tag)
        location = intent.context.get("location", "")
        if location and location in device_tags:
            score += 15.0

        # Emergency bonus
        if intent.urgency == Urgency.EMERGENCY and device.emergency_capable:
            score += 30.0

        # Actuator match
        target_actuators = set(resolution.get("actuators", []))
        device_actuators = {a.type for a in device.actuators}
        score += len(target_actuators & device_actuators) * 12.0

        return score

    def explain(self, intent: Intent) -> dict:
        """
        Explica el razonamiento del resolver para un intent dado.
        Muestra el score de cada dispositivo y por qué fue incluido o excluido.
        Calculado on-demand — refleja el estado actual del registry.
        """
        resolution = self._get_resolution(intent)
        target_tags      = set(resolution.get("tags", []))
        target_actuators = set(resolution.get("actuators", []))
        generic_tags     = {"light", "climate", "communication", "sensor", "appliance", "display"}
        specific_tags    = target_tags - generic_tags
        location         = intent.context.get("location", "")

        included = []
        excluded = []

        for device in self.registry.all():
            device_tags      = set(device.tags)
            device_actuators = {a.type for a in device.actuators}

            # Build score breakdown for explanation
            tag_overlap_tags = target_tags & device_tags
            tag_overlap      = len(tag_overlap_tags) * 10.0
            location_bonus   = 15.0 if (location and location in device_tags) else 0.0
            emergency_bonus  = 30.0 if (intent.urgency == Urgency.EMERGENCY and device.emergency_capable) else 0.0
            actuator_matched = target_actuators & device_actuators
            actuator_bonus   = len(actuator_matched) * 12.0
            score            = tag_overlap + location_bonus + emergency_bonus + actuator_bonus

            # Exclusion reason when score == 0
            if score == 0:
                if specific_tags and not (specific_tags & device_tags):
                    reason = f"required specific tags {specific_tags} not in device tags {device_tags}"
                elif not (target_tags & device_tags):
                    reason = "no tag overlap with intent resolution tags"
                else:
                    reason = "score = 0"
                excluded.append({
                    "device_id":   device.device_id,
                    "device_name": device.device_name,
                    "device_tags": sorted(device.tags),
                    "reason":      reason,
                    "included":    False,
                })
            else:
                included.append({
                    "device_id":   device.device_id,
                    "device_name": device.device_name,
                    "device_tags": sorted(device.tags),
                    "score":       score,
                    "score_breakdown": {
                        "tag_overlap":      tag_overlap,
                        "matched_tags":     sorted(tag_overlap_tags),
                        "location_bonus":   location_bonus,
                        "emergency_bonus":  emergency_bonus,
                        "actuator_match":   actuator_bonus,
                        "matched_actuators": sorted(actuator_matched),
                    },
                    "emergency_capable": device.emergency_capable,
                    "included": True,
                })

        # Sort included devices by descending relevance score
        included.sort(key=lambda x: x["score"], reverse=True)

        return {
            "intent":              intent.intent.value,
            "urgency":             intent.urgency.value,
            "context":             intent.context,
            "resolution_tags":     sorted(target_tags),
            "resolution_actuators": sorted(target_actuators),
            "devices_evaluated":   len(included) + len(excluded),
            "devices_included":    len(included),
            "devices_excluded":    len(excluded),
            "included":            included,
            "excluded":            excluded,
            "note": (
                "This explanation reflects the resolver scoring only. "
                "The PolicyEngine may further block, modify, or request confirmation "
                "before execution. See the audit log for actual execution outcomes."
            ),
        }

    def _profile_params(self, device: CapabilityManifest,
                         actuator_type: str, intent: Intent) -> dict | None:
        """
        Si el intent tiene acciones explicitas del FamilyProfile en el context,
        busca los params correspondientes a este dispositivo y actuator.
        Retorna None si no hay match — el caller usara los defaults.
        """
        profile_actions = intent.context.get("actions", [])
        if not profile_actions:
            return None
        for pa in profile_actions:
            # Match: device tag matches the action tag
            # and actuator type matches
            if (pa.get("action_type") == actuator_type and
                    pa.get("tag") in device.tags):
                return pa.get("params", {})
        return None

    def _build_actions_for_device(
        self,
        device: CapabilityManifest,
        intent: Intent,
        resolution: dict,
    ) -> list[DeviceAction]:
        actions = []
        target_actuators = set(resolution.get("actuators", []))

        for actuator in device.actuators:
            if not target_actuators or actuator.type in target_actuators:
                # Prefer FamilyProfile params when available
                profile_p = self._profile_params(device, actuator.type, intent)
                params = profile_p if profile_p is not None                     else self._default_params(actuator, intent)
                actions.append(DeviceAction(
                    device_id=device.device_id,
                    action=actuator.type,
                    params=params,
                ))

        # For sensors with no actuators, add a "read" action
        if not actions and device.sensors:
            actions.append(DeviceAction(
                device_id=device.device_id,
                action="read_sensors",
                params={"sensor_ids": [s.id for s in device.sensors]},
            ))

        return actions

    def _default_params(self, actuator: ActuatorSpec, intent: Intent) -> dict:
        """Sensible defaults per actuator type."""
        defaults = {
            "unlock":           {"duration_seconds": 300},
            "lock":             {},
            "call":             {"number": intent.context.get("emergency_number", "911"),
                                 "message": intent.context.get("message", "Emergency at home")},
            "notify":           {"message": intent.context.get("message", ""),
                                 "urgency": intent.urgency.value},
            "alarm":            {"pattern": "emergency" if intent.urgency == Urgency.EMERGENCY
                                            else "alert"},
            "light":            {"brightness": 100, "color": "white"},
            "set_brightness":   {"brightness": 100},
            "set_temperature":  {"celsius": intent.context.get("target_temp", 21)},
        }
        return defaults.get(actuator.type, {})

    def _get_resolution(self, intent: Intent) -> dict:
        """Return resolution tags/actuators from the intent_classes DB table.
        All intent classes — universal and domain-specific — live in the DB.
        Falls back to empty resolution if intent class is not registered.
        """
        try:
            hub = getattr(self, "hub", None)
            db  = getattr(hub, "db", None)
            if db:
                name = str(intent.intent)
                row = db.get_intent_class(name)
                if row:
                    return {
                        "tags":      row["resolution_tags"],
                        "actuators": row["resolution_actuators"],
                    }
        except Exception as e:
            log.warning("_get_resolution: DB lookup failed for '%s': %s", str(intent.intent), e)
        return {"tags": [], "actuators": []}

    def resolve(self, intent: Intent) -> ActionPlan:
        from datetime import datetime
        # Context validation: schedule-aware intents
        schedule = intent.context.get("schedule")
        if schedule:
            now = datetime.now()
            days_ok = now.weekday() < 5  # lun-vie = 0-4
            hour_range = schedule.get("hour_range")
            if hour_range:
                h_start, h_end = hour_range
                hour_ok = h_start <= now.hour * 60 + now.minute <= h_end
            else:
                hour_ok = True
            if not days_ok or not hour_ok:
                log.info("Intent '%s' blocked by schedule (day=%s hour=%s:%s)",
                         intent.intent.value, now.weekday(), now.hour, now.minute)
                return ActionPlan(intent_id=intent.intent_id, actions=[], urgency=intent.urgency)
        resolution = self._get_resolution(intent)

        # Candidate selection via inverted tag index.
        # Strategy:
        #   specific_tags → intersection index: O(|result|), devices must have ALL
        #   generic_tags only → union index: O(|tags| + |candidates|)
        #   no tags (report_status) → all devices
        target_tags   = set(resolution.get("tags", []))
        generic_tags  = {"light", "climate", "communication", "sensor", "appliance", "display"}
        specific_tags = target_tags - generic_tags

        if target_tags:
            # Union index: candidates are devices with ANY of the target tags.
            # O(|target_tags| + |candidates|) with the inverted index.
            candidates = self.registry.find_by_tags(list(target_tags))
        else:
            # Intents with no resolution tags (e.g. report_status) select all devices.
            # This is intentional — report_status is a status query across the
            # entire deployment, not a targeted action.
            candidates = self.registry.all()

        # Emergency intents: always include emergency_capable devices as candidates,
        # even if their tags don't overlap with the intent's resolution tags.
        # This ensures physical safety devices (lights at max brightness, alarms)
        # are never excluded by the tag filter on emergency intents.
        if intent.urgency == Urgency.EMERGENCY:
            candidate_ids = {d.device_id for d in candidates}
            for device in self.registry.find_emergency_capable():
                if device.device_id not in candidate_ids:
                    candidates.append(device)

        # Score candidates only
        scored: list[tuple[float, CapabilityManifest]] = []
        for device in candidates:
            score = self._relevance_score(device, intent, resolution)
            if score > 0:
                scored.append((score, device))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Emergency: always include ALL emergency-capable devices
        if intent.urgency == Urgency.EMERGENCY:
            scored_ids = {d.device_id for _, d in scored}
            for device in self.registry.find_emergency_capable():
                if device.device_id not in scored_ids:
                    scored.append((50.0, device))

        # Build actions
        all_actions: list[DeviceAction] = []
        for score, device in scored:
            actions = self._build_actions_for_device(device, intent, resolution)
            for a in actions:
                a.relevance_score = score
            all_actions.extend(actions)

        log.info(
            "Intent '%s' resolved to %d actions across %d devices",
            intent.intent.value, len(all_actions), len(scored),
        )

        return ActionPlan(
            intent_id=intent.intent_id,
            actions=all_actions,
            urgency=intent.urgency,
        )




# ── State-aware resolver ──────────────────────────────────────────────────────

class StateAwareResolver(CapabilityMatchingResolver):
    """
    Extends CapabilityMatchingResolver with device state awareness.

    Before executing an action, queries the current device state
    and skips redundant actions:
    - Does not turn on a light that is already at full brightness
    - Does not unlock a door that is already unlocked
    - Does not set a temperature that is already at target

    This is the recommended resolver for production deployments.
    """

    def __init__(self, registry: 'CapabilityRegistry', hub: 'DoSyncHub'):
        super().__init__(registry)
        self._hub = hub
        self._state_cache: dict = {}

    def _get_device_state(self, device_id: str) -> dict:
        """Returns cached device state or empty dict if unknown."""
        return self._state_cache.get(device_id, {})

    def mark_unreachable(self, device_id: str, ttl_seconds: int = None) -> None:
        """
        Marks a device as unreachable for TTL seconds.
        TTL is configured via DOSYNC_UNREACHABLE_TTL env var (default: 1800s = 30min).
        Called when an adapter fails with a connection error.
        """
        import time as _time
        import os as _os
        ttl = ttl_seconds if ttl_seconds is not None else int(_os.environ.get("DOSYNC_UNREACHABLE_TTL", "1800"))
        if device_id not in self._state_cache:
            self._state_cache[device_id] = {}
        self._state_cache[device_id]['unreachable'] = True
        self._state_cache[device_id]['unreachable_until'] = _time.time() + ttl
        self._state_cache[device_id]['unreachable_since'] = _time.time()
        log.info('StateAwareResolver: device %s marked unreachable for %ds (TTL)', device_id, ttl)
        try:
            db = getattr(self._hub, 'db', None)
            if db:
                db.save_device_state(device_id, self._state_cache[device_id])
        except Exception as _e:
            log.warning('StateAwareResolver: failed to persist unreachable state for %s: %s', device_id, _e)

    def clear_unreachable(self, device_id: str) -> None:
        """Clears unreachable mark — called when device responds successfully."""
        state = self._state_cache.get(device_id, {})
        state.pop('unreachable', None)
        state.pop('unreachable_until', None)
        state.pop('unreachable_since', None)
        self._state_cache[device_id] = state
        log.info('StateAwareResolver: device %s unreachable mark cleared', device_id)

    def update_state(self, device_id: str, state: dict) -> None:
        """Called by adapters after execution to update state cache and persist to DB."""
        if device_id not in self._state_cache:
            self._state_cache[device_id] = {}
        self._state_cache[device_id].update(state)
        # Persist to SQLite to survive hub restarts
        try:
            db = getattr(self._hub, 'db', None)
            if db:
                db.save_device_state(device_id, self._state_cache[device_id])
        except Exception as _e:
            log.warning('StateAwareResolver: failed to persist state for %s: %s', device_id, _e)

    async def start_background_refresh(
        self,
        executor: "DeviceExecutor",
        interval: float = None,
    ) -> None:
        """
        Background task that periodically queries device state via get_state().
        Updates the state cache without blocking intent resolution.

        Only queries devices whose adapter implements get_state().
        Devices that don't respond are silently skipped — unreachable marking
        is handled by the executor, not by the refresher.

        Args:
            executor:  AdapterExecutor instance to get adapters from
            interval:  refresh interval in seconds. Defaults to
                       DOSYNC_STATE_REFRESH_INTERVAL env var (default: 60s)
        """
        import os as _os
        import time as _time

        if interval is None:
            interval = float(_os.environ.get("DOSYNC_STATE_REFRESH_INTERVAL", "60"))

        log.info("StateAwareResolver: background refresh started (interval=%.0fs)", interval)

        while True:
            try:
                await asyncio.sleep(interval)
                await self._refresh_cycle(executor)
            except asyncio.CancelledError:
                log.info("StateAwareResolver: background refresh stopped")
                break
            except Exception as e:
                log.warning("StateAwareResolver: refresh cycle error: %s", e)

    async def _refresh_cycle(self, executor: "DeviceExecutor") -> None:
        """Run one refresh cycle — query all devices whose adapter supports get_state()."""
        from .adapters import AdapterExecutor
        if not isinstance(executor, AdapterExecutor):
            return

        refreshed = 0
        skipped   = 0

        for device in self.registry.all():
            adapter = executor.get_adapter(device.adapter)
            if adapter is None:
                skipped += 1
                continue

            try:
                state = await asyncio.wait_for(
                    adapter.get_state(device.device_id),
                    timeout=3.0,
                )
            except (asyncio.TimeoutError, Exception):
                skipped += 1
                continue

            if state is None:
                skipped += 1
                continue

            # Update cache — only if device responded (positive signal only)
            self.update_state(device.device_id, state)
            # Clear unreachable mark if device is responding
            if self._state_cache.get(device.device_id, {}).get("unreachable"):
                self.clear_unreachable(device.device_id)
                log.info("StateAwareResolver: %s back online (detected by refresher)",
                         device.device_id)
            refreshed += 1

        if refreshed > 0:
            log.debug("StateAwareResolver: refresh cycle done — %d updated, %d skipped",
                      refreshed, skipped)

    def _load_state_from_db(self) -> None:
        """Load state cache from SQLite on startup. Silent if no data exists."""
        try:
            db = getattr(self._hub, 'db', None)
            if db:
                states = db.load_all_device_states()
                if states:
                    self._state_cache.update(states)
                    log.info('StateAwareResolver: loaded state for %d device(s) from DB',
                             len(states))
        except Exception as _e:
            log.warning('StateAwareResolver: failed to load state from DB: %s', _e)

    def _is_redundant(self, action: DeviceAction) -> bool:
        """Returns True if the action would have no effect given current state."""
        state = self._get_device_state(action.device_id)
        if not state:
            return False  # unknown state — execute to be safe

        if action.action == 'turn_on' and state.get('on') is True:
            brightness = action.params.get('brightness', 100)
            current_brightness = state.get('brightness', 0)
            if current_brightness >= brightness:
                log.debug('Skipping redundant turn_on for %s (already on at %s%%)',
                          action.device_id, current_brightness)
                return True

        if action.action == 'turn_off' and state.get('on') is False:
            log.debug('Skipping redundant turn_off for %s (already off)', action.device_id)
            return True

        if action.action == 'unlock' and state.get('locked') is False:
            log.debug('Skipping redundant unlock for %s (already unlocked)', action.device_id)
            return True

        if action.action == 'lock' and state.get('locked') is True:
            log.debug('Skipping redundant lock for %s (already locked)', action.device_id)
            return True

        if action.action == 'set_temperature':
            target = action.params.get('celsius')
            current = state.get('temperature')
            if target and current and abs(target - current) < 0.5:
                log.debug('Skipping redundant set_temperature for %s (already at %.1f)',
                          action.device_id, current)
                return True

        return False

    def resolve(self, intent: Intent) -> ActionPlan:
        """Resolves intent and filters redundant actions based on device state."""
        plan = super().resolve(intent)

        if not self._state_cache:
            return plan  # no state info yet — pass through

        filtered = [a for a in plan.actions if not self._is_redundant(a)]
        skipped = len(plan.actions) - len(filtered)
        if skipped:
            log.info('StateAwareResolver: skipped %d redundant action(s) for intent %s',
                     skipped, intent.intent.value)

        return ActionPlan(
            intent_id=plan.intent_id,
            actions=filtered,
            urgency=plan.urgency,
        )

# ── Audit log ─────────────────────────────────────────────────────────────────

class AuditLog:
    """
    Tamper-evident chained log for all intent executions.
    SHA-256 chains each entry to the previous one.
    """

    def __init__(self):
        self._entries: list[dict] = []
        self._prev_hash = "0" * 64
        self._persist_cb = None   # set by DoSyncHub after db.init()

    def append(self, entry: dict) -> str:
        entry["prev_hash"] = self._prev_hash
        entry["timestamp"] = time.time()
        raw = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(raw.encode()).hexdigest()
        entry["hash"] = entry_hash
        self._prev_hash = entry_hash
        self._entries.append(entry)
        if self._persist_cb:
            self._persist_cb(entry)
        return entry_hash

    def verify(self) -> bool:
        prev = "0" * 64
        for entry in self._entries:
            stored_hash = entry.pop("hash")
            raw = json.dumps(entry, sort_keys=True)
            calc = hashlib.sha256(raw.encode()).hexdigest()
            entry["hash"] = stored_hash
            if calc != stored_hash or entry["prev_hash"] != prev:
                return False
            prev = stored_hash
        return True

    def entries(self) -> list[dict]:
        return list(self._entries)


# ── DoSync Hub ────────────────────────────────────────────────────────────────

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
        self.audit_log      = AuditLog()
        self.occupancy      = OccupancyEngine()
        self.family_profile: FamilyProfile | None = None
        self._event_handlers: list[Callable] = []
        self.db             = DoSyncDB(db_path)
        self.db.init()
        # Load persisted state now that db is ready
        if hasattr(self, "resolver"):
            self.resolver._load_state_from_db()
        self.audit_log._persist_cb = self.db.append_audit
        self._restore_from_db()

    # ── Family profile ───────────────────────────────────────────────────────

    # ── DB restore ──────────────────────────────────────────────────────────

    def _restore_from_db(self) -> None:
        """
        Al iniciar el hub, restaura el estado desde SQLite.
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

        # Restore audit log
        for entry in self.db.load_audit_log():
            self.audit_log._entries.append(entry)
            self.audit_log._prev_hash = entry.get("hash", "0" * 64)

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
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.execute_intent(alert_intent))
                else:
                    loop.run_until_complete(self.execute_intent(alert_intent))
            except Exception:
                pass  # Intent fire is best-effort — never block registration
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

    async def execute_intent(
        self,
        intent: Intent,
        executor: "DeviceExecutor",
    ) -> IntentResult:
        log.info("Executing intent: %s [%s]", intent.intent.value, intent.urgency.value)

        plan = self.resolver.resolve(intent)

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
                plan = _AP(intent_id=plan.intent_id, actions=policy_result.modified_actions, urgency=plan.urgency)

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

        # Execute with the plan's FailurePolicy
        results, failed, aborted, policy_applied = [], [], [], "continue"
        try:
            results, failed, aborted, policy_applied = await self._execute_with_policy(
                plan, executor, intent
            )
        finally:
            self._active_intents.pop(intent_value, None)
            self._active_intent_devices.pop(intent_value, None)

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
            "failed":           failed,
            "aborted":          aborted,
            "failure_policy":   policy_applied,
            "status":           status,
            "success":          success,
        })

        return intent_result

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
        Ejecuta un PhasedActionPlan: cada fase en paralelo,
        las fases en secuencia con delay entre ellas.
        Ideal para emergencias donde el orden importa.
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
        Retorna occupied=True si la confianza ponderada de presencia >= 0.5.
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