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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.auth import AuthManager, require_auth, set_auth_manager, DeviceAuthManager, set_device_auth_manager
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

# Device authentication manager
hub.db.init_device_tokens_table()
hub.db.init_emergency_snapshots_table()
_device_auth_manager = DeviceAuthManager(hub.db)
set_device_auth_manager(_device_auth_manager)


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
                            context={**snap['context'], "recovery": True, "original_intent_id": snap['intent_id']},
                            timestamp=_time.time()
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
    # Periodically queries device state via get_state() to keep the cache fresh.
    # Only runs if the resolver is StateAwareResolver and executor is AdapterExecutor.
    _refresh_task = None
    try:
        from dosync.adapters import AdapterExecutor
        if isinstance(hub.resolver, __import__('dosync.hub', fromlist=['StateAwareResolver']).StateAwareResolver)                 and isinstance(executor, AdapterExecutor):
            _refresh_task = asyncio.create_task(
                hub.resolver.start_background_refresh(executor)
            )
            log.info("Background state refresher started")
        else:
            log.debug("Background state refresher not started (resolver or executor not compatible)")
    except Exception as _refresh_e:
        log.warning("Failed to start background state refresher: %s", _refresh_e)

    yield

    # Cancel background refresher on shutdown
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
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
                                poll_interval_ms=s.poll_interval_ms)
                     for s in req.sensors],
            actuators=[ActuatorSpec(a.id, a.type, a.description)
                       for a in req.actuators],
            events=[EventSpec(e.id, Urgency(e.severity), e.description)
                    for e in req.events],
            emergency_capable=req.emergency_capable,
            cert_tier=CertTier(req.cert_tier),
        )
        # Store mTLS authentication status in adapter_config
        if cert_authenticated:
            manifest.adapter_config = {
                **(manifest.adapter_config or {}),
                "cert_authenticated": True,
            }
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
        valid = [e.value for e in IntentClass]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid intent class '{intent_class}'. Valid values: {valid}"
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
    Device Health Monitor — estadísticas de ejecución por dispositivo.

    Retorna la tasa de éxito de cada dispositivo basada en las últimas `last_n` ejecuciones.
    Los dispositivos con tasa por debajo de `threshold` aparecen en `alerts`.

    Usar para detectar dispositivos que fallan frecuentemente y requieren atención.
    La decisión de qué hacer con esa información es siempre del operador humano.
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
    """Estadísticas de salud de un dispositivo específico."""
    health = hub.db.get_device_health(device_id, last_n=last_n)
    if health["total"] == 0:
        raise HTTPException(status_code=404, detail=f"No execution history for device '{device_id}'")
    return health


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
            detail="urgency must be one of: emergency, alert, info"
        )
    # Validate tags
    if not req.resolution_tags:
        raise HTTPException(status_code=400, detail="resolution_tags cannot be empty")

    hub.db.save_intent_class(
        name=name,
        urgency=req.urgency,
        resolution_tags=req.resolution_tags,
        resolution_actuators=req.resolution_actuators,
        description=req.description,
        domain=req.domain,
    )
    return {
        "status":       "registered",
        "name":         name,
        "urgency":      req.urgency,
        "resolution_tags": req.resolution_tags,
        "resolution_actuators": req.resolution_actuators,
        "description":  req.description,
        "domain":       req.domain,
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
        raise HTTPException(status_code=422, detail=str(e))
    # Validate intent class is registered in DB
    if not hub.db.get_intent_class(req.intent):
        registered = [r["name"] for r in hub.db.list_intent_classes()]
        raise HTTPException(
            status_code=422,
            detail=f"Intent '{req.intent}' is not registered. "
                   f"Register via POST /v1/intent-classes or use one of: {registered}"
        )
    try:
        urgency = Urgency(req.urgency)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Urgency '{req.urgency}' not valid. Use: emergency, alert, info")
    intent = Intent(
        intent=intent_class,
        urgency=urgency,
        subject=req.subject,
        context=req.context,
    )

    _store_cleanup()
    _intent_store[intent.intent_id] = {
        "status":     "pending",
        "result":     None,
        "created_at": _time.time(),
        "intent":     req.intent,
        "urgency":    req.urgency,
    }

    async def _run_intent():
        result = await hub.execute_intent(intent, executor)
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
        return {"intent_id": intent_id, "status": "pending",
                "intent": entry.get("intent"), "urgency": entry.get("urgency")}
    return {"intent_id": intent_id, "status": entry["status"], **entry["result"]}


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
