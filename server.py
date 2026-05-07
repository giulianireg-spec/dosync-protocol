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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.auth import AuthManager, require_auth, set_auth_manager
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
executor = SimulatedExecutor(failure_rate=0.0)

# ── Auth setup ────────────────────────────────────────────────────────────────
# Set DOSYNC_AUTH=false para deshabilitar en desarrollo local
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
        self._connections.remove(ws)
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


# ── Schemas de entrada (lo que recibe la API) ─────────────────────────────────

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
    # Generar key por defecto si no hay ninguna
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
    """
    WebSocket para eventos en tiempo real.
    
    Conectar: ws://localhost:47200/ws?token=<api-key>
    
    Eventos emitidos:
        device_event     — un dispositivo emitió un evento
        intent_executed  — un intent fue ejecutado
        phase_executed   — una fase de un intent fue ejecutada
        presence_updated — señal de presencia actualizada
        ping             — keepalive cada 30s
    """
    # Auth via query param (WebSocket no soporta headers custom fácilmente)
    token = ws.query_params.get("token", "")
    if _auth_enabled and not _auth_manager.verify(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws_manager.connect(ws)
    try:
        # Enviar estado inicial al conectarse
        await ws.send_text(json.dumps({
            "type": "connected",
            "data": {
                "devices":    len(hub.registry.all()),
                "hub_version": "0.1.0",
                "protocol":   "dosync/0.1",
            }
        }))
        # Keepalive loop
        import asyncio
        while True:
            await asyncio.sleep(30)
            await ws.send_text(json.dumps({"type": "ping", "data": {}}))
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


@app.get("/", tags=["Status"])
def root():
    """Estado del hub."""
    return {
        "name": "DoSync Hub",
        "version": "0.1.0",
        "protocol": "dosync/0.1",
        "status": "running",
        "devices_registered": len(hub.registry.all()),
    }


@app.post("/v1/devices/register", tags=["Devices"])
def register_device(req: RegisterDeviceRequest, auth: str = Depends(require_auth)):
    """
    Un gadget se anuncia al hub con su manifiesto de capacidades.
    Este es el primer mensaje que envía cualquier dispositivo DoSync al unirse a la red.
    """
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
    """Lista todos los gadgets registrados y sus capacidades."""
    return {
        "count": len(hub.registry.all()),
        "devices": [d.to_dict() for d in hub.registry.all()],
    }


@app.get("/v1/devices/{device_id}", tags=["Devices"])
def get_device(device_id: str, auth: str = Depends(require_auth)):
    """Detalle de un gadget específico."""
    device = hub.registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    return device.to_dict()


@app.delete("/v1/devices/{device_id}", tags=["Devices"])
def unregister_device(device_id: str, auth: str = Depends(require_auth)):
    """Desregistra un gadget del hub."""
    if not hub.registry.get(device_id):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    hub.unregister_device(device_id)
    return {"status": "unregistered", "device_id": device_id}


@app.post("/v1/intent", tags=["AI"])
async def execute_intent(req: IntentRequest, auth: str = Depends(require_auth)):
    """
    La IA envía una intención semántica al hub.
    El hub resuelve qué gadgets actúan y cómo, y ejecuta todo en paralelo.
    
    Intenciones disponibles:
    - ensure_safety · notify_family · report_status
    - set_environment · control_access · monitor_health
    """
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
    """
    Un gadget envía un evento al hub (caída detectada, avería, movimiento, etc).
    El hub lo registra en el audit log y notifica a los handlers de la IA.
    """
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
    """Lista todas las API keys registradas (sin mostrar el token completo)."""
    return {"keys": _auth_manager.list_keys()}


@app.post("/v1/keys", tags=["Security"])
def create_key(label: str = "new_key", auth: str = Depends(require_auth)):
    """Genera una nueva API key. El token se muestra solo una vez."""
    token = _auth_manager.generate_key(label)
    return {
        "token": token,
        "label": label,
        "warning": "Save this token — it will not be shown again.",
    }


@app.get("/v1/audit", tags=["Security"])
def get_audit_log(auth: str = Depends(require_auth)):
    """
    Historial completo de todas las acciones del hub.
    Cada entrada está encadenada con SHA-256 para detectar manipulaciones.
    """
    entries = hub.audit_log.entries()
    return {
        "count":    len(entries),
        "integrity": hub.audit_log.verify(),
        "entries":  entries,
    }


@app.post("/v1/presence", tags=["Context"])
async def update_presence(req: PresenceSignalRequest, auth: str = Depends(require_auth)):
    """
    Un context provider (celular, smartwatch, sensor PIR) actualiza
    su señal de presencia. El hub la agrega al OccupancyEngine.
    """
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
    """Estado de ocupación inferido actual del hogar."""
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
    """
    Escanea la red local en busca de dispositivos compatibles.
    Registra automaticamente los que encuentra (WiZ, y futuros adapters).
    """
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
    """
    Escanea la red local y devuelve los dispositivos encontrados
    sin registrarlos. Util para ver que hay antes de registrar.
    """
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


@app.get("/v1/status", tags=["Status"])
def get_status():
    """Estado completo del hub incluyendo estadísticas de la base de datos."""
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
