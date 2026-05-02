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

class CertTier(str, Enum):
    BASIC     = "basic"      # Layers 1–3
    STANDARD  = "standard"   # Layers 1–4
    EMERGENCY = "emergency"  # All layers + override

class IntentClass(str, Enum):
    ENSURE_SAFETY   = "ensure_safety"
    NOTIFY_FAMILY   = "notify_family"
    REPORT_STATUS   = "report_status"
    SET_ENVIRONMENT = "set_environment"
    CONTROL_ACCESS  = "control_access"
    MONITOR_HEALTH  = "monitor_health"


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
class CapabilityManifest:
    device_id: str
    device_name: str
    manufacturer: str
    model: str
    firmware: str
    category: DeviceCategory
    tags: list[str]
    sensors: list[SensorSpec]          = field(default_factory=list)
    actuators: list[ActuatorSpec]      = field(default_factory=list)
    events: list[EventSpec]            = field(default_factory=list)
    emergency_capable: bool            = False
    cert_tier: CertTier                = CertTier.BASIC
    dosync_version: str                = "0.1"

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
            },
            "emergency_capable": self.emergency_capable,
            "cert_tier": self.cert_tier.value,
        }


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


# ── Device event (Layer 5, device → AI) ──────────────────────────────────────

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
