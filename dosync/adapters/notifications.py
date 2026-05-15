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

log = logging.getLogger("dosync.notifications")

# Cargar .env si existe
try:
    from pathlib import Path
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())
except Exception:
    pass

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM    = os.environ.get("TWILIO_FROM", "")
EMERGENCY_TO   = os.environ.get("DOSYNC_EMERGENCY_CONTACT", "")

# Intents que disparan notificaciones
EMERGENCY_INTENTS = {"ensure_safety", "alert_anomaly", "notify_family"}
WARNING_INTENTS   = {"report_status", "remind_chore"}


class NotificationAdapter:
    """Envía SMS via Twilio para intents críticos de DoSync."""

    def __init__(self):
        self._available = bool(TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM)
        if not self._available:
            log.warning("Twilio not configured — SMS notifications disabled")
        else:
            log.info("NotificationAdapter ready — SMS to %s", EMERGENCY_TO or "?")

    def _get_client(self):
        try:
            from twilio.rest import Client
            return Client(TWILIO_SID, TWILIO_TOKEN)
        except ImportError:
            log.error("twilio not installed — run: pip install twilio")
            return None

    def _build_message(self, intent: str, urgency: str, context: dict) -> str:
        """Construye el mensaje SMS según el intent."""
        location = context.get("location", "")
        trigger  = context.get("trigger", "")
        temp     = context.get("temperature")

        if intent == "ensure_safety":
            loc = f" en {location}" if location else ""
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
        elif intent == "notify_family":
            msg = context.get("message", "Novedad en el hogar")
            return f"DOSYNC AVISO\n{msg}"
        elif intent == "report_status" and trigger == "motion_detected":
            loc = f" en {location}" if location else ""
            return f"DOSYNC INFO\nMovimiento detectado{loc}."
        else:
            return f"DOSYNC {urgency.upper()}\nIntent: {intent}"

    async def notify(self, intent: str, urgency: str, context: dict,
                     to: str = None) -> bool:
        """Envía un SMS. Retorna True si fue exitoso."""
        if not self._available:
            log.warning("SMS not sent — Twilio not configured")
            return False

        destination = to or EMERGENCY_TO
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
                from_=TWILIO_FROM,
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
