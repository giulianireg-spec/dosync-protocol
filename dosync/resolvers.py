"""Intent resolution: which devices take part in a goal.

Extracted from `hub.py` on 3 September 2026. Four classes in an inheritance
chain — `BaseResolver` → `ExternalResolver`, and `BaseResolver` →
`CapabilityMatchingResolver` → `StateAwareResolver` — plus the `ScoreBreakdown`
they produce. They had to move together; there is no seam between them.

`CapabilityRegistry`, `is_quarantined` and `quarantine_reason` stay in `hub.py`
and are imported here. The dependency runs one way: a resolver reads the
registry, the registry knows nothing about resolvers.

**This is the code Phase 1 measured and Phase 3 will redesign.** On the two
corpora it was not tuned on it scores F1 0.64 and 0.61, while word overlap that
ignores the curated tags scores 0.69 and 0.67 — and a lock declaring `lock` and
`unlock` is excluded from `control_access` for want of a tag the capability
already implies. Extracting it first is what makes rewriting it possible without
dragging the registry, the executor and the orchestration along.

Substitution is preserved: `server.py` replaces `hub.resolver` with an
`ExternalResolver` at startup, and several tests replace `resolve` outright.
Everything is re-exported from `dosync.hub`, so no caller changes.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field

from .models import (ActionPlan, ActuatorSpec, CapabilityManifest, DeviceAction,
                     Intent, Urgency)

# Imported at call time inside the methods that need them: hub.py imports this
# module, so importing hub.py here at module level would close the cycle.
log = logging.getLogger("dosync.hub")


# ── Quarantine ────────────────────────────────────────────────────────────────
#
# Moved here from hub.py on 3 September 2026. It reads a manifest and decides
# whether the device may take part in an intent, which is a resolver question —
# and the resolvers were calling it three times to the hub's one. Leaving it in
# hub.py had forced a lazy import to avoid a cycle; a debt declared when the
# resolvers moved and paid now, separately, so that neither change hides in the
# other.

#: Marker used to quarantine a device without deleting it.
QUARANTINE_KEY = "quarantined"



def is_quarantined(manifest) -> bool:
    """Whether a device is registered but must not participate in intents.

    Stored in `adapter_config` rather than as a new manifest field so it rides
    the existing serialisation untouched — the flag is deployment state, not a
    capability of the device.
    """
    cfg = getattr(manifest, "adapter_config", None) or {}
    return bool(cfg.get(QUARANTINE_KEY))



def quarantine_reason(manifest) -> str:
    cfg = getattr(manifest, "adapter_config", None) or {}
    return str(cfg.get("quarantine_reason", "")) if cfg.get(QUARANTINE_KEY) else ""




# ── Semantic Resolver (Layer 4) ───────────────────────────────────────────────

# Maps intent classes to the tags and actuator types we look for
# ── Intent class resolution ──────────────────────────────────────────────────
# DoSync v0.4+: Intent classes are stored in the database, not hardcoded here.
# The protocol defines the FORMAT of an intent name, not its vocabulary.
# Resolution tags and actuators are registered via POST /v1/intent-classes.
# Universal intents (ensure_safety, alert_anomaly, control_access,
# report_status, notify) are seeded automatically at hub init.



# ── Resolver interface ────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """v9: the structured result of ONE scoring computation, shared by the
    decision path (resolve uses .total) and the explanation path (explain reads
    the components). Because both consume this, the explanation cannot diverge
    from the decision — the class that used to be two copies is now one."""
    matched_tags: list
    tag_component: float
    location_component: float
    emergency_component: float
    matched_actuators: list
    actuator_component: float
    hard_filtered: bool
    required_specific_tags: list
    device_tags: list
    had_any_tag_overlap: bool

    @property
    def total(self) -> float:
        # The hard filter zeroes the score outright: an all-specific resolution
        # with no overlap is OUT, bonuses notwithstanding (F3b).
        if self.hard_filtered:
            return 0.0
        return (self.tag_component + self.location_component
                + self.emergency_component + self.actuator_component)

    def exclusion_reason(self) -> str:
        if self.hard_filtered:
            return (f"required specific tags {self.required_specific_tags} not in "
                    f"device tags {self.device_tags} (hard filter — bonuses do not apply)")
        if not self.had_any_tag_overlap:
            return "no tag overlap with intent resolution tags"
        return "score = 0"



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

    def __init__(self, registry: 'CapabilityRegistry', url: str, hub_id: str = "",
                 hub: "DoSyncHub | None" = None):
        super().__init__(registry)
        self._url     = url.rstrip("/") + "/resolve"
        self._hub_id  = hub_id
        # Wiring contract (regression: tests/test_resolution_wiring.py): every
        # resolver that reads intent resolutions needs a hub handle (_hub) for
        # DB access — INCLUDING the local fallback. An unwired fallback returns
        # empty resolutions for every intent, which is exactly the silent
        # failure that gutted production explain()/fallback until 2026-07-11.
        self._hub = hub
        self._fallback = CapabilityMatchingResolver(registry)
        self._fallback._hub = hub
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
        # Preserve the fallback's semantic note (e.g. the read-only status-query
        # explanation) — clobbering it hid WHY actuators don't fire. The wrapper
        # context goes in its own field instead.
        result["resolver_note"] = (
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

    # v9 (2026-07-21): scoring weights, named once. Previously these five
    # constants were duplicated between _relevance_score (the decision) and
    # explain (the story), with a comment promising they "must mirror exactly"
    # — a promise the language did not enforce. Now there is one source.
    _W_TAG        = 10.0   # per overlapping tag
    _W_LOCATION   = 15.0   # context location matches a device tag
    _W_EMERGENCY  = 30.0   # emergency urgency + emergency_capable device
    _W_ACTUATOR   = 12.0   # per matching actuator type
    _FORCED_SCORE = 50.0   # emergency force-inclusion floor (mirrors resolve())

    def _candidates(self, intent: Intent, resolution: dict) -> list:
        """The ONE answer to "which devices does this intent evaluate?".

        resolve() used registry.find_by_tags(); explain() iterated
        registry.active(). Two answers to one question — the sixth time this
        project has held one fact in two places — and they disagreed in a way
        that mattered: a device matching only on ACTUATOR scores 12 in explain
        and was never a candidate in resolve, so the explain endpoint reported
        devices as INCLUDED that the resolver structurally could not act on.
        Measured on three registries: 2 in the reference deployment
        (ensure_safety), 2 industrial, 5 clinical — among them an OR ventilation
        unit and a patient-facing display, both listed as participating in an
        emergency that never touches them.

        That contradicts the project's first advertised property ("the score it
        reports is the same value the resolver decided with"). v9 unified the
        scoring FORMULA; it did not unify the candidate SET. This does.

        An empty resolution is not handled here: it is a read-only status query
        with its own branch in both callers (§6.4.1).
        """
        target_tags = set(resolution.get("tags", []))
        if not target_tags:
            return list(self.registry.active())

        # Quarantined devices are excluded here, once, for every caller.
        # active() filters them; find_by_tags() and find_emergency_capable() —
        # the two index lookups resolution actually went through — did not. So a
        # device the operator believes is gone was planned into intents,
        # including emergencies, which is precisely what active()'s contract
        # forbids ("must not be planned into an emergency, because the operator
        # already believes it is gone"). Found on the reference deployment: a
        # quarantined, emergency_capable light appeared in ensure_safety, and it
        # only became visible because explain() and resolve() started reporting
        # the same set and the totals stopped matching.
        candidates = [d for d in self.registry.find_by_tags(list(target_tags))
                      if not is_quarantined(d)]

        # Emergency intents also evaluate every emergency_capable device, tags or
        # not (F2b): a safety device must never be dropped by a tag filter. This
        # extension lived only in resolve(), which is part of why the two sets
        # drifted — it belongs to the definition of "candidate", not to one
        # caller. Quarantine still wins over it: force-inclusion exists to beat
        # the TAG filter, not to resurrect a device the operator withdrew.
        if intent.urgency == Urgency.EMERGENCY:
            seen = {d.device_id for d in candidates}
            for device in self.registry.find_emergency_capable():
                if device.device_id not in seen and not is_quarantined(device):
                    candidates.append(device)
        return candidates

    def _actuator_only_match(self, device: CapabilityManifest,
                              resolution: dict) -> list:
        """Actuator types this device declares that the resolution asks for.

        Used to explain a NON-candidate usefully rather than dropping it: a
        device whose actuators fit but whose tags do not is exactly the case an
        operator wants to know about — it is one tag away from participating.
        """
        target_actuators = set(resolution.get("actuators", []))
        return sorted(target_actuators & {a.type for a in device.actuators})

    def _score_breakdown(
        self,
        device: CapabilityManifest,
        intent: Intent,
        resolution: dict,
    ) -> "ScoreBreakdown":
        """The ONE scoring computation. Returns a structured breakdown whose
        .total is the relevance score. Both the decision path (resolve, which
        uses .total) and the explanation path (explain, which reads the parts)
        consume THIS — they can no longer disagree, because there is nothing to
        keep in sync."""
        target_tags   = set(resolution.get("tags", []))
        device_tags   = set(device.tags)
        generic_tags  = {"light", "climate", "communication", "sensor", "appliance", "display"}
        specific_tags = target_tags - generic_tags
        resolution_is_all_specific = bool(specific_tags) and not (target_tags & generic_tags)

        matched_tags = target_tags & device_tags
        # Hard-filter semantics (2026-07-11 panel decision, F3b): only when the
        # resolution is ALL specific tags are those tags a requirement; mixed
        # resolutions treat specific tags as boost, not gate.
        hard_filtered = resolution_is_all_specific and not (specific_tags & device_tags)

        location = intent.context.get("location", "")
        location_hit = bool(location) and location in device_tags

        emergency_hit = (intent.urgency == Urgency.EMERGENCY and device.emergency_capable)

        target_actuators = set(resolution.get("actuators", []))
        device_actuators = {a.type for a in device.actuators}
        matched_actuators = target_actuators & device_actuators

        return ScoreBreakdown(
            matched_tags=sorted(matched_tags),
            tag_component=len(matched_tags) * self._W_TAG,
            location_component=self._W_LOCATION if location_hit else 0.0,
            emergency_component=self._W_EMERGENCY if emergency_hit else 0.0,
            matched_actuators=sorted(matched_actuators),
            actuator_component=len(matched_actuators) * self._W_ACTUATOR,
            hard_filtered=hard_filtered,
            required_specific_tags=sorted(specific_tags) if hard_filtered else [],
            device_tags=sorted(device_tags),
            had_any_tag_overlap=bool(matched_tags),
        )

    def _relevance_score(
        self,
        device: CapabilityManifest,
        intent: Intent,
        resolution: dict,
    ) -> float:
        """Back-compat thin wrapper: the score is the breakdown's total."""
        return self._score_breakdown(device, intent, resolution).total

    def explain(self, intent: Intent) -> dict:
        """
        Explain the resolver's reasoning for a given intent.
        Shows each device's score and why it was included or excluded.
        Computed on demand — reflects the registry as it stands now.
        """
        resolution = self._get_resolution(intent)
        target_tags      = set(resolution.get("tags", []))
        target_actuators = set(resolution.get("actuators", []))
        generic_tags     = {"light", "climate", "communication", "sensor", "appliance", "display"}
        specific_tags    = target_tags - generic_tags
        location         = intent.context.get("location", "")

        included = []
        excluded = []

        # Empty resolution = READ-ONLY status query — mirrors resolve() (F4a):
        # the plan reads sensors on every sensing device; actuators never fire.
        if not target_tags and not target_actuators:
            for device in self.registry.active():
                if device.sensors:
                    included.append({
                        "device_id":   device.device_id,
                        "device_name": device.device_name,
                        "device_tags": sorted(device.tags),
                        "score":       1.0,
                        "score_breakdown": {"read_only_status_query": True,
                                            "sensors": [sn.id for sn in device.sensors]},
                        "emergency_capable": device.emergency_capable,
                        "included": True,
                    })
                else:
                    excluded.append({
                        "device_id":   device.device_id,
                        "device_name": device.device_name,
                        "device_tags": sorted(device.tags),
                        "reason":      "read-only status query — device has no sensors to read",
                        "included":    False,
                    })
            return {
                "intent":              intent.intent.value,
                "urgency":             intent.urgency.value,
                "context":             intent.context,
                "resolution_tags":     [],
                "resolution_actuators": [],
                "devices_evaluated":   len(included) + len(excluded),
                "devices_included":    len(included),
                "devices_excluded":    len(excluded),
                "included":            included,
                "excluded":            excluded,
                "note": ("Empty resolution: this intent is a read-only status query. "
                         "The plan is read_sensors on every sensing device; actuators never fire."),
            }

        # E1: the SAME candidate set resolve() evaluates. Iterating active()
        # here is what let explain report devices the resolver never considered.
        candidates = self._candidates(intent, resolution)
        candidate_ids = {d.device_id for d in candidates}

        # E2: a non-candidate is not silently dropped. A device whose ACTUATORS
        # fit the resolution but whose tags do not is one tag away from
        # participating, and that is precisely what an operator auditing "what
        # will my system do?" needs to see — stated as excluded, with the tag
        # that would change it. Reporting it as included (the old behavior) was
        # worse than either.
        for device in self.registry.active():
            if device.device_id in candidate_ids:
                continue
            fitting = self._actuator_only_match(device, resolution)
            reason = ("no tag overlap with intent resolution tags — not evaluated"
                      if not fitting else
                      f"not evaluated: declares matching actuators {fitting} but none "
                      f"of the resolution tags {sorted(target_tags)}; adding one of "
                      f"those tags would make it participate")
            excluded.append({
                "device_id":   device.device_id,
                "device_name": device.device_name,
                "device_tags": sorted(device.tags),
                "reason":      reason,
                "actuators_fit_resolution": fitting,
                "included":    False,
            })

        for device in candidates:
            # v9: consume the SAME breakdown resolve() decides with. No recompute,
            # nothing to keep in sync — the explanation IS the decision, narrated.
            bd = self._score_breakdown(device, intent, resolution)
            score = bd.total

            # Emergency force-inclusion — mirrors resolve() (F2b): emergency_capable
            # devices always participate in an emergency response, even when
            # tags/actuators match nothing.
            forced_emergency = (score == 0.0 and intent.urgency == Urgency.EMERGENCY
                                and device.emergency_capable)
            if forced_emergency:
                score = self._FORCED_SCORE

            if score == 0:
                excluded.append({
                    "device_id":   device.device_id,
                    "device_name": device.device_name,
                    "device_tags": sorted(device.tags),
                    "reason":      bd.exclusion_reason(),
                    "included":    False,
                })
            else:
                included.append({
                    "device_id":   device.device_id,
                    "device_name": device.device_name,
                    "device_tags": sorted(device.tags),
                    "score":       score,
                    "score_breakdown": {
                        "tag_overlap":      bd.tag_component,
                        "matched_tags":     bd.matched_tags,
                        "location_bonus":   bd.location_component,
                        "emergency_bonus":  bd.emergency_component,
                        "actuator_match":   bd.actuator_component,
                        "matched_actuators": bd.matched_actuators,
                        "forced_emergency": forced_emergency,
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
        When the intent carries explicit profile actions in its context,
        finds the params belonging to this device and actuator.
        Returns None when there is no match — the caller uses the defaults.
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
            # StateAwareResolver stores the hub as self._hub; some external/custom
            # resolvers may expose it publicly as self.hub. Check both — this line
            # being wrong ("hub" only) silently emptied EVERY intent resolution in
            # production until 2026-07-11: the resolver ran purely on the
            # emergency-capable bonus. Regression: tests/test_resolution_wiring.py
            hub = getattr(self, "_hub", None) or getattr(self, "hub", None)
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
            candidates = self._candidates(intent, resolution)
        else:
            # Empty resolution (e.g. report_status) = a READ-ONLY status query
            # across the deployment (2026-07-11 panel decision, F4a): the plan is
            # read_sensors on every device that senses — actuators never fire on
            # a status query. Note: before this, the "all devices as candidates"
            # branch was dead code — every candidate scored 0 and was dropped, so
            # report_status had never produced a single action in production.
            # SENSOR-KIND scope (2026-07-17): "read the environment" and "read
            # every device's self-state" are different status questions, and the
            # protocol now distinguishes them (SensorSpec.kind) while the
            # DEPLOYMENT decides which one a bare status query means:
            #   * intent.context["scope"] wins per-query: "all" | "environment"
            #   * otherwise DOSYNC_STATUS_SCOPE (deployment config, same place
            #     the deployment declares its other preferences)
            #   * otherwise "all" — today's behavior, so nothing changes for a
            #     deployment that has expressed no preference. The protocol has
            #     no opinion; it only makes the question expressible.
            # Invalid values warn and fall back rather than fail: a status query
            # is read-only and harmless, and refusing it over a typo'd
            # preference would be disproportionate.
            scope = (intent.context or {}).get("scope")
            if scope is not None and scope not in ("all", "environment"):
                log.warning("status scope %r unknown — using deployment default", scope)
                scope = None
            if scope is None:
                scope = os.environ.get("DOSYNC_STATUS_SCOPE", "all")
                if scope not in ("all", "environment"):
                    log.warning("DOSYNC_STATUS_SCOPE=%r invalid — using 'all'", scope)
                    scope = "all"

            read_actions = []
            for d in self.registry.active():
                if not d.sensors:
                    continue
                sensor_ids = [
                    sn.id for sn in d.sensors
                    if scope == "all"
                    or getattr(sn, "kind", "environment") == "environment"
                ]
                if sensor_ids:    # a device with only device_state sensors drops out
                    read_actions.append(DeviceAction(
                        device_id=d.device_id,
                        action="read_sensors",
                        params={"sensor_ids": sensor_ids},
                    ))
            return ActionPlan(
                intent_id=intent.intent_id,
                actions=read_actions,
                urgency=intent.urgency,
            )

        # (Emergency force-inclusion now lives in _candidates, shared with explain.)

        # Score candidates only
        scored: list[tuple[float, CapabilityManifest]] = []
        for device in candidates:
            score = self._relevance_score(device, intent, resolution)
            if score > 0:
                scored.append((score, device))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Emergency: every emergency-capable CANDIDATE is force-scored.
        #
        # This iterated find_emergency_capable() directly, a third answer to
        # "who participates?" beside _candidates() and active() — so filtering
        # quarantine in _candidates() alone still let a withdrawn device be
        # planned into an emergency here. Reading from the candidate set instead
        # keeps the rule in one place: force-inclusion overrides the TAG filter,
        # never quarantine.
        forced_ids: set = set()
        if intent.urgency == Urgency.EMERGENCY:
            scored_ids = {d.device_id for _, d in scored}
            for device in candidates:
                if device.emergency_capable and device.device_id not in scored_ids:
                    scored.append((50.0, device))
                    forced_ids.add(device.device_id)

        # Build actions. F2b (2026-07-11 panel decision): at EMERGENCY urgency,
        # an emergency_capable device whose resolution-scoped build yields ZERO
        # actions falls back to its FULL capability set — a safety device that
        # shows up and does nothing is worse than one that acts broadly. This
        # covers both force-included devices and tag/bonus-scored devices whose
        # actuator types simply don't appear in the resolution list. Tag and
        # type devices correctly (TAG-VOCABULARY.md) for precise, resolution-
        # scoped behavior instead. NOTE: the fallback builds every actuator the
        # device declares — a deliberately blunt instrument for emergencies.
        all_actions: list[DeviceAction] = []
        for score, device in scored:
            actions = self._build_actions_for_device(device, intent, resolution)
            if (not actions and intent.urgency == Urgency.EMERGENCY
                    and device.emergency_capable):
                actions = self._build_actions_for_device(
                    device, intent, {**resolution, "actuators": []})
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
        #: device_id → {sensor_id: arrival timestamp}. Parallel to the cache so
        #: that reading state never sees a stamp where a sensor should be.
        self._state_stamps: dict = {}

    def reading_age(self, device_id: str, sensor_id: str) -> float | None:
        """When a pushed reading for this sensor arrived, or None if never.

        Returns the absolute timestamp, not an age in seconds, because the
        caller compares it against the moment an ACTION was dispatched — not
        against the clock. A reading that predates the action confirms nothing,
        however recent it is, and an age would have thrown that away.
        """
        return (self._state_stamps.get(device_id) or {}).get(sensor_id)

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
        """Called by adapters after execution to update state cache and persist to DB.

        Each key is stamped with the moment it arrived. Until 2026-08-01 the
        cache held values with no age at all, which made the whole of H2
        unimplementable: you cannot bound a cached reading by freshness when
        nothing records when it arrived. The stamps live in a parallel map
        rather than inside the state dict so nothing that reads state sees a
        new key appear beside a sensor.
        """
        if device_id not in self._state_cache:
            self._state_cache[device_id] = {}
        self._state_cache[device_id].update(state)

        now = time.time()
        stamps = self._state_stamps.setdefault(device_id, {})
        for key in state:
            stamps[key] = now
        # Persist to SQLite to survive hub restarts
        try:
            db = getattr(self._hub, 'db', None)
            if db:
                db.save_device_state(device_id, self._state_cache[device_id])
        except Exception as _e:
            log.warning('StateAwareResolver: failed to persist state for %s: %s', device_id, _e)

    # NOTE: start_background_refresh/_refresh_cycle used to live here. They moved
    # to the hub (DoSyncHub.start_state_refresh) on 2026-07-14: server.py started
    # them behind `isinstance(hub.resolver, StateAwareResolver)`, which is always
    # False in production (ExternalResolver), so the refresher never ran. State
    # refresh is a hub concern — the hub calls this resolver's update_state() to
    # keep this cache coherent.

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
