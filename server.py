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
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.auth import AuthManager, require_auth, set_auth_manager
from dosync.security import get_status as get_pki_status
from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
    DeviceEvent, EventSpec, Intent, IntentClass, SensorSpec, Urgency,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)

# ── Estado global del hub ─────────────────────────────────────────────────────

hub      = DoSyncHub(db_path="dosync.db")

# ── Executor con adapters físicos ─────────────────────────────────────────────
try:
    from dosync.adapters import AdapterExecutor
    from dosync.adapters.wiz import WiZAdapter
    executor = AdapterExecutor(hub, fallback_to_simulated=True)
    executor.register(WiZAdapter(hub=hub))
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
try:
    from dosync.policies import PolicyEngine, NeverAfterHoursPolicy, RequireConfirmationPolicy, DeviceExclusionPolicy
    policy_engine = PolicyEngine()
    from dosync.policies import ConflictResolutionPolicy, ContextualWeightingPolicy
    policy_engine.add(ContextualWeightingPolicy())
    policy_engine.add(ConflictResolutionPolicy(hub))
    policy_engine.add(NeverAfterHoursPolicy(
        actuator_types=["unlock", "alarm"],
        blocked_hours_start=0,
        blocked_hours_end=6,
        reason="Security policy: no remote unlocking between 00:00 and 06:00"
    ))
    policy_engine.add(RequireConfirmationPolicy(
        actuator_types=["alarm"],
        reason="Alarm activation requires explicit confirmation"
    ))
    hub.policy_engine = policy_engine
    logging.getLogger("dosync.server").info("PolicyEngine initialized with %d policies", len(policy_engine.list_policies()))
except Exception as _e:
    logging.getLogger("dosync.server").warning("PolicyEngine init failed: %s", _e)

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

class ActuatorIn(BaseModel):
    id: str
    type: str
    description: str = ""

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
    context: dict[str, Any] = {}

class EventRequest(BaseModel):
    device_id: str
    event_id: str
    severity: str
    data: dict[str, Any] = {}


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
    yield
    log.info("DoSync Hub shutting down")

app = FastAPI(
    title="DoSync Hub",
    description=(
        "DoSync Protocol — REST API\n\n"
        "El hub central que conecta la IA con los gadgets del hogar.\n"
        "Protocolo abierto · Apache 2.0 · github.com/dosync/protocol"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


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
                "hub_version": "0.1.0",
                "protocol":   "dosync/0.1",
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
        "version": "0.1.0",
        "protocol": "dosync/0.1",
        "status": "running",
        "devices_registered": len(hub.registry.all()),
    }


@app.post("/v1/devices/register", tags=["Devices"])
def register_device(req: RegisterDeviceRequest, auth: str = Depends(require_auth)):
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
                                poll_interval_ms=s.poll_interval_ms)
                     for s in req.sensors],
            actuators=[ActuatorSpec(a.id, a.type, a.description)
                       for a in req.actuators],
            events=[EventSpec(e.id, Urgency(e.severity), e.description)
                    for e in req.events],
            emergency_capable=req.emergency_capable,
            cert_tier=CertTier(req.cert_tier),
        )
        hub.register_device(manifest)
        return {"status": "registered", "device_id": req.device_id}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/v1/devices", tags=["Devices"])
def list_devices(auth: str = Depends(require_auth)):
    return {
        "count": len(hub.registry.all()),
        "devices": [d.to_dict() for d in hub.registry.all()],
    }


@app.get("/v1/devices/{device_id}", tags=["Devices"])
def get_device(device_id: str, auth: str = Depends(require_auth)):
    device = hub.registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return device.to_dict()


@app.delete("/v1/devices/{device_id}", tags=["Devices"])
def unregister_device(device_id: str, auth: str = Depends(require_auth)):
    if not hub.registry.get(device_id):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    hub.unregister_device(device_id)
    return {"status": "unregistered", "device_id": device_id}


@app.post("/v1/intent", tags=["AI"])
async def execute_intent(req: IntentRequest, auth: str = Depends(require_auth)):
    try:
        intent_class = IntentClass(req.intent)
    except ValueError:
        valid = [i.value for i in IntentClass]
        raise HTTPException(
            status_code=422,
            detail=f"Intent '{req.intent}' not recognized. Valid: {valid}"
        )

    try:
        urgency = Urgency(req.urgency)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Urgency '{req.urgency}' not valid")

    intent = Intent(
        intent=intent_class,
        urgency=urgency,
        subject=req.subject,
        context=req.context,
    )

    result = await hub.execute_intent(intent, executor)

    # ── SMS notification for emergency/alert intents ───────────────────────
    _emergency_intents = {"ensure_safety", "notify_family", "alert_anomaly"}
    if notifier and (
        req.urgency in ("emergency", "alert") or
        req.intent in _emergency_intents
    ):
        try:
            await notifier.notify(
                intent=req.intent,
                urgency=req.urgency,
                context=req.context,
            )
        except Exception as _e:
            logging.getLogger("dosync.server").warning(
                "SMS notification failed: %s", _e
            )

    return {
        "intent_id":       result.intent_id,
        "success":         result.success,
        "actions_taken":   len(result.results),
        "failed_devices":  result.failed_devices,
        "results": [
            {
                "device_id":   r.device_id,
                "action":      r.action,
                "success":     r.success,
                "response":    r.response,
                "error":       r.error,
            }
            for r in result.results
        ],
    }


@app.post("/v1/event", tags=["Devices"])
async def receive_event(req: EventRequest, auth: str = Depends(require_auth)):
    if not hub.registry.get(req.device_id):
        raise HTTPException(
            status_code=404,
            detail=f"Device '{req.device_id}' not registered. Register it first."
        )

    try:
        severity = Urgency(req.severity)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Severity '{req.severity}' not valid")

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


@app.get("/v1/status", tags=["Status"])
def get_status():
    db_stats = hub.db.stats()
    occupancy = hub.get_occupancy()
    return {
        "name":            "DoSync Hub",
        "version":         "0.1.0",
        "protocol":        "dosync/0.1",
        "status":          "running",
        "devices":         len(hub.registry.all()),
        "audit_entries":   len(hub.audit_log.entries()),
        "audit_integrity": hub.audit_log.verify(),
        "occupied":        occupancy.occupied,
        "ws_connections":  ws_manager.active_connections,
        "db":              db_stats,
        "family_profile":  hub.family_profile.family_name
                            if hub.family_profile else None,
    }
