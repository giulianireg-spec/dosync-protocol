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
    # ── Seguridad ────────────────────────────────────────────────────────────
    IntentClass.ENSURE_SAFETY: {
        "tags":      ["camera", "emergency", "door-lock", "alarm", "communication"],
        "actuators": ["unlock", "call", "alarm", "light"],
    },
    IntentClass.CONTROL_ACCESS: {
        "tags":      ["door-lock", "gate", "access"],
        "actuators": ["lock", "unlock"],
    },
    IntentClass.MONITOR_HEALTH: {
        "tags":      ["camera", "motion", "wearable", "sensor"],
        "actuators": [],
    },
    # ── Familia ──────────────────────────────────────────────────────────────
    IntentClass.NOTIFY_FAMILY: {
        "tags":      ["communication", "display", "phone"],
        "actuators": ["notify", "call", "display"],
    },
    IntentClass.REPORT_STATUS: {
        "tags":      [],
        "actuators": [],
    },
    # ── Ambiente ─────────────────────────────────────────────────────────────
    IntentClass.SET_ENVIRONMENT: {
        "tags":      ["light", "thermostat", "blinds", "climate"],
        "actuators": ["set_brightness", "set_temperature", "set_position"],
    },
    # ── Energia y eficiencia ─────────────────────────────────────────────────
    IntentClass.SAVE_ENERGY: {
        "tags":      ["light", "thermostat", "smart-plug", "climate", "blinds"],
        "actuators": ["set_brightness", "set_temperature", "turn_off", "set_position"],
    },
    IntentClass.REMIND_CHORE: {
        "tags":      ["communication", "display", "phone"],
        "actuators": ["notify", "display"],
    },
    IntentClass.ALERT_ANOMALY: {
        "tags":      ["communication", "phone", "display"],
        "actuators": ["notify", "call"],
    },
    # ── Rutinas ──────────────────────────────────────────────────────────────
    IntentClass.BEDTIME_ROUTINE: {
        "tags":      ["light", "blinds", "display", "smart-plug", "climate"],
        "actuators": ["set_brightness", "set_position", "turn_off", "set_temperature"],
    },
    IntentClass.MORNING_ROUTINE: {
        "tags":      ["light", "blinds", "appliance", "climate", "display"],
        "actuators": ["set_brightness", "set_position", "turn_on", "set_temperature"],
    },
    IntentClass.CHILDREN_ARRIVED: {
        "tags":      ["light", "climate", "communication"],
        "actuators": ["turn_on", "set_brightness", "set_temperature", "notify"],
    },
    IntentClass.AWAY_MODE: {
        "tags":      ["light", "smart-plug", "camera", "alarm", "thermostat"],
        "actuators": ["turn_off", "set_brightness", "arm", "set_temperature"],
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
            # Matchea si el tag del dispositivo coincide con el tag de la accion
            # y el tipo de actuador coincide
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
                # Preferir params del FamilyProfile si existen
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
        self.resolver       = SemanticResolver(self.registry)
        self.audit_log      = AuditLog()
        self.occupancy      = OccupancyEngine()
        self.family_profile: FamilyProfile | None = None
        self._event_handlers: list[Callable] = []
        self.db             = DoSyncDB(db_path)
        self.db.init()
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
            ContextSignalType, DeviceCategory, EventSpec, SensorSpec,
        )

        # Restaurar dispositivos
        for manifest_dict in self.db.load_devices():
            try:
                # Reconstruir el CapabilityManifest desde el dict guardado
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
                        severity=Urgency(e.get("severity", "info")),
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
                    cert_tier=CertTier(manifest_dict.get("cert_tier", "basic")),
                )
                # Restore adapter fields — critical for physical device control
                if manifest_dict.get("adapter"):
                    manifest.adapter        = manifest_dict["adapter"]
                    manifest.adapter_config = manifest_dict.get("adapter_config", {})
                self.registry.register(manifest)
            except Exception as e:
                log.warning("Could not restore device %s: %s",
                            manifest_dict.get("device_id", "?"), e)

        # Restaurar audit log
        for entry in self.db.load_audit_log():
            self.audit_log._entries.append(entry)
            self.audit_log._prev_hash = entry.get("hash", "0" * 64)

        # Restaurar senales de presencia
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
        """Carga el perfil familiar en el hub y lo persiste."""
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
        """Un context provider actualiza su señal de presencia."""
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
        """Estado de ocupacion inferido actual."""
        return self.occupancy.get_occupancy()

    # ── Device management ────────────────────────────────────────────────────

    def register_device(self, manifest: CapabilityManifest) -> None:
        self.registry.register(manifest)
        self.db.save_device(manifest.device_id, manifest.to_dict())
        self.audit_log.append({
            "type": "device_registered",
            "device_id": manifest.device_id,
            "device_name": manifest.device_name,
        })

    def unregister_device(self, device_id: str) -> None:
        self.registry.unregister(device_id)
        self.db.delete_device(device_id)
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

            tasks = [
                executor.execute(
                    DeviceAction(
                        device_id=a.device_id,
                        action=a.action,
                        params=a.params,
                    ),
                    plan.urgency,
                )
                for a in phase.actions
            ]
            results = await asyncio.gather(*tasks)

            failed = [r.device_id for r in results if not r.success]
            phase_result = IntentResult(
                intent_id=f"{plan.intent_id}-phase{i+1}",
                success=len(failed) == 0,
                results=list(results),
                failed_devices=failed,
            )
            all_results.append(phase_result)

            self.audit_log.append({
                "type":       "phase_executed",
                "intent_id":  plan.intent_id,
                "phase":      phase.name,
                "phase_num":  i + 1,
                "actions":    len(phase.actions),
                "failed":     failed,
                "success":    phase_result.success,
            })

            if phase.delay_after_ms > 0 and i < len(plan.phases) - 1:
                log.info("Waiting %dms before next phase...", phase.delay_after_ms)
                await asyncio.sleep(phase.delay_after_ms / 1000)

        return all_results


# ── Occupancy Engine ──────────────────────────────────────────────────────────

class OccupancyEngine:
    """
    Infiere el estado de ocupacion del hogar agregando señales de multiples
    context providers. Nunca usa una sola fuente — combina y pondera.

    Señales soportadas y su peso por defecto:
      GPS del celular fuera del perimetro  → ausencia con peso 0.9
      WiFi del celular desconectado        → ausencia con peso 0.7
      Sin movimiento PIR por 30+ min       → ausencia con peso 0.4
      Smartwatch GPS fuera del perimetro   → ausencia con peso 0.8
      Smart TV apagado                     → ausencia con peso 0.2
    """

    def __init__(self):
        self._signals: list[PresenceSignal] = []
        self._signal_ttl_seconds = 300      # señales expiran en 5 minutos

    def update(self, signal: PresenceSignal) -> None:
        """Registra o actualiza una señal de presencia."""
        # Reemplazar señal anterior del mismo dispositivo
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
        """Filtra señales expiradas."""
        cutoff = time.time() - self._signal_ttl_seconds
        return [s for s in self._signals if s.timestamp >= cutoff]

    def get_occupancy(self) -> OccupancyState:
        """
        Calcula el estado de ocupacion actual.
        Retorna occupied=True si la confianza ponderada de presencia >= 0.5.
        """
        signals = self._active_signals()
        if not signals:
            # Sin señales = estado desconocido, asumimos ocupado por seguridad
            return OccupancyState(
                occupied=True,
                confidence=0.0,
                members_home=[],
                signals_used=0,
            )

        # Calcular confianza ponderada de presencia
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