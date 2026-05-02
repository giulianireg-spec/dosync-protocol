"""
DoSync Hub — Capability Registry + Semantic Resolver
Layers 3 & 4 of the DoSync protocol stack
"""

from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import time
from typing import Callable, Optional

from .models import (
    ActionPlan, ActionResult, ActuatorSpec, CapabilityManifest,
    DeviceAction, DeviceEvent, Intent, IntentClass, IntentResult, Urgency,
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

    def register(self, manifest: CapabilityManifest) -> None:
        self._devices[manifest.device_id] = manifest
        log.info("Registered device: %s (%s)", manifest.device_id, manifest.device_name)
        for cb in self._listeners:
            cb(manifest)

    def unregister(self, device_id: str) -> None:
        self._devices.pop(device_id, None)
        log.info("Unregistered device: %s", device_id)

    def get(self, device_id: str) -> Optional[CapabilityManifest]:
        return self._devices.get(device_id)

    def all(self) -> list[CapabilityManifest]:
        return list(self._devices.values())

    def find_by_tags(self, tags: list[str]) -> list[CapabilityManifest]:
        return [d for d in self._devices.values()
                if any(t in d.tags for t in tags)]

    def find_emergency_capable(self) -> list[CapabilityManifest]:
        return [d for d in self._devices.values() if d.emergency_capable]

    def find_by_actuator(self, actuator_type: str) -> list[CapabilityManifest]:
        return [
            d for d in self._devices.values()
            if any(a.type == actuator_type for a in d.actuators)
        ]

    def on_register(self, cb: Callable) -> None:
        self._listeners.append(cb)


# ── Semantic Resolver (Layer 4) ───────────────────────────────────────────────

# Maps intent classes to the tags and actuator types we look for
INTENT_RESOLUTION_MAP: dict[IntentClass, dict] = {
    IntentClass.ENSURE_SAFETY: {
        "tags":      ["camera", "emergency", "door-lock", "alarm", "communication"],
        "actuators": ["unlock", "call", "alarm", "light"],
        "require_emergency_capable": False,  # will pick best available
    },
    IntentClass.NOTIFY_FAMILY: {
        "tags":      ["communication", "display", "phone"],
        "actuators": ["notify", "call", "display"],
    },
    IntentClass.REPORT_STATUS: {
        "tags":      [],   # matches all sensors
        "actuators": [],
    },
    IntentClass.SET_ENVIRONMENT: {
        "tags":      ["light", "thermostat", "blinds", "climate"],
        "actuators": ["set_brightness", "set_temperature", "set_position"],
    },
    IntentClass.CONTROL_ACCESS: {
        "tags":      ["door-lock", "gate", "access"],
        "actuators": ["lock", "unlock"],
    },
    IntentClass.MONITOR_HEALTH: {
        "tags":      ["camera", "motion", "wearable", "sensor"],
        "actuators": [],
    },
}


class SemanticResolver:
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
        score += len(target_actuators & device_actuators) * 8.0

        return score

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
                params = self._default_params(actuator, intent)
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

    def resolve(self, intent: Intent) -> ActionPlan:
        resolution = INTENT_RESOLUTION_MAP.get(intent.intent, {"tags": [], "actuators": []})

        # Score all devices
        scored: list[tuple[float, CapabilityManifest]] = []
        for device in self.registry.all():
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


# ── Audit log ─────────────────────────────────────────────────────────────────

class AuditLog:
    """
    Tamper-evident chained log for all intent executions.
    SHA-256 chains each entry to the previous one.
    """

    def __init__(self):
        self._entries: list[dict] = []
        self._prev_hash = "0" * 64

    def append(self, entry: dict) -> str:
        entry["prev_hash"] = self._prev_hash
        entry["timestamp"] = time.time()
        raw = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(raw.encode()).hexdigest()
        entry["hash"] = entry_hash
        self._prev_hash = entry_hash
        self._entries.append(entry)
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

    def __init__(self):
        self.registry  = CapabilityRegistry()
        self.resolver  = SemanticResolver(self.registry)
        self.audit_log = AuditLog()
        self._event_handlers: list[Callable] = []

    # ── Device management ────────────────────────────────────────────────────

    def register_device(self, manifest: CapabilityManifest) -> None:
        self.registry.register(manifest)
        self.audit_log.append({
            "type": "device_registered",
            "device_id": manifest.device_id,
            "device_name": manifest.device_name,
        })

    def unregister_device(self, device_id: str) -> None:
        self.registry.unregister(device_id)
        self.audit_log.append({"type": "device_unregistered", "device_id": device_id})

    # ── Intent execution ─────────────────────────────────────────────────────

    async def execute_intent(
        self,
        intent: Intent,
        executor: "DeviceExecutor",
    ) -> IntentResult:
        log.info("Executing intent: %s [%s]", intent.intent.value, intent.urgency.value)

        plan = self.resolver.resolve(intent)

        # Execute all actions in parallel
        tasks = [
            executor.execute(action, intent.urgency)
            for action in plan.actions
        ]
        results: list[ActionResult] = await asyncio.gather(*tasks)

        failed = [r.device_id for r in results if not r.success]
        success = len(failed) == 0

        intent_result = IntentResult(
            intent_id=intent.intent_id,
            success=success,
            results=results,
            failed_devices=failed,
        )

        # Audit log
        self.audit_log.append({
            "type":       "intent_executed",
            "intent_id":  intent.intent_id,
            "intent":     intent.intent.value,
            "urgency":    intent.urgency.value,
            "actions":    len(plan.actions),
            "failed":     failed,
            "success":    success,
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
            await asyncio.coroutine(handler)(event) if asyncio.iscoroutinefunction(handler) \
                else handler(event)