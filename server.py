"""
DoSync Hub — REST API Server
Corre con: uvicorn server:app --host 0.0.0.0 --port 47200 --reload
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import json
import os
import re
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from dosync.hub import DoSyncHub
from dosync import metrics as M
from dosync.executor import SimulatedExecutor
from dosync.auth import AuthManager, set_auth_manager, DeviceAuthManager, set_device_auth_manager
from dosync.auth_fastapi import require_auth
from dosync.security import get_status as get_pki_status
from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
    DeviceEvent, EventSpec, Intent, IntentClass, SensorSpec, Urgency, Severity,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)

_certify_mode = os.environ.get("DOSYNC_CERTIFY", "").lower() in ("1", "true", "yes")
# ── Estado global del hub ─────────────────────────────────────────────────────

hub      = DoSyncHub(
    db_path=":memory:" if _certify_mode else os.environ.get("DOSYNC_DB", "dosync.db")
)

# ── Async intent store ────────────────────────────────────────────────────────
# In-memory store for async intent results. TTL: 5 minutes.
import time as _time
_intent_store: dict = {}
_INTENT_STORE_TTL = 300  # seconds

def _store_cleanup():
    """Remove intent results older than TTL."""
    now = _time.time()
    expired = [k for k, v in _intent_store.items() if now - v["created_at"] > _INTENT_STORE_TTL]
    for k in expired:
        del _intent_store[k]
    # Idempotency keys share the same retention window as intent results.
    idem_expired = [k for k, v in _idempotency_store.items() if now - v["created_at"] > _INTENT_STORE_TTL]
    for k in idem_expired:
        del _idempotency_store[k]


# ── Idempotency (protocol v0.2) ───────────────────────────────────────────────
# Maps a client-supplied idempotency key -> {body_hash, intent_id, created_at}.
# Delivery model: at-least-once with optional deduplication.
#   - No key supplied  -> every request is unique (v0.1 behavior, unchanged).
#   - Key + same body   -> return the cached intent_id, do NOT re-execute.
#   - Key + different body -> 409 Conflict (anti-suppression: a key cannot be
#     reused for different content, closing the intent-suppression attack).
# Retention matches _INTENT_STORE_TTL so a key lives as long as its result.
_idempotency_store: dict = {}

def _intent_body_hash(req: "IntentRequest") -> str:
    """Stable SHA-256 of the semantically meaningful intent fields.
    Excludes idempotency_key itself so the same logical intent hashes equally."""
    import hashlib, json as _json
    payload = {
        "intent":  req.intent,
        "urgency": req.urgency,
        "subject": req.subject,
        "source":  req.source,
        "context": req.context,
    }
    encoded = _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

# ── Executor ──────────────────────────────────────────────────────────────────
# _certify_mode is defined before DoSyncHub initialization (see above)

if _certify_mode:
    logging.getLogger("dosync.server").warning(
        "DOSYNC_CERTIFY mode active — SimulatedExecutor in use. "
        "No physical devices will be contacted. Do NOT use in production."
    )
    executor = SimulatedExecutor(failure_rate=0.0)
else:
    try:
        from dosync.adapters import AdapterExecutor
        from dosync.adapters.wiz import WiZAdapter
        executor = AdapterExecutor(hub, fallback_to_simulated=True)
        executor.register(WiZAdapter(hub=hub))
        # BLE adapter — opt-in. Only registered when DOSYNC_BLE_ENABLED=true, since
        # not every hub has a Bluetooth radio. Universal: one adapter drives any
        # BLE device via the manifest's action→characteristic map.
        if os.environ.get("DOSYNC_BLE_ENABLED", "false").lower() == "true":
            try:
                from dosync.adapters.ble import BLEAdapter
                executor.register(BLEAdapter(hub=hub))
                logging.getLogger("dosync.server").info("BLEAdapter registered")
            except Exception as _ble_e:
                logging.getLogger("dosync.server").warning(
                    "BLEAdapter registration failed: %s", _ble_e)
        # MAVLink adapter — opt-in. Only registered when DOSYNC_MAVLINK_ENABLED=true,
        # since most hubs don't drive an aerial/ground vehicle and the adapter needs
        # pymavlink. Each vehicle declares its own endpoint in its manifest
        # adapter_config["connection"] (e.g. "udp:127.0.0.1:14550" for SITL, or a
        # serial string for a real radio) — the adapter is identical for both; only
        # the connection string changes. Kept off the default path so a hub without a
        # drone never imports pymavlink or opens a MAVLink link.
        if os.environ.get("DOSYNC_MAVLINK_ENABLED", "false").lower() == "true":
            try:
                from dosync.adapters.mavlink import MAVLinkAdapter
                executor.register(MAVLinkAdapter(hub=hub))
                logging.getLogger("dosync.server").info("MAVLinkAdapter registered")
            except Exception as _mav_e:
                logging.getLogger("dosync.server").warning(
                    "MAVLinkAdapter registration failed: %s", _mav_e)
        from dosync.adapters.homeassistant import HABridge
        _ha_url = os.environ.get("HA_URL", "http://localhost:8123")
        _ha_token = os.environ.get("HA_TOKEN", "")
        if _ha_token:
            ha_bridge = HABridge(ha_url=_ha_url, ha_token=_ha_token, hub=hub)
            executor.register(ha_bridge)
            logging.getLogger("dosync.server").info("HABridge registered")
        logging.getLogger("dosync.server").info(
            "AdapterExecutor initialized with WiZAdapter"
        )
    except Exception as _e:
        logging.getLogger("dosync.server").warning(
            "AdapterExecutor init failed (%s) — falling back to SimulatedExecutor", _e
        )
        executor = SimulatedExecutor(failure_rate=0.0)

# ── Policy Engine ────────────────────────────────────────────────────────────
# Imported out here: the handler below must be able to name it, and a broken
# deployment policy file has to stop the hub rather than be caught by the generic
# handler and downgraded to a warning.
from dosync.policy_config import PolicyConfigError

try:
    # Only infrastructure policies are constructed here. The deployment-specific
    # ones (NeverAfterHours, RequireConfirmation, DeviceExclusion, Geofence...)
    # are built by policy_config from the deployer's file — see below.
    from dosync.policies import (
        PolicyEngine, IntentRateLimitPolicy, DeviceActuatorRateLimitPolicy,
    )
    policy_engine = PolicyEngine()
    from dosync.policies import ConflictResolutionPolicy, ContextualWeightingPolicy
    policy_engine.add(IntentRateLimitPolicy())          # priority 0 — source rate limit
    _device_rate_policy = DeviceActuatorRateLimitPolicy()
    _device_rate_policy.set_db(hub.db)                  # wire DB for restart-safe persistence
    policy_engine.add(_device_rate_policy)              # priority 5 — per-device rate limit
    policy_engine.add(ContextualWeightingPolicy())
    policy_engine.add(ConflictResolutionPolicy(hub))

    # ── Deployment policies (POL-1) ──────────────────────────────────────────
    # Everything above is INFRASTRUCTURE: rate limiting, conflict resolution and
    # contextual weighting carry no deployment values — they are part of what the
    # reference hub is, and every hub wants them.
    #
    # What used to sit HERE was not: a NeverAfterHoursPolicy blocking unlock
    # between 00:00 and 06:00, and a RequireConfirmationPolicy on alarms. Real
    # preferences of one house, baked into the reference implementation that
    # everyone else runs. Per the 2026-07-12 panel, device preferences are
    # DEPLOYMENT configuration: the protocol defines how intent maps to
    # capability; what should act in YOUR building is yours to declare.
    #
    # They now live in a file the deployer owns (see examples/policies.deployment.json,
    # which contains exactly those two, ready to use):
    #
    #     DOSYNC_POLICIES=/etc/dosync/policies.json
    #
    # No file configured = no deployment policies. That is a legitimate state, not
    # a degraded one: the protocol has no opinion about your house. A file that is
    # configured but broken raises and stops the hub — see policy_config for why a
    # policy must never fail quietly.
    from dosync import policy_config
    _policies_path = policy_config.configured_path()
    if _policies_path:
        _loaded = policy_config.load_into(policy_engine, _policies_path, hub=hub)
        logging.getLogger("dosync.server").info(
            "Deployment policies loaded from %s: %s",
            _policies_path, ", ".join(p.name for p in _loaded) or "(none declared)")
        # EMERGENCY-UNSAT part b: tell the operator NOW if these rules would
        # empty an emergency for the current registry — not the night it fires.
        for _w in policy_config.lint_emergency_satisfiability(hub, _loaded):
            logging.getLogger("dosync.server").warning("POLICY LINT: %s", _w)
    else:
        logging.getLogger("dosync.server").info(
            "No deployment policies configured (DOSYNC_POLICIES unset) — "
            "running with infrastructure policies only")
    # Metrics: count policy decisions without touching policies.py (same wrap
    # pattern used below for audit_log.append).
    _original_policy_evaluate = policy_engine.evaluate
    def _metered_policy_evaluate(intent, plan):
        result = _original_policy_evaluate(intent, plan)
        try:
            M.policy_decisions_total.inc({"decision": result.decision.value})
        except Exception:
            pass
        return result
    policy_engine.evaluate = _metered_policy_evaluate
    hub.policy_engine = policy_engine
    logging.getLogger("dosync.server").info("PolicyEngine initialized with %d policies", len(policy_engine.list_policies()))
except PolicyConfigError:
    # NEVER degrade a broken policy file into "no policies". The deployer
    # explicitly declared restrictions; if they cannot be honored, the only safe
    # answer is to refuse to start. Reaching the generic handler below would log a
    # warning and run the hub UNPROTECTED — with the operator believing otherwise.
    # (2026-07-14: the loader was written to fail loudly and this outer
    # `except Exception` silently defeated it. The hub started, minus the policies.)
    raise
except Exception:
    # POL-2, closed 2026-07-15 — by incident, not by argument. This handler used
    # to log one warning and continue. Then a NameError in the setup block (a
    # log call before `log` existed) was swallowed right before the
    # `hub.policy_engine = policy_engine` line: the engine was built, all seven
    # policies registered and logged, and the hub NEVER ATTACHED to it. Production
    # ran with hub.policy_engine=None — no deployment policies, no rate limits,
    # no conflict resolution — while an emergency intent drove devices the
    # operator had absolutely excluded. Nothing in this block has environmental
    # failure modes (core imports, in-memory construction; the deployment file is
    # already fatal via PolicyConfigError above), so any failure here means the
    # hub would run without its policy layer. That hub must not run.
    logging.getLogger("dosync.server").critical(
        "PolicyEngine setup failed — refusing to start without the policy layer",
        exc_info=True)
    raise

# ── Notification adapter ──────────────────────────────────────────────────────
try:
    from dosync.adapters.notifications import NotificationAdapter
    notifier = NotificationAdapter()
    executor.register(notifier)
    logging.getLogger("dosync.server").info("NotificationAdapter registered")
except Exception as _e:
    notifier = None
    logging.getLogger("dosync.server").warning("Notifications not available: %s", _e)

# ── Auth setup ────────────────────────────────────────────────────────────────
_auth_enabled = os.environ.get("DOSYNC_AUTH", "true").lower() != "false"
_auth_manager = AuthManager(hub.db, enabled=_auth_enabled)
set_auth_manager(_auth_manager)

# ── MQTT Adapter (optional) ──────────────────────────────────────────────────
# Activated via DOSYNC_MQTT_BROKER env var. Requires: pip install paho-mqtt
# and a running MQTT broker (Mosquitto recommended).
_mqtt_broker = os.environ.get("DOSYNC_MQTT_BROKER", "")
if _mqtt_broker:
    try:
        from dosync.adapters.mqtt import MQTTAdapter
        _mqtt_adapter = MQTTAdapter(hub=hub)
        executor.register(_mqtt_adapter)
        # connect() is async — deferred to lifespan startup
        logging.getLogger("dosync.server").info(
            "MQTTAdapter registered — will connect to %s on startup", _mqtt_broker
        )
    except Exception as _mqtt_e:
        logging.getLogger("dosync.server").warning(
            "MQTTAdapter init failed (%s) — MQTT transport disabled", _mqtt_e
        )
else:
    _mqtt_adapter = None

# ── Device Arbiter — emergency preemption at the execution layer ──────────────
# Wrap the fully-configured executor so an emergency-urgency write is device-final
# with respect to any lower-urgency action it overlaps with (dosync/device_arbiter.py,
# spec/CONSISTENCY-MODEL.md §3). Wrapped in ALL modes (adapter / simulated / certify)
# so behaviour is identical in production and certification. The hub releases claims
# on intent completion. `_adapter_executor` keeps the unwrapped reference for the
# isinstance checks below (the arbiter delegates everything else transparently).
from dosync.device_arbiter import DeviceArbiter
_adapter_executor = executor
executor = DeviceArbiter(executor, audit_hook=hub.audit_log.append)
logging.getLogger("dosync.server").info("DeviceArbiter active — emergency preemption enabled")

# ── External Resolver (optional) ─────────────────────────────────────────────
# If DOSYNC_RESOLVER_URL is set, the hub delegates intent resolution to an
# external HTTP service implementing the DoSync External Resolver Protocol.
# See spec/RESOLVER-SPEC-v0.3.md §5 and docs/ADAPTER-GUIDE.md for the contract.
# Falls back to CapabilityMatchingResolver if the external service is unreachable.
_resolver_url = os.environ.get("DOSYNC_RESOLVER_URL", "")
if _resolver_url:
    try:
        from dosync.hub import ExternalResolver
        _hub_id = getattr(hub, "hub_id", "")  # app.state not available at module level
        hub.resolver = ExternalResolver(hub.registry, _resolver_url, hub_id=_hub_id, hub=hub)
        logging.getLogger("dosync.server").info(
            "ExternalResolver configured: %s", _resolver_url
        )
    except Exception as _ext_e:
        logging.getLogger("dosync.server").warning(
            "ExternalResolver init failed (%s) — using CapabilityMatchingResolver", _ext_e
        )

# Device authentication manager
hub.db.init_device_tokens_table()
hub.db.init_emergency_snapshots_table()
hub.db.init_operations_table()
_device_auth_manager = DeviceAuthManager(hub.db)
set_device_auth_manager(_device_auth_manager)

# ── Multi-hub monitor (Phase A) ───────────────────────────────────────────────
# Active only when this hub is configured as a standby. On a primary it stays
# None and all multi-hub endpoints report role=primary with no monitor. The
# monitor is the pure decision core (dosync/hub_monitor.py); the polling loop
# that feeds it observations is started in the lifespan/startup when standby.
from dosync.hub_monitor import HubMonitor, HeartbeatObservation, MonitorState

_hub_role = os.environ.get("DOSYNC_HUB_ROLE", "primary").lower()
_primary_url = os.environ.get("DOSYNC_PRIMARY_URL")  # peer to watch (standby only)
_hub_monitor: Optional[HubMonitor] = None
if _hub_role == "standby":
    _failure_threshold = int(os.environ.get("DOSYNC_FAILURE_THRESHOLD", "3"))
    _hub_monitor = HubMonitor(
        failure_threshold=_failure_threshold,
        local_device_count=len(hub.registry.all()),
    )


# ── WebSocket manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    """Gestiona todas las conexiones WebSocket activas."""

    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logging.getLogger("dosync.ws").info(
            "Client connected — total: %d", len(self._connections)
        )

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._connections.remove(ws)
        except ValueError:
            pass
        logging.getLogger("dosync.ws").info(
            "Client disconnected — total: %d", len(self._connections)
        )

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Emite un evento a todos los clientes conectados."""
        if not self._connections:
            return
        message = json.dumps({"type": event_type, "data": data})
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    @property
    def active_connections(self) -> int:
        return len(self._connections)


ws_manager = ConnectionManager()


# ── Hook hub events into WebSocket broadcast ──────────────────────────────────

_original_on_event = hub.receive_event.__wrapped__ if hasattr(hub.receive_event, '__wrapped__') else None

async def _ws_event_handler(event):
    await ws_manager.broadcast("device_event", {
        "device_id": event.device_id,
        "event_id":  event.event_id,
        "severity":  event.severity.value,
        "data":      event.data,
        "timestamp": event.timestamp,
    })

hub.on_event(_ws_event_handler)


# Patch audit log to broadcast intent executions
_original_audit_append = hub.audit_log.append

def _patched_audit_append(entry: dict) -> str:
    result = _original_audit_append(entry)
    import asyncio
    entry_type = entry.get("type", "")
    if entry_type in ("intent_executed", "phase_executed", "presence_updated"):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(ws_manager.broadcast(entry_type, entry))
        except Exception:
            pass
    return result

hub.audit_log.append = _patched_audit_append

def on_event(event: DeviceEvent):
    logging.getLogger("dosync.server").info(
        "Event received: %s from %s [%s]",
        event.event_id, event.device_id, event.severity.value
    )

hub.on_event(on_event)


# ── Schemas de entrada ────────────────────────────────────────────────────────

class SensorIn(BaseModel):
    id: str
    type: str
    description: str = ""
    unit: Optional[str] = None
    poll_interval_ms: int = 30000
    # SENSOR-KIND: "environment" (measures the world) | "device_state" (reports
    # the device's own condition). Default keeps every existing client valid.
    kind: str = "environment"

class ActuatorIn(BaseModel):
    id: str
    type: str
    description: str = ""
    params_schema: dict = {}  # JSON Schema (draft 2020-12) for this action's params

class EventSpecIn(BaseModel):
    id: str
    severity: str
    description: str = ""

class RegisterDeviceRequest(BaseModel):
    device_id: str
    device_name: str
    manufacturer: str
    model: str
    firmware: str
    category: str
    tags: list[str]
    sensors: list[SensorIn] = []
    actuators: list[ActuatorIn] = []
    events: list[EventSpecIn] = []
    emergency_capable: bool = False
    cert_tier: str = "basic"
    adapter:         Optional[str] = None  # which adapter drives this device (e.g. "mavlink", "wiz")
    adapter_config:  dict = {}             # adapter-specific config (e.g. {"connection": "udp:127.0.0.1:14550"})
    device_token:    Optional[str] = None  # token de autenticación del dispositivo
    certificate_pem: Optional[str] = None  # PEM certificate for mTLS auth (optional)

class PresenceSignalRequest(BaseModel):
    device_id: str
    signal_type: str
    present: bool
    confidence: float = 0.7
    member_id: Optional[str] = None

class IntentRequest(BaseModel):
    intent: str
    urgency: str = "info"
    subject: Optional[str] = None
    source: str = "api"
    context: dict[str, Any] = {}
    idempotency_key: Optional[str] = None  # opt-in dedup (protocol v0.2). UUID recommended.

class EventRequest(BaseModel):
    device_id: str
    event_id: str
    severity: str
    data: dict[str, Any] = {}


# Heartbeat report bounds (parada técnica 2026-07-21, Sosa). Module-level, not
# class attributes: in Pydantic v2 a name starting with "_" inside a BaseModel
# becomes a ModelPrivateAttr, not an int. Bounds fit a generous real self-report
# (battery, rssi, firmware, uptime, a few custom fields) with wide margin while
# making abuse impossible — a report is telemetry about ONE device, not a data
# channel.
_HEARTBEAT_MAX_REPORT_KEYS  = 32
_HEARTBEAT_MAX_REPORT_BYTES = 4096


class HeartbeatRequest(BaseModel):
    device_id: str
    # A device MAY volunteer a structured self-report (battery %, rssi, firmware,
    # uptime…). Optional and free-form: the hub stores it verbatim and takes no
    # position on its CONTENTS — but it does bound its SIZE. "No position on the
    # contents" is not "accepts unbounded input": a compromised or buggy device
    # could otherwise push a multi-megabyte report on every heartbeat, persisted.
    report: dict[str, Any] = {}

    @field_validator("report")
    @classmethod
    def _bound_report(cls, v: dict) -> dict:
        if len(v) > _HEARTBEAT_MAX_REPORT_KEYS:
            raise ValueError(
                f"heartbeat report has {len(v)} keys; max {_HEARTBEAT_MAX_REPORT_KEYS}")
        try:
            size = len(json.dumps(v).encode("utf-8"))
        except (TypeError, ValueError) as e:
            raise ValueError(f"heartbeat report is not JSON-serializable: {e}") from e
        if size > _HEARTBEAT_MAX_REPORT_BYTES:
            raise ValueError(
                f"heartbeat report is {size} bytes; max {_HEARTBEAT_MAX_REPORT_BYTES}")
        return v


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("dosync.server")
    log.info("DoSync Hub started on port 47200")
    first_token = _auth_manager.ensure_default_key()
    if first_token:
        print("\n" + "="*60)
        print("  DoSync Hub — First Run")
        print("  Your API key (save this — shown only once):")
        print(f"\n  {first_token}\n")
        print("  Use: Authorization: Bearer <token>")
        print("  Or:  DOSYNC_AUTH=false to disable (dev only)")
        print("="*60 + "\n")
    elif not _auth_enabled:
        log.warning("Auth DISABLED — do not use in production")
    # PKI status check
    try:
        from dosync.security import get_status as get_pki_status
        _pki = get_pki_status()
        if _pki.is_ready:
            log.info("=== TLS/PKI: ACTIVE === CA valid %d days · Hub cert valid %d days · %d adapter cert(s)",
                     _pki.ca_info.days_until_expiry, _pki.hub_info.days_until_expiry, len(_pki.adapter_certs))
            if _pki.hub_info.is_expiring_soon:
                log.warning("Hub cert expires in %d days — run: python3 -m dosync.security renew hub", _pki.hub_info.days_until_expiry)
        else:
            log.warning("=== TLS/PKI: NOT CONFIGURED === Hub running on plain HTTP. Run: bash setup_pki.sh")
    except Exception as _pki_e:
        log.warning("PKI status check failed: %s", _pki_e)
    # ── Startup recovery — re-disparar emergencias activas pre-corte ─────────
    try:
        active = hub.db.get_active_emergency_snapshots()
        if active:
            log.warning("STARTUP RECOVERY: %d emergency intent(s) were active before shutdown", len(active))
            for snap in active:
                age_minutes = ((__import__('time').time() - snap['fired_at']) / 60)
                if age_minutes < 60:  # solo re-disparar si fue hace menos de 1 hora
                    log.warning("Re-firing intent '%s' (was active %.1f min ago)", snap['intent_class'], age_minutes)
                    try:
                        from dosync.models import Intent, IntentClass, Urgency
                        import uuid, time as _time
                        recovery_intent = Intent(
                            intent=IntentClass(snap['intent_class']),
                            intent_id=f"recovery-{uuid.uuid4().hex[:8]}",
                            urgency=Urgency(snap['urgency']),
                            source="recovery",
                            context={**snap['context'], "recovery": True, "original_intent_id": snap['intent_id']},
                        )
                        hub.fire_intent(recovery_intent)
                        hub.db.resolve_emergency_snapshot(snap['intent_id'])
                        log.info("Recovery intent '%s' fired successfully", snap['intent_class'])
                    except Exception as _re:
                        log.error("Failed to re-fire recovery intent '%s': %s", snap['intent_class'], _re)
                else:
                    log.info("Skipping stale emergency snapshot '%s' (%.1f min ago — too old)", snap['intent_class'], age_minutes)
                    hub.db.resolve_emergency_snapshot(snap['intent_id'])
        hub.db.clear_old_snapshots(max_age_hours=24)
    except Exception as _recovery_e:
        log.error("Startup recovery failed: %s", _recovery_e)

    # ── Background state refresher ──────────────────────────────────────────
    # Periodically queries device state via get_state() — active health probing.
    # Hub-owned, so it runs under ANY resolver (it was gated on the resolver
    # being StateAwareResolver, which production never is: it never ran).
    _refresh_task = None
    try:
        from dosync.adapters import AdapterExecutor
        # The refresher is HUB-owned and must run under ANY resolver. It used to
        # be gated on `isinstance(hub.resolver, StateAwareResolver)`, which is
        # always False in production (ExternalResolver) — so it never ran, and
        # said so only at debug level. The only real requirement is an
        # AdapterExecutor, which is what sources the adapters to probe.
        if isinstance(_adapter_executor, AdapterExecutor):
            _refresh_task = asyncio.create_task(
                hub.start_state_refresh(_adapter_executor)
            )
            log.info("Background state refresher started (hub-owned, active health probing)")
        else:
            log.warning("Background state refresher NOT started: executor is %s, "
                        "not an AdapterExecutor — device health will be passive only",
                        type(_adapter_executor).__name__)
    except Exception as _refresh_e:
        log.warning("Failed to start background state refresher: %s", _refresh_e)

    # Purge old terminal operations. clear_old_snapshots was wired here and
    # clear_old_operations never was, so the operations table grew forever.
    # Own try/except on purpose: a failure here must be reported as itself, not
    # swallowed by an unrelated handler. Active (non-terminal) operations are
    # never purged by age — a long-running op is exactly what must survive for
    # reconciliation.
    try:
        _purged_ops = hub.db.clear_old_operations(max_age_hours=24)
        if _purged_ops:
            log.info("Purged %d terminal operation(s) older than 24h", _purged_ops)
    except Exception as _ops_purge_e:
        log.warning("Failed to purge old operations: %s", _ops_purge_e)

    # Purge expired rate limit events from DB (prevents unbounded table growth)
    try:
        purged = hub.db.purge_rate_limit_events(window_seconds=60)
        if purged > 0:
            log.info("Startup: purged %d expired rate limit event(s) from DB", purged)
    except Exception as _pe:
        log.warning("Could not purge rate limit events: %s", _pe)

    # MQTT adapter startup (async connect, after event loop is running)
    if _mqtt_adapter is not None:
        try:
            await _mqtt_adapter.connect()
        except Exception as _mc:
            log.warning("MQTTAdapter startup connect failed: %s", _mc)

    yield

    # Cancel background refresher on shutdown
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass

    # MQTT adapter shutdown
    if _mqtt_adapter is not None:
        try:
            await _mqtt_adapter.disconnect()
        except Exception:
            pass

    log.info("DoSync Hub shutting down")

app = FastAPI(
    title="DoSync Hub",
    description=(
        "DoSync Protocol — REST API\n\n"
        "El hub central que conecta la IA con los gadgets del hogar.\n"
        "Protocolo abierto · Apache 2.0 · github.com/dosync/protocol"
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# ── API versioning ─────────────────────────────────────────────────────────────
# Protocol version: the DoSync semantic protocol version (intent format, manifest schema)
# API version:      the REST API version (URL prefix /v1/)
# These are exposed as response headers on every request so clients can detect
# the version without parsing the URL or the response body.
DOSYNC_PROTOCOL_VERSION = "0.4"
DOSYNC_API_VERSION = "1"

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class VersionHeaderMiddleware(BaseHTTPMiddleware):
    """
    Adds DoSync version headers to every HTTP response.

    Headers:
      X-DoSync-Protocol-Version  The semantic protocol version (e.g. 0.1)
      X-DoSync-API-Version       The REST API version (e.g. 1, matching /v1/)

    Clients SHOULD read these headers to detect protocol version without
    parsing the URL. Future breaking API changes will increment the API version
    and introduce a new URL prefix (/v2/) while keeping /v1/ active during
    the deprecation window.
    """
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-DoSync-Protocol-Version"] = DOSYNC_PROTOCOL_VERSION
        response.headers["X-DoSync-API-Version"] = DOSYNC_API_VERSION
        return response

app.add_middleware(VersionHeaderMiddleware)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if _auth_enabled and not _auth_manager.verify(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws_manager.connect(ws)
    try:
        await ws.send_text(json.dumps({
            "type": "connected",
            "data": {
                "devices":    len(hub.registry.all()),
                "hub_version": "0.4.0",
                "protocol":   f"dosync/{DOSYNC_PROTOCOL_VERSION}",
            }
        }))
        import asyncio
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send_text(json.dumps({"type": "ping", "data": {}}))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_manager.disconnect(ws)




@app.get("/", response_class=FileResponse, tags=["Status"])
def dashboard():
    from pathlib import Path
    dashboard_path = Path(__file__).parent / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path, media_type="text/html")
    return FileResponse.__new__(FileResponse)


@app.get("/api", tags=["Status"])
def root():
    return {
        "name": "DoSync Hub",
        "version": "0.4.0",
        "protocol": f"dosync/{DOSYNC_PROTOCOL_VERSION}",
        "status": "running",
        "devices_registered": len(hub.registry.all()),
    }


@app.post("/v1/devices/register", tags=["Devices"])
def register_device(req: RegisterDeviceRequest, auth: str = Depends(require_auth)):
    # ── Device authentication ──────────────────────────────────────────────
    from dosync.auth import get_device_auth_manager
    device_auth = get_device_auth_manager()
    if device_auth:
        if req.device_token:
            valid, reason = device_auth.verify(req.device_id, req.device_token)
            if not valid:
                raise HTTPException(status_code=403, detail=f"Device auth failed: {reason}")
        elif device_auth.strict:
            raise HTTPException(
                status_code=403,
                detail=f"Device '{req.device_id}' requires a device_token (strict mode)"
            )

    # ── Certificate authentication (Option C) ─────────────────────────────
    # Device can include certificate_pem in the register body.
    # This PEM is verified against the local CA — same logic as /v1/devices/verify-cert.
    # If valid, the manifest is marked cert_authenticated=True.
    # Devices without certificate_pem fall back to token-based auth (backward compatible).
    cert_authenticated = False
    cert_pem = getattr(req, 'certificate_pem', None)
    if cert_pem:
        try:
            import tempfile
            from pathlib import Path
            from dosync.security import verify_chain, _cert_info
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.crt', delete=False
            ) as f:
                f.write(cert_pem.strip())
                tmp_path = Path(f.name)
            try:
                chain_valid = verify_chain(tmp_path)
                cert_info   = _cert_info(tmp_path)
            finally:
                tmp_path.unlink(missing_ok=True)
            if chain_valid and cert_info and not cert_info.is_expired:
                cert_authenticated = True
                logging.getLogger("dosync.server").info("Device %s authenticated via certificate (expires in %d days)",
                         req.device_id, cert_info.days_until_expiry)
        except Exception as _cert_e:
            logging.getLogger("dosync.server").debug("Certificate verification for %s: %s", req.device_id, _cert_e)

    # ── Registro normal ────────────────────────────────────────────────────
    try:
        manifest = CapabilityManifest(
            device_id=req.device_id,
            device_name=req.device_name,
            manufacturer=req.manufacturer,
            model=req.model,
            firmware=req.firmware,
            category=DeviceCategory(req.category),
            tags=req.tags,
            sensors=[SensorSpec(s.id, s.type, s.description, s.unit,
                                poll_interval_ms=s.poll_interval_ms,
                                kind=s.kind)
                     for s in req.sensors],
            actuators=[ActuatorSpec(a.id, a.type, a.description, a.params_schema)
                       for a in req.actuators],
            events=[EventSpec(e.id, Severity(e.severity), e.description)
                    for e in req.events],
            emergency_capable=req.emergency_capable,
            cert_tier=CertTier(req.cert_tier),
            adapter=req.adapter,
            adapter_config=dict(req.adapter_config or {}),
        )
        # Store mTLS authentication status in adapter_config
        if cert_authenticated:
            manifest.adapter_config = {
                **(manifest.adapter_config or {}),
                "cert_authenticated": True,
            }
        # Validate that each actuator's params_schema is well-formed JSON Schema
        # (draft 2020-12). Protects the integrity of the standard: a manifest that
        # claims JSON Schema must actually be valid. Skipped gracefully if the
        # jsonschema library is absent (logs a warning, does not fail).
        from dosync.validation import validate_manifest_schemas
        schema_problems = validate_manifest_schemas(manifest)
        if schema_problems:
            raise HTTPException(
                status_code=422,
                detail="Invalid actuator params_schema: " + "; ".join(schema_problems),
            )
        hub.register_device(manifest)
        return {
            "status": "registered",
            "device_id": req.device_id,
            "cert_authenticated": cert_authenticated,
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/v1/intents/{intent_class}/explain", tags=["intents"])
async def explain_intent(
    intent_class: str,
    urgency: str = "info",
    location: str = "",
    auth=Depends(require_auth),
):
    """
    Explainability endpoint — muestra el razonamiento del resolver para un intent.

    Para cada dispositivo registrado, detalla:
    - Score total y desglose (tag overlap, location bonus, emergency bonus, actuator match)
    - Por qué fue incluido o excluido del ActionPlan
    - Tags que matchearon con las resolution tags del intent

    Nota: este endpoint muestra el scoring del resolver. El PolicyEngine puede
    modificar el plan antes de la ejecución — ver el audit log para el resultado real.

    Diseñado para ser consumido tanto por humanos como por sistemas de IA
    que interpreten el comportamiento del hub. Ver docs/DESIGN-PRINCIPLES.md.
    """
    from dosync.models import Intent, IntentClass, Urgency as _Urgency
    import uuid, time as _time

    # Validar intent_class
    try:
        intent_cls = IntentClass(intent_class)
    except ValueError:
        registered = [r["name"] for r in hub.db.list_intent_classes()]
        raise HTTPException(
            status_code=400,
            detail=(f"Invalid intent class name '{intent_class}' "
                    f"(must match ^[a-z][a-z0-9_]*$). Registered classes: {registered}")
        )

    # Validar urgency
    try:
        urgency_cls = _Urgency(urgency)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid urgency '{urgency}'. Valid values: info, warning, alert, emergency"
        )

    # Construir intent de prueba
    context = {}
    if location:
        context["location"] = location

    intent = Intent(
        intent=intent_cls,
        urgency=urgency_cls,
        context=context,
    )

    # Obtener explicación del resolver
    explanation = hub.resolver.explain(intent)
    return explanation


@app.get("/v1/health/devices", tags=["health"])
async def get_device_health(
    threshold: float = 0.7,
    last_n: int = 100,
    auth=Depends(require_auth),
):
    """
    Device Health Monitor — execution success-rate statistics per device.

    Returns each device's success rate over the last `last_n` executions; devices
    below `threshold` appear in `alerts`. Use this to find devices that FAIL
    OFTEN and need attention. The decision of what to do is always the operator's.

    ── Which health endpoint do I want? ─────────────────────────────────────────
      * /v1/health/devices        (this one) — HISTORICAL success rate of actions.
                                   "Has this device been failing lately?"
      * /v1/health/reachability   — CURRENT reachable/unreachable state, including
                                   device-initiated heartbeats. "Is this device
                                   responding right now, and how do I know?"
    They answer different questions; a device can be reachable now yet have a poor
    historical success rate, or vice versa.
    """
    all_health = hub.db.get_all_health(last_n=last_n)
    alerts     = hub.db.get_health_alerts(threshold=threshold, last_n=last_n)

    return {
        "devices":    all_health,
        "alerts":     alerts,
        "threshold":  threshold,
        "last_n":     last_n,
        "total_devices_monitored": len(all_health),
        "total_alerts": len(alerts),
    }


@app.get("/v1/health/devices/{device_id}", tags=["health"])
async def get_single_device_health(
    device_id: str,
    last_n: int = 100,
    auth=Depends(require_auth),
):
    """Historical execution success-rate for one device (see /v1/health/devices
    for the full note on which health endpoint answers which question; for
    current reachability + heartbeat state use /v1/health/reachability)."""
    health = hub.db.get_device_health(device_id, last_n=last_n)
    if health["total"] == 0:
        raise HTTPException(status_code=404, detail=f"No execution history for device '{device_id}'")
    return health


@app.get("/v1/health/reachability", tags=["health"])
async def get_reachability(auth=Depends(require_auth)):
    """Current reachability of every device the hub has interacted with.

    Complements /v1/health/devices (historical success rate) with the CURRENT
    reachable/unreachable state. Two signals feed it: PASSIVE (derived from real
    actions — the last interaction) and ACTIVE PUSH (device-initiated heartbeats,
    POST /v1/heartbeat — see the `last_heartbeat` field and the per-device
    `note`). It never asserts a device is "powered off" (it cannot tell an off
    device from a
    network-unreachable one). Read-only.
    """
    snapshots = [hub.health.snapshot(d.device_id) for d in hub.registry.all()]
    unreachable = [s for s in snapshots if s["reachable"] is False]
    return {
        "devices": snapshots,
        "unreachable": [s["device_id"] for s in unreachable],
        "total_devices": len(snapshots),
        "total_unreachable": len(unreachable),
        "note": ("Passive health from real interactions, not a heartbeat. "
                 "'unreachable' means the device did not respond to its last "
                 "action; it may be powered off or network-unreachable."),
    }


@app.get("/v1/devices", tags=["Devices"])
def list_devices(auth: str = Depends(require_auth)):
    return {
        "count": len(hub.registry.all()),
        "devices": [d.to_public_dict() for d in hub.registry.all()],
    }


@app.get("/v1/devices/{device_id}", tags=["Devices"])
def get_device(device_id: str, auth: str = Depends(require_auth)):
    device = hub.registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return device.to_public_dict()


@app.post("/v1/devices/provision", tags=["Devices"])
def provision_device(body: dict, auth: str = Depends(require_auth)):
    """
    Pre-registra un device_id y genera su token de autenticación.
    El token se muestra UNA SOLA VEZ — guardarlo de inmediato.
    """
    from dosync.auth import get_device_auth_manager
    device_auth = get_device_auth_manager()
    if not device_auth:
        raise HTTPException(status_code=503, detail="Device auth not configured")
    device_id = body.get("device_id")
    if not device_id:
        raise HTTPException(status_code=422, detail="device_id required")
    label = body.get("label", device_id)
    token = device_auth.provision(device_id, label)
    return {
        "device_id": device_id,
        "device_token": token,
        "warning": "Store this token immediately — it will not be shown again.",
        "usage": "Include device_token in your /v1/devices/register request"
    }


@app.post("/v1/devices/verify-cert", tags=["Devices"])
async def verify_device_cert(body: dict, auth: str = Depends(require_auth)):
    """
    Verify a device's client certificate against the local CA.

    The device presents its certificate PEM and the hub verifies:
    - It is a valid X.509 certificate
    - It was signed by the local DoSync CA
    - It has not expired
    - It identifies as a DoSync adapter (CN starts with 'dosync-adapter-')

    Returns cert_authenticated=True if all checks pass.
    The device should include certificate_pem in /v1/devices/register
    to record the cert_authenticated status in its manifest.

    Example usage (device side):
        cert_pem = open("certs/adapters/gpio.crt").read()
        r = requests.post("/v1/devices/verify-cert", json={"certificate_pem": cert_pem})
        if r.json()["cert_authenticated"]:
            # include certificate_pem in /v1/devices/register body
    """
    import tempfile, os as _os
    from pathlib import Path
    from dosync.security import verify_chain, _cert_info

    cert_pem = body.get("certificate_pem", "").strip()
    if not cert_pem:
        raise HTTPException(status_code=422, detail="certificate_pem is required")

    try:
        # Write to temp file for openssl verification
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.crt', delete=False
        ) as f:
            f.write(cert_pem)
            tmp_path = Path(f.name)

        try:
            # Verify chain against local CA
            chain_valid = verify_chain(tmp_path)
            cert_info   = _cert_info(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        if not cert_info:
            return {
                "cert_authenticated": False,
                "reason": "Could not parse certificate",
            }

        if cert_info.is_expired:
            return {
                "cert_authenticated": False,
                "reason": f"Certificate expired {abs(cert_info.days_until_expiry)} days ago",
            }

        if not chain_valid:
            return {
                "cert_authenticated": False,
                "reason": "Certificate not signed by local DoSync CA",
            }

        logging.getLogger("dosync.server").info("Device cert verified: %s (expires in %d days)",
                 cert_info.subject, cert_info.days_until_expiry)

        return {
            "cert_authenticated": True,
            "subject":            cert_info.subject,
            "issuer":             cert_info.issuer,
            "expires":            cert_info.not_after,
            "days_until_expiry":  cert_info.days_until_expiry,
            "serial":             cert_info.serial,
        }

    except Exception as e:
        logging.getLogger("dosync.server").warning("verify-cert error: %s", e)
        raise HTTPException(status_code=500, detail=f"Certificate verification error: {e}")


@app.get("/v1/devices/provisioned", tags=["Devices"])
def list_provisioned_devices(auth: str = Depends(require_auth)):
    """Lista todos los device_ids pre-registrados."""
    from dosync.auth import get_device_auth_manager
    device_auth = get_device_auth_manager()
    if not device_auth:
        return {"provisioned": []}
    return {"provisioned": device_auth.list_provisioned()}


@app.delete("/v1/devices/{device_id}/token", tags=["Devices"])
def revoke_device_token(device_id: str, auth: str = Depends(require_auth)):
    """Revoca el token de un dispositivo — deberá ser re-provisionado."""
    from dosync.auth import get_device_auth_manager
    device_auth = get_device_auth_manager()
    if not device_auth:
        raise HTTPException(status_code=503, detail="Device auth not configured")
    revoked = device_auth.revoke(device_id)
    if not revoked:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not provisioned")
    return {"status": "revoked", "device_id": device_id}


@app.delete("/v1/devices/{device_id}", tags=["Devices"])
def unregister_device(device_id: str, auth: str = Depends(require_auth)):
    if not hub.registry.get(device_id):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    hub.unregister_device(device_id)
    # Clear retained MQTT registration message so the device cannot auto-reconnect
    if _mqtt_adapter is not None and _mqtt_adapter.is_connected:
        _mqtt_adapter.clear_device_registration(device_id)
    return {"status": "unregistered", "device_id": device_id}



# ── Custom Intent Classes endpoints ──────────────────────────────────────────

# Intent class names are stored in the DB — no hardcoded list needed

class CustomIntentClassRequest(BaseModel):
    name:                  str
    urgency:               str = "info"
    resolution_tags:       list[str]
    resolution_actuators:  list[str] = []
    description:           str = ""
    domain:                str = "general"
    composition_kind:      Optional[str] = None  # e.g. "perimeter"; None = flat intent

@app.post("/v1/intent-classes", tags=["Protocol"], summary="Register a custom intent class")
async def register_intent_class(
    req: CustomIntentClassRequest,
    auth: str = Depends(require_auth),
):
    # Validate name format: ^[a-z][a-z0-9_]*$
    name = req.name.strip().lower().replace(" ", "_").replace("-", "_")
    if not name:
        raise HTTPException(status_code=400, detail="name cannot be empty")
    if not re.match(r'^[a-z][a-z0-9_]*$', name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid intent class name '{name}'. "
                   "Must match ^[a-z][a-z0-9_]*$ "
                   "(lowercase letters, digits, underscores only — no special characters)"
        )
    # Protect universal intents from being overridden
    existing = hub.db.get_intent_class(name)
    if existing and existing.get("is_universal"):
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a universal intent class and cannot be overridden. "
                   "Universal intents are defined by the DoSync protocol."
        )
    # Validate urgency
    if req.urgency not in ("emergency", "alert", "info"):
        raise HTTPException(
            status_code=400,
            detail="urgency must be one of: emergency, alert, warning, info"
        )
    # Validate tags
    if not req.resolution_tags:
        raise HTTPException(status_code=400, detail="resolution_tags cannot be empty")

    # Validate composition_kind: only kinds the hub can actually compose may be
    # declared. This mirrors the hub's routing (which fails explicitly on an unknown
    # kind) — refusing the declaration up front is clearer than accepting an intent
    # that would fail every time it fires. NULL = a normal flat intent.
    _KNOWN_COMPOSITION_KINDS = {"perimeter"}
    if req.composition_kind is not None and req.composition_kind not in _KNOWN_COMPOSITION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown composition_kind '{req.composition_kind}'. "
                   f"Known kinds: {sorted(_KNOWN_COMPOSITION_KINDS)}. "
                   "Omit composition_kind for a normal (flat) intent."
        )

    hub.db.save_intent_class(
        name=name,
        urgency=req.urgency,
        resolution_tags=req.resolution_tags,
        resolution_actuators=req.resolution_actuators,
        description=req.description,
        domain=req.domain,
        composition_kind=req.composition_kind,
    )
    return {
        "status":       "registered",
        "name":         name,
        "urgency":      req.urgency,
        "resolution_tags": req.resolution_tags,
        "resolution_actuators": req.resolution_actuators,
        "description":  req.description,
        "domain":       req.domain,
        "composition_kind": req.composition_kind,
    }


@app.get("/v1/intent-classes", tags=["Protocol"], summary="List all intent classes")
async def list_intent_classes(auth: str = Depends(require_auth)):
    """List all registered intent classes — universal and domain-specific.
    No distinction between built-in and custom: all live in the DB."""
    classes = hub.db.list_intent_classes()
    return {
        "intent_classes": classes,
        "total":          len(classes),
    }


@app.delete("/v1/intent-classes/{name}", tags=["Protocol"], summary="Delete a custom intent class")
async def delete_intent_class(name: str, auth: str = Depends(require_auth)):
    row = hub.db.get_intent_class(name)
    if row and row.get("is_universal"):
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a universal intent class and cannot be deleted. "
                   "Universal intents are defined by the DoSync protocol."
        )
    deleted = hub.db.delete_intent_class(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Custom intent class '{name}' not found")
    return {"status": "deleted", "name": name}


@app.post("/v1/intent", tags=["AI"], include_in_schema=False)
async def execute_intent_legacy(req: IntentRequest, auth: str = Depends(require_auth)):
    """Deprecated — use POST /v1/intent/async instead."""
    from fastapi.responses import Response
    return Response(status_code=308, headers={"Location": "/v1/intent/async"})


@app.post("/v1/intent/async", tags=["AI"])
async def execute_intent_async(req: IntentRequest, auth: str = Depends(require_auth)):
    # Validate format: ^[a-z][a-z0-9_]*$
    try:
        intent_class = IntentClass(req.intent)
    except ValueError as e:
        # Cardinality rule: rejected intents carry arbitrary user strings — never
        # use them as a label value. Count them under the fixed "_invalid" class.
        M.intents_total.inc({"intent_class": "_invalid", "urgency": req.urgency if req.urgency in ("emergency", "alert", "info") else "_invalid", "outcome": "rejected"})
        raise HTTPException(status_code=422, detail=str(e))
    # Validate intent class is registered in DB
    if not hub.db.get_intent_class(req.intent):
        registered = [r["name"] for r in hub.db.list_intent_classes()]
        M.intents_total.inc({"intent_class": "_invalid", "urgency": req.urgency if req.urgency in ("emergency", "alert", "info") else "_invalid", "outcome": "rejected"})
        raise HTTPException(
            status_code=422,
            detail=f"Intent '{req.intent}' is not registered. "
                   f"Register via POST /v1/intent-classes or use one of: {registered}"
        )
    try:
        urgency = Urgency(req.urgency)
    except ValueError:
        M.intents_total.inc({"intent_class": req.intent, "urgency": "_invalid", "outcome": "rejected"})
        raise HTTPException(status_code=422, detail=f"Urgency '{req.urgency}' not valid. Use: emergency, alert, warning, info")

    # ── Idempotency check (protocol v0.2, opt-in) ─────────────────────────
    # If the client supplied an idempotency key, deduplicate against prior
    # requests within the retention window. This makes the retry advised by
    # the consistency model (§6) safe for physical actions.
    if req.idempotency_key:
        _store_cleanup()
        body_hash = _intent_body_hash(req)
        prior = _idempotency_store.get(req.idempotency_key)
        if prior is not None:
            if prior["body_hash"] == body_hash:
                # Legitimate retry: same key, same body. Return cached intent_id,
                # do NOT re-execute (a lock must not unlock twice).
                return {
                    "intent_id": prior["intent_id"],
                    "status":    "accepted",
                    "idempotent_replay": True,
                }
            # Anti-suppression: same key, different body → reject. A key cannot
            # be reused to suppress a different intent.
            raise HTTPException(
                status_code=409,
                detail="Idempotency key reused with a different request body.",
            )

    intent = Intent(
        intent=intent_class,
        urgency=urgency,
        subject=req.subject,
        source=req.source,
        context=req.context,
    )

    # Register the idempotency key now (before execution) so a fast retry that
    # arrives while the first is still running also deduplicates.
    if req.idempotency_key:
        _idempotency_store[req.idempotency_key] = {
            "body_hash":  _intent_body_hash(req),
            "intent_id":  intent.intent_id,
            "created_at": _time.time(),
        }

    _store_cleanup()
    _intent_store[intent.intent_id] = {
        "status":     "pending",
        "result":     None,
        "created_at": _time.time(),
        "intent":     req.intent,
        "urgency":    req.urgency,
        # MCP-V13: live partial progress, updated as each action completes, so a
        # poll of a still-pending intent can report what has ALREADY happened
        # instead of an opaque "still processing". Total planned count is filled
        # in once resolution is done (below); until then it is None.
        "progress":   {"completed": [], "planned": None},
    }

    def _on_action(r):
        prog = _intent_store.get(intent.intent_id, {}).get("progress")
        if prog is not None:
            prog["completed"].append({
                "device_id": r.device_id, "action": r.action,
                "success": bool(getattr(r, "success", False)),
            })

    async def _run_intent():
        result = await hub.execute_intent(intent, executor, progress_cb=_on_action)
        _snap_intents = {"ensure_safety", "notify_family", "alert_anomaly"}
        if req.intent in _snap_intents or req.urgency in ("emergency", "alert"):
            try:
                hub.db.save_emergency_snapshot(
                    intent_id=result.intent_id,
                    intent_class=req.intent,
                    urgency=req.urgency,
                    context=req.context,
                )
            except Exception as _snap_e:
                logging.getLogger("dosync.server").warning("Failed to save emergency snapshot: %s", _snap_e)

        _intent_store[intent.intent_id] = {
            "status":     result.status if hasattr(result, "status") else ("success" if result.success else "partial"),
            "result":     {
                "intent_id":      result.intent_id,
                "success":        result.success,
                "actions_taken":  len(result.results),
                "failed_devices": result.failed_devices,
                "results": [
                    {"device_id": r.device_id, "action": r.action, "success": r.success,
                     "response": r.response, "error": r.error}
                    for r in result.results
                ],
            },
            "created_at": _time.time(),
            "intent":     req.intent,
            "urgency":    req.urgency,
        }

        # ── Metrics: execution outcome + per-action results ──────────────
        M.intent_executions_total.inc({"outcome": _intent_store[intent.intent_id]["status"]})
        for r in result.results:
            if r.success:
                _res = "success"
            elif r.error and str(r.error).startswith("superseded"):
                _res = "superseded"
                M.device_preemptions_total.inc()
            else:
                _res = "failed"
            M.intent_actions_total.inc({"result": _res})

    M.intents_total.inc({"intent_class": req.intent, "urgency": req.urgency, "outcome": "accepted"})
    if urgency == Urgency.EMERGENCY:
        M.emergency_intents_total.inc()

    asyncio.create_task(_run_intent())

    return {
        "intent_id": intent.intent_id,
        "status":    "pending",
        "intent":    req.intent,
        "urgency":   req.urgency,
    }


@app.get("/v1/intent/{intent_id}", tags=["AI"])
async def get_intent_result(intent_id: str, auth: str = Depends(require_auth)):
    """Poll the result of an async intent execution."""
    entry = _intent_store.get(intent_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Intent '{intent_id}' not found")
    if entry["status"] == "pending":
        prog = entry.get("progress") or {}
        completed = prog.get("completed", [])
        return {"intent_id": intent_id, "status": "pending",
                "intent": entry.get("intent"), "urgency": entry.get("urgency"),
                # MCP-V13: partial progress so far — never an opaque pending.
                "partial": {
                    "actions_completed": len(completed),
                    "actions_planned":   prog.get("planned"),
                    "results":           completed,
                }}
    return {"intent_id": intent_id, "status": entry["status"], **entry["result"]}


@app.post("/v1/event", tags=["Devices"])
async def receive_event(req: EventRequest, auth: str = Depends(require_auth)):
    if not hub.registry.get(req.device_id):
        raise HTTPException(
            status_code=404,
            detail=f"Device '{req.device_id}' not registered. Register it first."
        )

    try:
        severity = Severity(req.severity)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Severity '{req.severity}' not valid. Use: info, warning, alert, emergency")

    event = DeviceEvent(
        device_id=req.device_id,
        event_id=req.event_id,
        severity=severity,
        data=req.data,
    )
    await hub.receive_event(event)

    return {
        "status":    "received",
        "device_id": req.device_id,
        "event_id":  req.event_id,
        "severity":  req.severity,
    }


@app.post("/v1/heartbeat", tags=["Devices"])
async def receive_heartbeat(req: HeartbeatRequest, auth: str = Depends(require_auth)):
    """DEVICE-HEALTH-ACTIVE (b): a device proactively reports it is alive.

    Complements the hub's periodic pull-probe with device-initiated push, which
    is the only liveness signal available for devices the hub cannot poll
    (behind NAT, sleeping, inbound-blocked). Positive signal only: it marks the
    device reachable and stamps last_heartbeat; it never marks a device
    unreachable. Unknown devices are rejected — a heartbeat is an assertion of
    identity and must come from a registered device.
    """
    if not hub.registry.get(req.device_id):
        raise HTTPException(
            status_code=404,
            detail=f"Device '{req.device_id}' not registered. Register it first.")
    hub.health.record_heartbeat(req.device_id, req.report or None)
    snap = hub.health.snapshot(req.device_id)
    return {
        "status":         "acknowledged",
        "device_id":      req.device_id,
        "last_heartbeat": snap["last_heartbeat"],
        "reachable":      snap["reachable"],
    }


@app.get("/v1/keys", tags=["Security"])
def list_keys(auth: str = Depends(require_auth)):
    return {"keys": _auth_manager.list_keys()}


@app.post("/v1/keys", tags=["Security"])
def create_key(label: str = "new_key", auth: str = Depends(require_auth)):
    token = _auth_manager.generate_key(label)
    return {
        "token": token,
        "label": label,
        "warning": "Save this token — it will not be shown again.",
    }


@app.get("/v1/audit", tags=["Security"])
def get_audit_log(auth: str = Depends(require_auth)):
    entries = hub.audit_log.entries()
    return {
        "count":    len(entries),
        "integrity": hub.audit_log.verify(),
        "entries":  entries,
    }


@app.post("/v1/presence", tags=["Context"])
async def update_presence(req: PresenceSignalRequest, auth: str = Depends(require_auth)):
    from dosync.models import PresenceSignal, ContextSignalType
    try:
        signal_type = ContextSignalType(req.signal_type)
    except ValueError:
        valid = [t.value for t in ContextSignalType]
        raise HTTPException(status_code=422,
            detail=f"signal_type '{req.signal_type}' not valid. Valid: {valid}")

    signal = PresenceSignal(
        device_id=req.device_id,
        signal_type=signal_type,
        present=req.present,
        confidence=req.confidence,
        member_id=req.member_id,
    )
    state = hub.update_presence(signal)
    return {
        "status":       "updated",
        "occupied":     state.occupied,
        "confidence":   round(state.confidence, 3),
        "members_home": state.members_home,
        "signals_used": state.signals_used,
    }


@app.get("/v1/presence", tags=["Context"])
def get_presence(auth: str = Depends(require_auth)):
    state = hub.get_occupancy()
    signals = hub.occupancy.all_signals()
    return {
        "occupied":     state.occupied,
        "confidence":   round(state.confidence, 3),
        "members_home": state.members_home,
        "signals_used": state.signals_used,
        "signals":      signals,
    }


@app.post("/v1/discovery/run", tags=["Discovery"])
async def run_discovery(auth: str = Depends(require_auth)):
    from dosync.discovery import Discovery
    disc = Discovery(hub, timeout_override=5.0)
    new_count = await disc.run()
    return {
        "status":       "complete",
        "new_devices":  new_count,
        "total_devices": len(hub.registry.all()),
    }


@app.get("/v1/discovery/scan", tags=["Discovery"])
async def scan_devices(auth: str = Depends(require_auth)):
    from dosync.discovery import discover_wiz
    wiz_devices = await discover_wiz(timeout=5.0)
    return {
        "found": [
            {
                "adapter":     d.adapter,
                "device_id":   d.device_id,
                "device_name": d.device_name,
                "ip":          d.ip,
                "registered":  hub.registry.get(d.device_id) is not None,
            }
            for d in wiz_devices
        ],
        "count": len(wiz_devices),
    }


@app.post("/v1/device/action", tags=["Devices"])
async def device_action(
    req: dict,
    auth: str = Depends(require_auth),
):
    from dosync.models import DeviceAction, Urgency
    device_id = req.get("device_id")
    action    = req.get("action")
    params    = req.get("params", {})

    if not device_id or not action:
        raise HTTPException(status_code=422,
            detail="device_id and action are required")

    device = hub.registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404,
            detail=f"Device '{device_id}' not found")

    dev_action = DeviceAction(
        device_id=device_id,
        action=action,
        params=params,
    )
    result = await executor.execute(dev_action, Urgency.INFO)

    await ws_manager.broadcast("device_action", {
        "device_id": result.device_id,
        "action":    result.action,
        "success":   result.success,
        "response":  result.response,
    })

    return {
        "device_id": result.device_id,
        "action":    result.action,
        "success":   result.success,
        "response":  result.response,
        "error":     result.error,
    }


@app.get("/v1/hub/heartbeat", tags=["Hub"], summary="Hub heartbeat for monitoring and multi-hub failover detection")
async def hub_heartbeat():
    """
    Lightweight health check for hub monitoring and multi-hub failover detection.

    Returns minimal hub state to allow standby hubs and monitoring systems to
    determine whether this hub is healthy and should be treated as the active hub.

    Response fields:
      hub_id:            Unique identifier for this hub instance (stable across restarts)
      status:            "healthy" | "degraded" — degraded if audit log integrity fails
      protocol_version:  DoSync semantic protocol version
      api_version:       REST API version
      timestamp:         Current UTC timestamp (ISO 8601)
      uptime_seconds:    Seconds since hub process started
      devices:           Number of registered devices
      role:              "primary" | "standby" (from DOSYNC_HUB_ROLE env var)

    Clients and standby hubs SHOULD poll this endpoint to detect primary failure.
    Recommended polling interval: 5 seconds. A hub is considered failed after
    3 consecutive missed heartbeats (15 seconds of no response).
    """
    import time as _time_hb
    import uuid as _uuid
    from datetime import datetime, timezone

    # Generate a stable hub_id from the host machine identity
    # Uses a hash of the DB path + process start time as a stable identifier
    hub_id = getattr(app.state, "hub_id", None)
    if not hub_id:
        import hashlib
        raw = f"{os.environ.get('DOSYNC_DB', 'dosync.db')}-{os.getpid()}"
        app.state.hub_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        hub_id = app.state.hub_id

    # Determine health status
    audit_ok = hub.audit_log.verify() if hasattr(hub.audit_log, "verify") else True
    status = "healthy" if audit_ok else "degraded"

    # Calculate uptime
    start_time = getattr(app.state, "start_time", None)
    if not start_time:
        app.state.start_time = _time_hb.time()
        start_time = app.state.start_time
    uptime = int(_time_hb.time() - start_time)

    role = os.environ.get("DOSYNC_HUB_ROLE", "primary").lower()

    return {
        "hub_id":           hub_id,
        "status":           status,
        "protocol_version": DOSYNC_PROTOCOL_VERSION,
        "api_version":      DOSYNC_API_VERSION,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "uptime_seconds":   uptime,
        "devices":          len(hub.registry.all()),
        "role":             role,
        "multi_hub_capable": True,
        "transports": {
            "mqtt": {
                "enabled":   _mqtt_adapter is not None,
                "connected": _mqtt_adapter.is_connected if _mqtt_adapter else False,
                "broker":    _mqtt_adapter.broker      if _mqtt_adapter else None,
            }
        },
    }


@app.get("/v1/hub/peers", tags=["Hub"], summary="Multi-hub monitor view (Phase A)")
async def hub_peers():
    """Return this hub's multi-hub coordination view.

    On a primary, the monitor is inert. On a standby, returns the monitor's
    current state, how many heartbeats the primary has missed, and whether
    promotion would be safe or destructive (state divergence).
    """
    role = os.environ.get("DOSYNC_HUB_ROLE", "primary").lower()
    if _hub_monitor is None:
        return {"role": role, "monitor_state": "n/a", "primary_url": None}
    snap = _hub_monitor.snapshot()
    snap["role"] = role
    snap["primary_url"] = _primary_url
    return snap


@app.post("/v1/hub/promote", tags=["Hub"], summary="Operator-assisted standby promotion (Phase A)")
async def hub_promote(body: dict = None, auth=Depends(require_auth)):
    """Promote this standby to primary. Operator-assisted, never automatic.

    Requires the monitor to be in PRIMARY_DOWN. If promotion would be
    destructive (the primary held more devices than this standby — no Phase B
    replication yet), requires {"force": true} and returns 409 otherwise. This
    is the human-in-the-loop gate that prevents split-brain and silent device
    loss.
    """
    body = body or {}
    if _hub_monitor is None:
        raise HTTPException(status_code=400, detail="This hub is not a standby (no monitor running).")

    if _hub_monitor.state is not MonitorState.PRIMARY_DOWN:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot promote: monitor state is {_hub_monitor.state.value}, expected PRIMARY_DOWN.",
        )

    proposal = _hub_monitor.promotion_proposal()
    if proposal.destructive and not body.get("force"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Promotion would lose state",
                "reason": proposal.reason,
                "local_devices": proposal.local_devices,
                "primary_devices_last_known": proposal.primary_devices_last_known,
                "hint": "Re-issue with {\"force\": true} to promote anyway.",
            },
        )

    # Promote: flip role to primary. (Phase A: role failover only; state
    # replication is Phase B. A forced destructive promotion serves a registry
    # missing the primary's devices — the operator was warned.)
    os.environ["DOSYNC_HUB_ROLE"] = "primary"
    return {
        "promoted": True,
        "new_role": "primary",
        "was_destructive": proposal.destructive,
        "devices_served": proposal.local_devices,
        "warning": proposal.reason if proposal.destructive else None,
    }


# ── Metrics (Prometheus text format) ─────────────────────────────────────────
# Operational feature of the reference implementation — NOT part of the
# normative protocol (a hub without /metrics is still conforming). Auth-protected:
# unlike /v1/status (minimal public health for monitors/standby hubs), /metrics
# exposes operational detail, so it requires a token. Point your Prometheus
# scraper at it with:  authorization: { credentials: "<token>" }.

_metrics_start_time = _time.time()

M.REGISTRY.gauge_func(
    "dosync_devices_registered",
    "Devices currently registered in the capability registry",
    lambda: len(hub.registry.all()),
)
M.REGISTRY.gauge_func(
    "dosync_devices_emergency_capable",
    "Registered devices declaring emergency_capable=true",
    lambda: sum(1 for d in hub.registry.all() if getattr(d, "emergency_capable", False)),
)
M.REGISTRY.gauge_func(
    "dosync_audit_entries",
    "Entries in the tamper-evident audit log",
    lambda: len(hub.audit_log.entries()),
)
M.REGISTRY.gauge_func(
    "dosync_audit_integrity",
    "1 if the audit log SHA-256 chain verifies, 0 if broken",
    lambda: 1 if hub.audit_log.verify() else 0,
)
M.REGISTRY.gauge_func(
    "dosync_ws_connections",
    "Active WebSocket connections",
    lambda: ws_manager.active_connections,
)
M.REGISTRY.gauge_func(
    "dosync_hub_uptime_seconds",
    "Seconds since the hub process started",
    lambda: _time.time() - _metrics_start_time,
)

def _device_success_rate_samples():
    # device_id label is acceptable here ONLY because device health is a bounded,
    # low-volume gauge (see cardinality rule in dosync/metrics.py). Detailed
    # per-device history stays in /v1/health/devices.
    out = []
    for d in hub.db.get_all_health(last_n=100):
        dev = d.get("device_id")
        rate = d.get("success_rate")
        if dev is not None and rate is not None:
            out.append(({"device_id": dev}, rate))
    return out

M.REGISTRY.gauge_func(
    "dosync_device_success_rate",
    "Per-device action success rate over the last 100 executions (0.0-1.0)",
    _device_success_rate_samples,
)


@app.get("/metrics", tags=["Status"], summary="Prometheus metrics (reference implementation feature, non-normative)")
def get_metrics(auth: str = Depends(require_auth)):
    return PlainTextResponse(M.REGISTRY.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/v1/status", tags=["Status"])
def get_status():
    db_stats = hub.db.stats()
    occupancy = hub.get_occupancy()
    return {
        "name":            "DoSync Hub",
        "version":         "0.4.0",
        "protocol":        f"dosync/{DOSYNC_PROTOCOL_VERSION}",
        "status":          "running",
        "certify_mode":    _certify_mode,
        "protocol_version": DOSYNC_PROTOCOL_VERSION,
        "api_version":      DOSYNC_API_VERSION,
        "devices":         len(hub.registry.all()),
        "audit_entries":   len(hub.audit_log.entries()),
        "audit_integrity": hub.audit_log.verify(),
        # AUDIT-ARCHIVE: surface whether the chain is anchored (segmented) so a
        # conformance test can verify the anchor is honored end to end.
        "audit_anchored":  hub.audit_log.anchor_prev_hash != "0" * 64,
        "audit_anchor_prefix": hub.audit_log.anchor_prev_hash[:16],
        # v13 hygiene: nonzero means a progress callback has been failing —
        # swallowed so it never breaks execution, but surfaced here so a real
        # bug is visible instead of hidden in logs.
        "progress_cb_failures": getattr(hub, "progress_cb_failures", 0),
        "occupied":        occupancy.occupied,
        "ws_connections":  ws_manager.active_connections,
        "db":              db_stats,
        "family_profile":  hub.family_profile.family_name
                            if hub.family_profile else None,
    }


# ── Long-running operations (execution_model) ─────────────────────────────────
# Read-only query surface for operations started by execute_intent. The intent
# response returns each operation's id; these endpoints let a client follow it.
# Cancellation is intentionally NOT here yet — querying is safe and additive;
# cancel is a state-changing action that belongs with the adapter that can carry
# it out (e.g. the future MAVLink adapter). These endpoints never mutate state.

@app.get("/v1/operations", tags=["Operations"], summary="List active long-running operations")
def list_operations(auth: str = Depends(require_auth)):
    """Active (non-terminal) long-running operations the hub is tracking. These are
    the operations still in flight — the ones that would be reconciled after a
    restart. Completed/failed/cancelled operations are not 'active' and are not
    listed here (query them individually by id)."""
    active = hub.db.get_active_operations()
    return {
        "count":      len(active),
        "operations": active,
    }


@app.get("/v1/operations/{operation_id}", tags=["Operations"], summary="Get one operation by id")
def get_operation(operation_id: str, auth: str = Depends(require_auth)):
    """Full state of a single operation by id — current state, timing, and the
    complete transition history (the audit trail of its lifecycle). Works for both
    active and terminal operations until they are cleaned up."""
    op = hub.db.get_operation(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=f"Operation '{operation_id}' not found")
    return op
