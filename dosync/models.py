"""
DoSync — Core data models (Layers 3–5)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import time
import re
import uuid


# ── Enumerations ─────────────────────────────────────────────────────────────

class Urgency(str, Enum):
    """Execution priority of an Intent. Determines policy bypass behaviour."""
    INFO      = "info"
    WARNING   = "warning"
    ALERT     = "alert"
    EMERGENCY = "emergency"


class Severity(str, Enum):
    """Observable severity of a DeviceEvent or EventSpec condition.
    Intentionally separate from Urgency: a temperature anomaly may have
    severity=warning (the condition is notable) while the resulting intent
    carries urgency=emergency (act immediately). Conflating the two
    prevents expressing "low-severity emergency" or "high-severity routine"."""
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
    CONTEXT       = "context"      # contributes to high-level occupancy inference

class CertTier(str, Enum):
    BASIC     = "basic"      # Layers 1-3
    STANDARD  = "standard"   # Layers 1-4
    EMERGENCY = "emergency"  # All layers + override

class FailurePolicy(str, Enum):
    """
    Defines executor behaviour when an action fails.

    CONTINUE  — compntes
    ABORT     — detiene las acciones pendientes al primer fallo
    RETRY     — retry the failed action N times before continuing
    """
    CONTINUE = "continue"
    ABORT    = "abort"
    RETRY    = "retry"


class IntentClass(str):
    """
    Open string type for DoSync intent classes.

    The protocol defines the FORMAT of an intent class name, not its vocabulary.
    Any string matching ^[a-z][a-z0-9_]*$ is a valid intent class.

    Five universal intents are seeded into every hub at init time.
    Domain-specific intents (healthcare, retail, industrial, residential)
    are registered via POST /v1/intent-classes — no code changes required.

    .value returns self (str) for compatibility with Enum-style .value access.
    """

    _PATTERN = __import__('re').compile(r'^[a-z][a-z0-9_]*$')

    def __new__(cls, value: str) -> 'IntentClass':
        if not cls._PATTERN.match(str(value)):
            raise ValueError(
                f"Invalid intent class name '{value}'. "
                "Must match ^[a-z][a-z0-9_]*$ "
                "(lowercase letters, digits, underscores only)"
            )
        return super().__new__(cls, value)

    @property
    def value(self) -> str:
        """Compatibility with Enum-style .value access."""
        return str(self)

    def __repr__(self) -> str:
        return f"IntentClass({str(self)!r})"

    def __eq__(self, other) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self))


# ── Universal intent classes — seeded at hub init ─────────────────────────────
# These five are the only intents defined at the PROTOCOL level.
# They represent concepts valid in any physical environment regardless of domain.
# All other intents (residential, healthcare, industrial, etc.) are registered
# via POST /v1/intent-classes without any code changes.

IntentClass.ENSURE_SAFETY  = IntentClass("ensure_safety")   # Safety emergency
IntentClass.ALERT_ANOMALY  = IntentClass("alert_anomaly")   # Unexpected condition
IntentClass.CONTROL_ACCESS = IntentClass("control_access")  # Physical access control
IntentClass.REPORT_STATUS  = IntentClass("report_status")   # Status report
IntentClass.NOTIFY         = IntentClass("notify")          # Push information


class ContextSignalType(str, Enum):
    """Type of inference contributed by a context provider."""
    PRESENCE   = "presence"    # whether someone is home
    LOCATION   = "location"    # GPS location of a household member
    SLEEP      = "sleep"       # sleep state
    HEALTH     = "health"      # vital signs, physical activity
    ROUTINE    = "routine"     # daily routine pattern
    VEHICLE    = "vehicle"     # vehicle / garage state


# ── Capability manifest (Layer 3) ─────────────────────────────────────────────

@dataclass
class SensorSpec:
    id: str
    type: str                          # "temperature" | "boolean" | "motion" | etc.
    description: str = ""
    unit: Optional[str] = None
    range: Optional[list[float]] = None
    poll_interval_ms: int = 30_000
    # ── SENSOR-KIND (panel 2026-07-14, shipped 2026-07-17) ───────────────────
    # Not all sensors are alike, and collapsing them made report_status
    # genuinely ambiguous: a DHT measuring the room and a lamp reporting its own
    # brightness both "sense", but "read the environment" and "read every
    # device's self-state" are different questions. The kind distinguishes them
    # WITHOUT hiding anything — a lamp's brightness is real telemetry and stays
    # declared (hiding it would be the TV mistake: mutilating truth to encode a
    # preference).
    #   "environment"  — measures the world: temperature, motion, humidity, sun.
    #   "device_state" — reports the device's own condition: brightness, on/off,
    #                    position, a setpoint.
    # Default "environment" keeps every existing manifest byte-for-byte valid.
    # The grain is PER SENSOR, deliberately: a thermostat's current_temp
    # measures the room (environment) while its target_temp is a setpoint
    # (device_state) — a per-device rule would misclassify one of them.
    kind: str = "environment"

@dataclass
class ActuatorSpec:
    id: str
    type: str                          # "lock" | "light" | "unlock" | etc.
    description: str = ""
    params_schema: dict = field(default_factory=dict)
    # ── Execution model (orthogonal to params_schema) ─────────────────────────
    # "instant"  (default): fire-and-result, exactly as every actuator works today.
    # "long_running": the action takes time and has a lifecycle (see operations.py).
    # The default keeps every existing manifest byte-for-byte compatible.
    execution_model: str = "instant"
    # Optional richness flags — only meaningful when execution_model == "long_running".
    # A simple long-running device (e.g. an oven) declares none of these; a drone
    # declares all three. The device declares how rich it is; the hub adapts.
    supports_progress: bool = False    # hub can query intermediate progress
    supports_cancel: bool = False      # the operation can be cancelled
    emits_telemetry: bool = False      # device streams telemetry → enables sub-states + reconciliation
    # ── INDEPENDENT-OBSERVATION (panel design 2026-07-21) ─────────────────────
    # The manufacturer's NATURAL pairing: which sensor confirms this actuator's
    # effect (e.g. a lock's own bolt_position). Optional — most actuators declare
    # none. A deployment can add or override a CROSS-DEVICE binding on the intent
    # (the lock from vendor A verified by vendor B's door sensor), which wins:
    # the manufacturer cannot know sensors it does not ship with.
    verify_with: Optional["VerifyBinding"] = None

@dataclass
class EventSpec:
    """Severity of a device event condition (separate from Intent urgency)."""
    id: str
    severity: Severity
    description: str = ""

    def __post_init__(self):
        # Auto-coerce string → Severity enum (FastAPI parses dataclass fields as raw strings)
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)

@dataclass
class ContextSignal:
    """
    Declares that this device contributes to context inference.
    Ejemplo: un smartwatch contribuye a PRESENCE y HEALTH.
    """
    type: ContextSignalType
    description: str = ""
    confidence_weight: float = 1.0    # relative weight in occupancy inference (0.0-1.0)
                                      # phone GPS weighs more than a PIR for presence

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
    adapter: Optional[str]                 = None
    adapter_config: dict                   = field(default_factory=dict)
    #: What the device announced about ITSELF at discovery, kept verbatim.
    #:
    #: Not what the hub concluded — what the device said. An SSDP `NT` header
    #: reading `urn:bambulab-com:device:3dprinter:1` is the most authoritative
    #: statement a device makes about its own identity, and adoption used to
    #: discard it: the discoverer captured headers, description document and
    #: transport, and persisted only the address and service type. A model
    #: later asked to describe that printer received `"announcement": {}` and
    #: wrote an adapter for a completely different protocol.
    #:
    #: Deliberately opaque to the hub. Nothing here is parsed, matched or
    #: branched on — this project does not keep a catalogue of vendors, and
    #: storing the raw datum must not become the first step towards one. It is
    #: for whoever describes the device later, and for diagnosing in six months
    #: what a device claimed to be on the day it was adopted.
    discovery_evidence: dict               = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
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
                "events":    [{**e.__dict__, "severity": e.severity.value if hasattr(e.severity, "value") else e.severity}
                              for e in self.events],
                "context_signals": [
                    {**c.__dict__, "type": c.type.value}
                    for c in self.context_signals
                ],
            },
            "emergency_capable": self.emergency_capable,
            "cert_tier": self.cert_tier.value,
        }
        if self.adapter:
            d["adapter"] = self.adapter
        # `adapter_config` used to be emitted only alongside an adapter, so a
        # device with none — precisely the one that still needs describing —
        # serialised without the address and service type recorded when it was
        # adopted. The endpoint patched around it by reading the object; the
        # dict was still lying to every other caller.
        if self.adapter_config:
            d["adapter_config"] = self.adapter_config
        # Unconditional, unlike adapter_config: a device with no adapter is
        # exactly the one whose announcement someone will need in order to
        # write it one.
        if self.discovery_evidence:
            d["discovery_evidence"] = self.discovery_evidence
        return d

    def to_public_dict(self) -> dict:
        """
        Serialize manifest for public API responses.

        Identical to to_dict() but excludes adapter_config, which contains
        sensitive internal routing information (IP addresses, ports, tokens).
        Clients interact with the hub via the semantic intent layer and never
        need to know the physical address of a device.

        Use to_dict() for internal operations (DB persistence, adapter routing).
        Use to_public_dict() for all GET /v1/devices/* API responses.
        """
        d = self.to_dict()
        d.pop("adapter_config", None)
        return d


# ── Context model (occupancy inference and routines) ────────────────────────────

@dataclass
class PresenceSignal:
    """
    A single presence signal emitted by a context provider.
    The hub aggregates them to infer home occupancy state.
    """
    device_id: str
    signal_type: ContextSignalType
    present: bool                          # True = presence detected
    confidence: float                      # 0.0-1.0
    member_id: Optional[str] = None        # which household member this signal belongs to
    timestamp: float = field(default_factory=time.time)

@dataclass
class OccupancyState:
    """
    Inferred occupancy state of the deployment, computed by the hub
    a partir de multiples PresenceSignals.
    """
    occupied: bool
    confidence: float                      # 0.0-1.0
    members_home: list[str]               # IDs of household members currently detected home
    signals_used: int                      # how many signals contributed to this state
    last_updated: float = field(default_factory=time.time)


# ── Intent (Layer 5) ──────────────────────────────────────────────────────────

@dataclass
class Intent:
    intent: IntentClass
    context: dict[str, Any]
    urgency: Urgency                   = Urgency.INFO
    subject: Optional[str]            = None
    source: str                        = "api"    # who fired this intent: "api" | "mcp" | "hub" | "scheduler" | "gpio" | "recovery"
    constraints: dict[str, Any]       = field(default_factory=lambda: {
        "timeout_ms": 10_000,
        "require_confirmation": False,
    })
    intent_id: str                     = field(
        default_factory=lambda: f"int-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    )
    timestamp: float                   = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "intent_id":   self.intent_id,
            "intent":      self.intent.value,
            "subject":     self.subject,
            "urgency":     self.urgency.value,
            "source":      self.source,
            "context":     self.context,
            "constraints": self.constraints,
            "timestamp":   self.timestamp,
        }


# ── Device event (Layer 5, device -> AI) ──────────────────────────────────────

@dataclass
class DeviceEvent:
    device_id: str
    event_id: str
    severity: Severity
    data: dict[str, Any]              = field(default_factory=dict)
    timestamp: float                  = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "event_id":  self.event_id,
            "severity":  self.severity.value if hasattr(self.severity, "value") else self.severity,
            "data":      self.data,
            "timestamp": self.timestamp,
        }


# ── Action plan (Layer 4 output) ──────────────────────────────────────────────

class VerificationStatus(str, Enum):
    """INDEPENDENT-OBSERVATION (panel design 2026-07-21). Separate from success:
    success answers "did the device accept the command?"; verification answers
    "did an independent sensor confirm the effect happened?"."""
    UNVERIFIED   = "unverified"    # no check ran (opt-in absent, or check pending)
    VERIFIED     = "verified"      # sensor agreed with expected_reading in time
    CONTRADICTED = "contradicted"  # device said OK but the sensor disagreed
    UNVERIFIABLE = "unverifiable"  # the verifying sensor itself did not answer in time
    # A push-only sensor that reports ON CHANGE and did not report, because
    # nothing it watches changed (panel, Kim). Distinct from `unverifiable`: the
    # sensor is healthy and silent, which is not the same as absent, and an
    # operator chasing a broken sensor should not be sent after this one.
    NO_CHANGE_REPORTED = "no_change_reported"


@dataclass
class VerifyBinding:
    """Opt-in, declarative (NOT a rule language — panel decision, Sosa): one
    sensor, one expected reading, one deadline. Declared on the manifest (the
    manufacturer's natural pairing) and/or on the intent (deployment cross-link,
    which overrides)."""
    sensor_id: str
    expected_reading: Any
    deadline_s: float = 5.0
    #: Accept a reading the sensor PUSHED, if it arrived after the action was
    #: dispatched and within this many seconds. `None` (the default) means only
    #: a reading polled on demand counts, which is the behaviour before
    #: 2026-08-01 and stays the default deliberately.
    #:
    #: Push-only sensors (MQTT, GPIO) cannot be polled at all, so without this
    #: they can never verify anything and the hub returns `unverifiable` — honest
    #: but useless exactly where most sensors are. With it, a recent pushed
    #: reading counts as evidence, and the result records that it was weaker
    #: evidence (see VerificationResult.evidence).
    #:
    #: No global default is possible (panel, Aguirre): an ambient thermometer
    #: reporting every five minutes is fine, a door sensor reporting every five
    #: minutes is useless for confirming a lock. The binding declares it or
    #: nothing does.
    accept_cached_within_s: float | None = None


@dataclass
class VerificationResult:
    status: "VerificationStatus"
    sensor_id: str
    expected: Any
    observed: Any = None
    # Grade of independence (panel decision, Benítez): a sensor on a DIFFERENT
    # device_id than the actuator is genuine independent observation; the same
    # firmware reporting twice is weaker evidence. Recorded so an auditor knows
    # what "verified" actually means.
    independence: str = "independent_device"   # or "same_device"
    #: HOW the observation was obtained, because `verified` must not mean two
    #: different things (panel decision, Torres). A reading we asked for after
    #: acting is causally posterior to the action; a reading the device happened
    #: to send is weaker evidence — legitimate, but not the same, and an auditor
    #: has to be able to tell without reading the code.
    #:
    #:   "polled"  — the hub queried the sensor after the action
    #:   "pushed"  — the device sent it, after dispatch and within the window
    evidence: str = "polled"
    #: For pushed evidence: when the reading actually arrived. Absolute, not an
    #: age, because the comparison that matters is against the ACTION, not the
    #: clock — a reading that predates dispatch confirms nothing however recent.
    observed_at: float | None = None
    checked_at: float = field(default_factory=time.time)


@dataclass
class DeviceAction:
    device_id: str
    action: str
    params: dict[str, Any]            = field(default_factory=dict)
    relevance_score: float            = 0.0
    # INDEPENDENT-OBSERVATION: opt-in. None → behaves exactly as before.
    verify_with: Optional["VerifyBinding"] = None
    #: When this action was handed to an adapter. Set by the executor, not by
    #: the caller. Exists so verification can ask whether a PUSHED sensor
    #: reading arrived after the action or before it — a reading that predates
    #: dispatch describes the world before we acted and confirms nothing,
    #: however recent it is. Without this the freshness window would be measured
    #: against the clock, which is the wrong question.
    dispatched_at: Optional[float] = None

@dataclass
class ActionPlan:
    intent_id: str
    actions: list[DeviceAction]
    urgency: Urgency
    created_at: float                 = field(default_factory=time.time)
    failure_policy: "FailurePolicy"   = field(default=None)
    max_retries: int                  = 1
    # failure_policy=None → usa CONTINUE (backward compatible)

@dataclass
class ActionResult:
    device_id: str
    action: str
    success: bool
    response: Any                     = None
    error: Optional[str]             = None
    executed_at: float                = field(default_factory=time.time)
    aborted: bool                     = False  # True if cancelled by ABORT policy
    retries: int                      = 0      # retries attempted before final result
    # INDEPENDENT-OBSERVATION: separate from success (panel decision, Morales).
    # None when the action declared no verify_with. success=True with
    # verification.status=CONTRADICTED is a real, valuable state: the device
    # accepted the command but the world did not change.
    verification: Optional["VerificationResult"] = None
    # SIMULATION: whether the hub actually reached the device, kept separate
    # from `success` because they answer different questions. `success=False`
    # means something went wrong; `simulated=True` means nothing went anywhere.
    # Before this field the two were indistinguishable to every reader: a
    # device with no adapter fell to the SimulatedExecutor and came back
    # success=True, and the reference deployment ran an SMS notifier that way
    # for an unknown length of time while every log line said the intent had
    # executed. The Data layer does not lie — including by omission.
    #
    # Three adapters already marked simulation inside `response`; that is the
    # right instinct in the wrong place. A caller should not have to know which
    # adapter answered in order to learn whether anything happened.
    simulated: bool                   = False
    #: Why, when simulated. One of SIMULATION_REASONS — the operator's reaction
    #: differs: a missing adapter is a registration mistake, an unavailable one
    #: is an install or a network problem, and a deliberate simulation is fine.
    simulated_reason: Optional[str]   = None


#: Why an action was simulated rather than executed.
SIMULATION_REASONS = (
    "no_adapter_declared",    # the manifest names no adapter for this device
    "adapter_unavailable",    # an adapter is named but could not act (missing
                              # library, unreachable device, simulated mode)
    "explicit_simulation",    # simulation was asked for — certification,
                              # evaluation corpora, development without hardware
)


@dataclass
class IntentResult:
    intent_id: str
    success: bool
    results: list[ActionResult]
    failed_devices: list[str]         = field(default_factory=list)
    aborted_devices: list[str]        = field(default_factory=list)
    completed_at: float               = field(default_factory=time.time)
    failure_policy_applied: str       = "continue"
    status: str                       = "success"
    rejected_actions: list            = field(default_factory=list)
    # Long-running operations started by this intent (execution_model). Each entry:
    # {operation_id, device_id, state}. EMPTY for intents that only triggered instant
    # actions → a client that predates operations sees an identical IntentResult.
    # The presence of entries signals "these actions started and are still running;
    # track them by operation_id". (Querying/cancelling them is a separate API step.)
    operations: list                  = field(default_factory=list)
    # "success"        — all actions completed successfully
    # "partial"        — some actions failed or were rejected, rest continued
    # "partial_abort"  — some actions executed, rest aborted by ABORT policy
    # "failed"         — all actions failed or intent was blocked
    # "retry_exhausted"— retries exhausted, result is failure
    # "rejected_invalid_params" — every action rejected by param validation (v0.3)
    # "accepted"       — the intent started one or more long-running operations that
    #   are still in progress (execution_model). Not success (not done) nor failed
    #   (not failed) — accepted and running. Only appears when `operations` is non-empty.
    # rejected_actions: actions dropped because their params violated the
    #   actuator's JSON Schema — distinct from failed_devices (device didn't
    #   respond). Each entry: {device_id, action, reason}.


# ── Phased action plan (for ordered sequences like multi-phase emergencies) ─────

@dataclass
class PhaseAction:
    """A single action within a plan phase."""
    device_id: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)

@dataclass
class Phase:
    """
    A group of actions executed in parallel.
    Phases execute sequentially with a configurable delay between them.
    """
    name: str
    actions: list[PhaseAction]
    delay_after_ms: int = 0    # wait X ms before executing the next phase

@dataclass
class PhasedActionPlan:
    intent_id: str
    phases: list[Phase]
    urgency: Urgency
    created_at: float = field(default_factory=time.time)


# ── Family profile (per-family configurable routines) ───────────────────────────

@dataclass
class RoutineAction:
    """A single action within a family routine."""
    tag: str                           # device tag that should execute this action
    action_type: str                   # actuator type
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""

@dataclass
class FamilyProfile:
    """
    Per-family routine profile.
    Defines which actions to execute for each routine and at what time.
    Each family configures their own — no values are imposed.
    """
    family_name: str

    # Morning routine — triggered by the first motion detection of the day
    routine_morning: list[RoutineAction] = field(default_factory=list)

    # Bedtime routine — time-based; fired by an external scheduler client
    routine_bedtime: list[RoutineAction] = field(default_factory=list)
    bedtime_hour:   int = 21
    bedtime_minute: int = 30

    # Away mode — triggered by garage sensor or presence signal
    routine_away: list[RoutineAction] = field(default_factory=list)

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

    @classmethod
    def from_dict(cls, data: dict) -> "FamilyProfile":
        """Rebuild a FamilyProfile from to_dict() output — the missing round-trip.

        Added 2026-07-14: the profile was persisted by set_family_profile() but
        NEVER restored on startup (db.load_family_profile() existed and nothing
        called it), so every hub restart silently dropped it — even though
        _restore_from_db's docstring promised the profile survives restarts.

        Note the asymmetry this must absorb: to_dict serializes bedtime as an
        "HH:MM" string, so it is parsed back into hour/minute here.
        """
        def action_list(items):
            return [
                RoutineAction(
                    tag=a["tag"],
                    action_type=a["action_type"],
                    params=a.get("params", {}) or {},
                    description=a.get("description", ""),
                )
                for a in (items or [])
            ]

        bedtime = data.get("bedtime", "21:30")
        try:
            _bh, _bm = (int(x) for x in str(bedtime).split(":", 1))
        except (ValueError, AttributeError):
            _bh, _bm = 21, 30

        return cls(
            family_name=data.get("family_name", ""),
            routine_morning=action_list(data.get("routine_morning")),
            routine_bedtime=action_list(data.get("routine_bedtime")),
            routine_away=action_list(data.get("routine_away")),
            bedtime_hour=_bh,
            bedtime_minute=_bm,
            timezone=data.get("timezone", "America/Argentina/Cordoba"),
        )
