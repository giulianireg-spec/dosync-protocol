"""
DoSync — Core data models (Layers 3–5)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import uuid


# ── Enumerations ─────────────────────────────────────────────────────────────

class Urgency(str, Enum):
    INFO      = "info"
    WARNING   = "warning"
    ALERT     = "alert"
    EMERGENCY = "emergency"

class DeviceCategory(str, Enum):
    SENSOR        = "sensor"
    ACTUATOR      = "actuator"
    HYBRID        = "hybrid"
    COMMUNICATION = "communication"
    EMERGENCY     = "emergency"
    CONTEXT       = "context"      # contribuye a inferencias de alto nivel

class CertTier(str, Enum):
    BASIC     = "basic"      # Layers 1-3
    STANDARD  = "standard"   # Layers 1-4
    EMERGENCY = "emergency"  # All layers + override

class IntentClass(str, Enum):
    # Seguridad
    ENSURE_SAFETY   = "ensure_safety"
    CONTROL_ACCESS  = "control_access"
    MONITOR_HEALTH  = "monitor_health"
    # Familia
    NOTIFY_FAMILY   = "notify_family"
    REPORT_STATUS   = "report_status"
    # Ambiente
    SET_ENVIRONMENT = "set_environment"
    # Energia y eficiencia
    SAVE_ENERGY     = "save_energy"      # nadie en casa -> modo ahorro
    REMIND_CHORE    = "remind_chore"     # electrodomestico termino -> recordatorio
    ALERT_ANOMALY   = "alert_anomaly"    # pico de consumo -> investigar
    # Rutinas
    BEDTIME_ROUTINE  = "bedtime_routine"  # hora de dormir de los ninos
    MORNING_ROUTINE  = "morning_routine"  # buenos dias, primera presencia del dia
    AWAY_MODE        = "away_mode"        # auto salio / todos salieron


class ContextSignalType(str, Enum):
    """Tipo de inferencia a la que contribuye un context provider."""
    PRESENCE   = "presence"    # si hay alguien en casa
    LOCATION   = "location"    # ubicacion GPS de un miembro
    SLEEP      = "sleep"       # estado de sueno
    HEALTH     = "health"      # signos vitales, actividad fisica
    ROUTINE    = "routine"     # patron de rutina diaria
    VEHICLE    = "vehicle"     # estado del auto / garage


# ── Capability manifest (Layer 3) ─────────────────────────────────────────────

@dataclass
class SensorSpec:
    id: str
    type: str                          # "temperature" | "boolean" | "motion" | etc.
    description: str = ""
    unit: Optional[str] = None
    range: Optional[list[float]] = None
    poll_interval_ms: int = 30_000

@dataclass
class ActuatorSpec:
    id: str
    type: str                          # "lock" | "light" | "unlock" | etc.
    description: str = ""
    params_schema: dict = field(default_factory=dict)

@dataclass
class EventSpec:
    id: str
    severity: Urgency
    description: str = ""

@dataclass
class ContextSignal:
    """
    Declara que este dispositivo contribuye a inferencias de contexto.
    Ejemplo: un smartwatch contribuye a PRESENCE y HEALTH.
    """
    type: ContextSignalType
    description: str = ""
    confidence_weight: float = 1.0    # peso relativo en la inferencia (0.0-1.0)
                                      # GPS del celular pesa mas que un PIR para presencia

@dataclass
class CapabilityManifest:
    device_id: str
    device_name: str
    manufacturer: str
    model: str
    firmware: str
    category: DeviceCategory
    tags: list[str]
    sensors: list[SensorSpec]              = field(default_factory=list)
    actuators: list[ActuatorSpec]          = field(default_factory=list)
    events: list[EventSpec]                = field(default_factory=list)
    context_signals: list[ContextSignal]   = field(default_factory=list)
    emergency_capable: bool                = False
    cert_tier: CertTier                    = CertTier.BASIC
    dosync_version: str                    = "0.1"

    def to_dict(self) -> dict:
        return {
            "dosync_version": self.dosync_version,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware": self.firmware,
            "category": self.category.value,
            "tags": self.tags,
            "capabilities": {
                "sensors":   [s.__dict__ for s in self.sensors],
                "actuators": [a.__dict__ for a in self.actuators],
                "events":    [{**e.__dict__, "severity": e.severity.value}
                              for e in self.events],
                "context_signals": [
                    {**c.__dict__, "type": c.type.value}
                    for c in self.context_signals
                ],
            },
            "emergency_capable": self.emergency_capable,
            "cert_tier": self.cert_tier.value,
        }


# ── Context model (inferencia de ocupacion y rutinas) ─────────────────────────

@dataclass
class PresenceSignal:
    """
    Una señal individual de presencia emitida por un context provider.
    El hub las agrega para inferir el estado de ocupacion del hogar.
    """
    device_id: str
    signal_type: ContextSignalType
    present: bool                          # True = detecta presencia
    confidence: float                      # 0.0-1.0
    member_id: Optional[str] = None        # a que miembro de la familia corresponde
    timestamp: float = field(default_factory=time.time)

@dataclass
class OccupancyState:
    """
    Estado inferido de ocupacion del hogar, calculado por el hub
    a partir de multiples PresenceSignals.
    """
    occupied: bool
    confidence: float                      # 0.0-1.0
    members_home: list[str]               # IDs de miembros detectados en casa
    signals_used: int                      # cuantas señales contribuyeron
    last_updated: float = field(default_factory=time.time)


# ── Intent (Layer 5) ──────────────────────────────────────────────────────────

@dataclass
class Intent:
    intent: IntentClass
    context: dict[str, Any]
    urgency: Urgency                   = Urgency.INFO
    subject: Optional[str]            = None
    constraints: dict[str, Any]       = field(default_factory=lambda: {
        "timeout_ms": 10_000,
        "require_confirmation": False,
    })
    intent_id: str                     = field(
        default_factory=lambda: f"int-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    )

    def to_dict(self) -> dict:
        return {
            "intent_id":   self.intent_id,
            "intent":      self.intent.value,
            "subject":     self.subject,
            "urgency":     self.urgency.value,
            "context":     self.context,
            "constraints": self.constraints,
        }


# ── Device event (Layer 5, device -> AI) ──────────────────────────────────────

@dataclass
class DeviceEvent:
    device_id: str
    event_id: str
    severity: Urgency
    data: dict[str, Any]              = field(default_factory=dict)
    timestamp: float                  = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "event_id":  self.event_id,
            "severity":  self.severity.value,
            "data":      self.data,
            "timestamp": self.timestamp,
        }


# ── Action plan (Layer 4 output) ──────────────────────────────────────────────

@dataclass
class DeviceAction:
    device_id: str
    action: str
    params: dict[str, Any]            = field(default_factory=dict)
    relevance_score: float            = 0.0

@dataclass
class ActionPlan:
    intent_id: str
    actions: list[DeviceAction]
    urgency: Urgency
    created_at: float                 = field(default_factory=time.time)

@dataclass
class ActionResult:
    device_id: str
    action: str
    success: bool
    response: Any                     = None
    error: Optional[str]             = None
    executed_at: float                = field(default_factory=time.time)

@dataclass
class IntentResult:
    intent_id: str
    success: bool
    results: list[ActionResult]
    failed_devices: list[str]         = field(default_factory=list)
    completed_at: float               = field(default_factory=time.time)


# ── Phased action plan (para secuencias ordenadas como emergencias) ────────────

@dataclass
class PhaseAction:
    """Una accion dentro de una fase del plan."""
    device_id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)

@dataclass
class Phase:
    """
    Un grupo de acciones que se ejecutan en paralelo.
    Las fases se ejecutan en orden secuencial con delay entre ellas.
    """
    name: str
    actions: list[PhaseAction]
    delay_after_ms: int = 0    # esperar X ms antes de ejecutar la siguiente fase

@dataclass
class PhasedActionPlan:
    intent_id: str
    phases: list[Phase]
    urgency: Urgency
    created_at: float = field(default_factory=time.time)


# ── Family profile (rutinas configurables por familia) ────────────────────────

@dataclass
class RoutineAction:
    """Una accion dentro de una rutina familiar."""
    tag: str                           # tag del dispositivo que debe ejecutarla
    action_type: str                   # tipo de actuador
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

@dataclass
class FamilyProfile:
    """
    Perfil de rutinas de una familia.
    Define que acciones ejecutar para cada rutina y a que hora.
    Cada familia configura el suyo — no hay valores impuestos.
    """
    family_name: str

    # Rutina de la manana — disparada por primer movimiento del dia
    routine_morning: list[RoutineAction] = field(default_factory=list)

    # Rutina de hora de dormir — disparada por scheduler
    routine_bedtime: list[RoutineAction] = field(default_factory=list)
    bedtime_hour:   int = 21
    bedtime_minute: int = 30

    # Modo ausente — disparado por sensor de garage o presencia
    routine_away: list[RoutineAction] = field(default_factory=list)

    # Metadata
    timezone: str = "America/Argentina/Cordoba"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        def action_list(actions):
            return [
                {"tag": a.tag, "action_type": a.action_type,
                 "params": a.params, "description": a.description}
                for a in actions
            ]
        return {
            "family_name":       self.family_name,
            "bedtime":           f"{self.bedtime_hour:02d}:{self.bedtime_minute:02d}",
            "timezone":          self.timezone,
            "routine_morning":   action_list(self.routine_morning),
            "routine_bedtime":   action_list(self.routine_bedtime),
            "routine_away":      action_list(self.routine_away),
        }
