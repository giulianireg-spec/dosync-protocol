"""
DoSync — Notification Adapter (SMS via Twilio)
===============================================
Envía SMS cuando se disparan intents de emergencia o alertas.

Uso:
    # Agregar al servidor como middleware de notificaciones
    from dosync.adapters.notifications import NotificationAdapter
    notifier = NotificationAdapter()
    await notifier.notify_emergency(intent, context)

Variables de entorno (.env):
    TWILIO_ACCOUNT_SID   — Account SID de Twilio
    TWILIO_AUTH_TOKEN    — Auth Token de Twilio
    TWILIO_FROM          — Número Twilio (+1XXXXXXXXXX)
    DOSYNC_EMERGENCY_CONTACT — Número destino (+54XXXXXXXXXX)
"""

from __future__ import annotations
import logging
import os

from . import DoSyncAdapter

log = logging.getLogger("dosync.notifications")

def load_env_file(path=None) -> int:
    """Populate the environment from a .env file. Returns how many keys it set.

    This used to run at IMPORT time, which made importing a module mutate global
    process state from a file on disk — and did it silently, inside a bare
    `except Exception: pass`.

    It broke two tests on the reference deployment and nowhere else: they
    deleted DOSYNC_POLICIES with monkeypatch, then something imported this
    module, and `setdefault` put the variable straight back. A test that
    isolates its environment cannot defend against an import that un-isolates
    it. Green on the development laptop, red on the deployment — and the
    deployment is the machine whose behaviour we are asserting.

    Explicit call, so the mutation happens where a reader can see it: the hub
    does it once at startup, tests do not do it at all.
    """
    from pathlib import Path
    env_file = Path(path) if path else Path(__file__).parent.parent.parent / ".env"
    if not env_file.exists():
        return 0
    applied = 0
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if key not in os.environ:
                os.environ[key] = val
                applied += 1
    except OSError as exc:
        # Narrow, and audible: an unreadable .env is worth a line in the log.
        log.warning("could not read %s: %s", env_file, exc)
    return applied


def _twilio_config() -> tuple[str, str, str, str]:
    """Read Twilio settings at CALL time, not at import time.

    Module-level constants froze whatever the environment held at first import,
    so a hub that loaded its .env afterwards kept the empty strings forever.
    """
    return (os.environ.get("TWILIO_ACCOUNT_SID", ""),
            os.environ.get("TWILIO_AUTH_TOKEN", ""),
            os.environ.get("TWILIO_FROM", ""),
            os.environ.get("DOSYNC_EMERGENCY_CONTACT", ""))

# Intents que disparan notificaciones
EMERGENCY_INTENTS = {"ensure_safety", "alert_anomaly", "notify"}
WARNING_INTENTS   = {"report_status", "remind_chore"}


class NotificationAdapter(DoSyncAdapter):
    """SMS and push notifications.

    Inherits `DoSyncAdapter` (it did not until 2026-07-26 — it duck-typed with a
    matching `adapter_name` and `execute`, which worked until the base class
    gained `discover`/`can_discover` and this adapter silently lacked them).
    Duck-typing an interface means every later addition to that interface skips
    you without a word; the scan loop only survived it because it happened to
    use a defensive getattr.
    """

    adapter_kind = "infrastructure"
    adapter_name = "notifications"
    """Sends SMS via Twilio for critical DoSync intents."""

    def __init__(self):
        sid, token, from_number, emergency_to = _twilio_config()
        self._sid, self._token = sid, token
        self._from, self._emergency_to = from_number, emergency_to
        self._available = bool(sid and token and from_number)
        if not self._available:
            log.warning("Twilio not configured — SMS notifications disabled")
        else:
            log.info("NotificationAdapter ready — SMS to %s", self._emergency_to or "?")

    def _get_client(self):
        try:
            from twilio.rest import Client
            return Client(self._sid, self._token)
        except ImportError:
            log.error("twilio not installed — run: pip install twilio")
            return None

    def _build_message(self, intent: str, urgency: str, context: dict) -> str:
        """Build the SMS message body for the given intent."""
        location = context.get("location", "")
        trigger  = context.get("trigger", "")
        temp     = context.get("temperature")

        if intent == "ensure_safety":
            loc = f" en {location}" if location else ""
            member = context.get("member", "")
            if member:
                return (
                    f"DoSync — {member} llegaron a casa.\n"
                    f"El sensor de movimiento los detecto y el hogar respondio automaticamente."
                )
            return (
                f"DOSYNC EMERGENCIA{loc}\n"
                f"Se detecto una situacion de emergencia en el hogar.\n"
                f"El sistema activo el protocolo de seguridad.\n"
                f"Verificar inmediatamente. Llamar al 107 (SAME) si es necesario."
            )
        elif intent == "alert_anomaly" and temp:
            return (
                f"DOSYNC ALERTA\n"
                f"Temperatura anormal: {temp}C\n"
                f"Verificar el hogar."
            )
        elif intent == "report_status" and trigger == "motion_detected":
            loc = f" en {location}" if location else ""
            return f"DOSYNC INFO\nMovimiento detectado{loc}."
        elif intent == "notify":
            msg = context.get("message", "Notification from DoSync")
            return f"DOSYNC\n{msg}"
        else:
            return f"DOSYNC {urgency.upper()}\nIntent: {intent}"

    async def execute(self, action, urgency):
        from ..models import ActionResult
        params = action.params or {}
        message = params.get('message', 'Notificación DoSync')
        try:
            await self.notify(
                intent=action.action,
                urgency=urgency.value if hasattr(urgency, 'value') else str(urgency),
                context={'message': message},
                to=None
            )
            return ActionResult(device_id=action.device_id, action=action.action, success=True, response={'status': 'sent'})
        except Exception as e:
            return ActionResult(device_id=action.device_id, action=action.action, success=False, error=str(e))

    async def notify(self, intent: str, urgency: str, context: dict,
                     to: str = None) -> bool:
        """Send an SMS. Returns True on success."""
        if not self._available:
            log.warning("SMS not sent — Twilio not configured")
            return False

        destination = to or self._emergency_to
        if not destination:
            log.warning("SMS not sent — no destination number configured")
            return False

        # Solo notificar para intents relevantes
        if intent not in EMERGENCY_INTENTS and urgency not in ("emergency", "alert"):
            return False

        client = self._get_client()
        if not client:
            return False

        message = self._build_message(intent, urgency, context)

        try:
            msg = client.messages.create(
                body=message,
                from_=self._from,
                to=destination,
            )
            log.info("SMS sent to %s — SID: %s", destination, msg.sid)
            return True
        except Exception as e:
            log.error("SMS failed: %s", e)
            return False

    async def notify_emergency(self, intent: str, context: dict,
                                to: str = None) -> bool:
        """Shortcut para notificaciones de emergencia."""
        return await self.notify(intent, "emergency", context, to)
