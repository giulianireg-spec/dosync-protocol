"""
DoSync — Notification Adapter (SMS via Twilio)
===============================================
Sends SMS when emergency or alert intents fire.

Uso:
    # Agregar al servidor como middleware de notificaciones
    from dosync.adapters.notifications import NotificationAdapter
    notifier = NotificationAdapter()
    await notifier.notify_emergency(intent, context)

Variables de entorno (.env):
    TWILIO_ACCOUNT_SID   — Account SID de Twilio
    TWILIO_AUTH_TOKEN    — Auth Token de Twilio
    TWILIO_FROM          — Twilio number (+1XXXXXXXXXX)
    DOSYNC_EMERGENCY_CONTACT — destination number, E.164 format
"""

from __future__ import annotations
import logging
import os
import json

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


def _load_templates() -> dict:
    """Read the deployment's message templates, if it declared any."""
    path = os.environ.get("DOSYNC_NOTIFICATION_TEMPLATES", "")
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            log.warning("%s does not contain an object — ignoring templates", path)
            return {}
        return data
    except (OSError, ValueError) as exc:
        # Audible, and never fatal: a missing template file must not stop the
        # hub from notifying, it just means the default body is used.
        log.warning("could not read notification templates from %s: %s", path, exc)
        return {}


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

    def __init__(self, templates: dict | None = None):
        """`templates` maps an intent class to a message template.

        Templates are the deployment's words, not the protocol's. A template is
        a str.format string over the intent context, e.g.
            {"ensure_safety": "EMERGENCY at {location} — check now"}
        Loaded from the JSON file named by DOSYNC_NOTIFICATION_TEMPLATES when no
        dict is passed. Absent templates are the normal case, not a misconfigured
        one: the default body is already true and complete.
        """
        self.templates = templates if templates is not None else _load_templates()
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
        """Build the SMS body: what the protocol knows, and nothing else.

        This used to hold five hand-written templates, in Spanish, describing a
        home — and the emergency one ended with "Llamar al 107 (SAME)", the
        medical emergency number of one country, hard-coded into a protocol that
        claims to work anywhere. An operator elsewhere received, during a real
        emergency, a number that does not answer.

        It was not made configurable on purpose. An option for "who to call"
        still assumes there is someone to call: an industrial deployment stops a
        line, an aerial one notifies a ground station, a clinical one pages a
        team. The protocol has no business having a view on that.

        What is left is what the hub actually knows and can state truthfully in
        any domain: which intent fired, at what urgency, where if a location was
        given, and any message the caller passed. A deployment that wants its own
        wording supplies templates (see `templates` below); the default is short,
        factual and promises nothing.
        """
        location = context.get("location", "")
        at = f" at {location}" if location else ""

        template = (self.templates or {}).get(intent)
        if template:
            # The context comes first and the protocol's own fields override
            # it: passing both as keywords raised TypeError whenever a context
            # carried "location" — which is most emergencies — and the
            # exception escaped, taking the whole notification with it. Caught
            # here as well now: a template must never be able to silence an
            # alert.
            fields = dict(context)
            fields.update(intent=intent, urgency=urgency, location=location)
            try:
                return template.format(**fields)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                # A broken template must not silence an emergency notification.
                log.warning("notification template for %s is invalid (%s) — "
                            "falling back to the default body", intent, exc)

        if intent == "notify":
            msg = context.get("message", "")
            return f"DOSYNC\n{msg}" if msg else f"DOSYNC{at}"
        return f"DOSYNC {urgency.upper()}{at}\nIntent: {intent}"

    async def execute(self, action, urgency):
        from ..models import ActionResult
        params = action.params or {}
        message = params.get('message', 'DoSync notification')
        try:
            await self.notify(
                intent=action.action,
                urgency=urgency.value if hasattr(urgency, 'value') else str(urgency),
                context={'message': message},
                to=None
            )
            return ActionResult(device_id=action.device_id, action=action.action, success=True, response={'status': 'sent'})
        except Exception as e:
            return ActionResult(device_id=action.device_id, action=action.action, success=False, error=failure_reason(e))

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
