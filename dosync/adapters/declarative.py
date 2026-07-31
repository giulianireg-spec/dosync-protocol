"""Executes declarative adapters — one adapter for every device described in a file.

There is exactly one of these, no matter how many declarative devices exist. It
reads the transport definition the loader stored on each manifest and performs
the request. Adding a device is adding a file; it is never adding a class.

Supports HTTP and MQTT. Anything needing pairing, session state or a vendor SDK
is refused when the file loads rather than when an intent needs the device.
"""
import asyncio
import json
import logging
import re
from typing import Any

from . import DoSyncAdapter
from ..models import ActionResult

log = logging.getLogger("dosync.adapters.declarative")

#: `{params.temperature}` and `{device.id}` in a URL, header or body.
_PLACEHOLDER = re.compile(r"\{(params|device)\.([A-Za-z_][A-Za-z0-9_]*)\}")


def _substitute(value: Any, params: dict, device_id: str) -> Any:
    """Fill placeholders, recursing through the structure.

    Values are substituted, never evaluated. A declarative file is written by an
    operator and read by a hub that actuates physical devices; a template
    language would be a scripting language, and a scripting language in a device
    description is a way to run code without anyone deciding to.
    """
    if isinstance(value, str):
        def repl(m):
            scope, key = m.group(1), m.group(2)
            if scope == "device":
                return device_id if key == "id" else m.group(0)
            return str(params.get(key, m.group(0)))
        return _PLACEHOLDER.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute(v, params, device_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, params, device_id) for v in value]
    return value


class DeclarativeAdapter(DoSyncAdapter):
    """One adapter for every device described declaratively."""

    adapter_kind = "ecosystem"
    adapter_name = "declarative"

    def __init__(self, hub=None, timeout: float = 10.0):
        self._hub = hub
        self._timeout = timeout

    def _definition(self, device_id: str) -> dict | None:
        device = self._hub.registry.get(device_id) if self._hub else None
        if device is None:
            return None
        return getattr(device, "adapter_config", None) or None

    async def execute(self, action, urgency) -> ActionResult:
        definition = self._definition(action.device_id)
        if not definition:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"No declarative definition for '{action.device_id}'")

        spec = (definition.get("actions") or {}).get(action.action)
        if not spec:
            # Named separately from a transport failure: the device is fine, the
            # file simply does not describe this action, and the operator can fix
            # that in a text editor.
            available = ", ".join((definition.get("actions") or {}).keys()) or "none"
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"Action '{action.action}' is not declared for this device. "
                      f"Declared: {available}")

        transport = definition.get("transport") or {}
        kind = str(transport.get("kind", "http")).lower()
        if kind == "http":
            return await self._http(action, transport, spec)
        if kind == "mqtt":
            return await self._mqtt(action, transport, spec)
        # Unreachable in practice — the loader refuses unknown transports — but
        # a device could be registered through the API with a hand-made config.
        return ActionResult(
            device_id=action.device_id, action=action.action, success=False,
            error=f"Transport '{kind}' is not supported by declarative adapters. "
                  f"A device needing {kind} needs a code adapter.")

    async def _mqtt(self, action, transport: dict, spec: dict) -> ActionResult:
        """Publish one message and report whether the broker accepted it.

        MQTT is fire-and-forget by nature, and this is deliberately honest about
        what that means: a successful publish says the BROKER took the message,
        not that the device acted on it. At QoS 0 it does not even say that
        much. Anything stronger requires the device to confirm — which is what
        `verify_with` is for, and why the distinction between "we sent it" and
        "we know it happened" exists in this protocol at all.
        """
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error="paho-mqtt is required for declarative MQTT devices")

        publish = spec.get("publish") or {}
        params = dict(action.params or {})
        topic = _substitute(str(publish.get("topic", "")), params, action.device_id)
        payload = _substitute(publish.get("payload", ""), params, action.device_id)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)

        broker = str(transport.get("broker", ""))
        port = int(transport.get("port", 1883))
        qos = int(publish.get("qos", transport.get("qos", 1)))

        def _send() -> tuple[bool, str]:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            user = transport.get("username")
            if user:
                client.username_pw_set(user, transport.get("password"))
            try:
                client.connect(broker, port,
                               keepalive=int(transport.get("keepalive", 30)))
                client.loop_start()
                info = client.publish(topic, str(payload), qos=qos)
                info.wait_for_publish(timeout=float(transport.get("timeout", 10)))
                delivered = info.is_published()
                return delivered, "" if delivered else "broker did not confirm publish"
            finally:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass

        try:
            ok, err = await asyncio.get_running_loop().run_in_executor(None, _send)
        except Exception as e:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"{type(e).__name__}: {e}")

        return ActionResult(
            device_id=action.device_id, action=action.action, success=ok,
            response={"topic": topic, "qos": qos,
                      "note": "the broker accepted the message; whether the device "
                              "acted on it is not knowable from a publish"},
            error=err or None)

    async def _http(self, action, transport: dict, spec: dict) -> ActionResult:
        try:
            import aiohttp
        except ImportError:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error="aiohttp is required for declarative HTTP devices "
                      "(pip install aiohttp)")

        request = spec.get("request") or {}
        params = dict(action.params or {})
        base = str(transport.get("base_url", "")).rstrip("/")
        path = _substitute(str(request.get("path", "/")), params, action.device_id)
        url = f"{base}{path}" if base else path
        method = str(request.get("method", "POST")).upper()
        headers = _substitute(dict(transport.get("headers") or {}), params, action.device_id)
        headers.update(_substitute(dict(request.get("headers") or {}), params, action.device_id))
        body = _substitute(request.get("body"), params, action.device_id)

        try:
            timeout = aiohttp.ClientTimeout(
                total=float(transport.get("timeout", self._timeout)))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                kwargs = {"headers": headers} if headers else {}
                if body is not None:
                    if isinstance(body, (dict, list)):
                        kwargs["json"] = body
                    else:
                        kwargs["data"] = body
                async with session.request(method, url, **kwargs) as resp:
                    text = await resp.text()
                    ok = 200 <= resp.status < 300
                    return ActionResult(
                        device_id=action.device_id, action=action.action,
                        success=ok,
                        response={"status": resp.status, "body": text[:500]},
                        error=None if ok else f"HTTP {resp.status}: {text[:200]}")
        except asyncio.TimeoutError:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"timeout after {transport.get('timeout', self._timeout)}s")
        except Exception as e:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"{type(e).__name__}: {e}")

    async def get_state(self, device_id: str) -> dict | None:
        """Read declared sensors, so declarative devices participate in health
        and in verification like any other."""
        definition = self._definition(device_id)
        if not definition:
            return None
        sensors = definition.get("sensors") or {}
        transport = definition.get("transport") or {}
        if not sensors or str(transport.get("kind", "http")).lower() != "http":
            return None

        try:
            import aiohttp
        except ImportError:
            return None

        state = {}
        base = str(transport.get("base_url", "")).rstrip("/")
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for name, spec in sensors.items():
                    request = spec.get("request") or {}
                    if not request:
                        continue
                    url = f"{base}{request.get('path', '/')}"
                    async with session.get(url) as resp:
                        if resp.status >= 300:
                            continue
                        payload = await resp.text()
                    field = request.get("extract")
                    try:
                        data = json.loads(payload)
                        state[name] = data.get(field) if field else data
                    except ValueError:
                        state[name] = payload.strip()
        except Exception as e:
            log.debug("Declarative get_state failed for %s: %s", device_id, e)
            return None
        return state or None
