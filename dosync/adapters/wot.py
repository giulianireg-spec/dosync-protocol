"""Execute the actions of imported WoT Things through their TD's HTTP forms.

A Thing imported by dosync/wot_import.py carries, in its adapter_config, each
governed action's affordance: an action (invokeaction) or a writable property
(writeproperty), with the forms of its TD. This adapter performs them with the
WoT HTTP binding defaults -- POST to invoke an action, PUT to write a property,
GET to read one, JSON bodies -- after the hub has already decided, under its
guarantees and the operator's policies, that the action may run.

Only HTTP(S) forms are executed. A Thing whose TD offers only CoAP or MQTT forms
is imported and governed, and its actions are refused here with that reason,
rather than pretended. Security: ``nosec`` is assumed unless the operator gives
a bearer token for the Thing in adapter_config["bearer_token_env"] (the name of
an environment variable, never the token itself).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import quote

from . import DoSyncAdapter
from ..models import ActionResult


_TEMPLATE = re.compile(r"\{([?&/#+.;]?)([^}]*)\}")


def expand(href: str, params: dict) -> tuple[str, dict]:
    """Expand the RFC 6570 templates TD forms use for uriVariables -- ``{var}``
    in a path and ``{?a,b}`` / ``{&a}`` in a query -- from the action's params.
    Variables not given are dropped; the params used are removed from the body.
    Found against node-wot's own example Thing: its counter's form is
    ``.../actions/increment{?step}``, and the literal template is a 404."""
    rest = dict(params)

    def fill(m):
        op, names = m.group(1), [n.strip() for n in m.group(2).split(",") if n.strip()]
        given = [(n, rest.pop(n)) for n in names if n in rest]
        if op in ("?", "&"):
            if not given:
                return ""
            q = "&".join(f"{quote(str(n))}={quote(str(v))}" for n, v in given)
            return ("?" if op == "?" else "&") + q
        return ",".join(quote(str(v), safe="") for _, v in given)
    return _TEMPLATE.sub(fill, href), rest


def _http_form(forms: list[dict]) -> dict | None:
    return next((f for f in forms if str(f.get("href", "")).startswith(("http://", "https://"))), None)


def _body_for(affordance: dict, params: dict):
    if affordance["kind"] == "property":
        return params.get("value", next(iter(params.values()), None)) if params else None
    schema = affordance.get("input") or {}
    if not params:
        return None
    if isinstance(schema, dict) and schema.get("type") not in (None, "object") and len(params) == 1:
        return next(iter(params.values()))
    return params


class WotHttpAdapter(DoSyncAdapter):
    def __init__(self, hub=None, timeout: float = 10.0):
        self._hub = hub
        self._timeout = timeout

    @property
    def adapter_name(self) -> str:
        return "wot"

    async def connect(self, config) -> bool:
        return True

    def _config(self, device_id: str) -> dict:
        device = self._hub.registry.get(device_id) if self._hub else None
        return dict(getattr(device, "adapter_config", None) or {})

    def _request(self, method: str, url: str, body, config: dict):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        env = config.get("bearer_token_env")
        if env and os.environ.get(env):
            req.add_header("Authorization", f"Bearer {os.environ[env]}")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            text = resp.read().decode(errors="replace")
            return resp.status, text

    async def execute(self, action, urgency) -> ActionResult:
        config = self._config(action.device_id)
        if action.action == "read_sensors":
            readings, errors = {}, []
            for st, spec in (config.get("sensors") or {}).items():
                form = _http_form(spec.get("forms") or [])
                if not form:
                    continue
                try:
                    status, text = await asyncio.to_thread(self._request, form.get("method") or "GET",
                                                           expand(form["href"], {})[0], None, config)
                    readings[st] = json.loads(text) if text else None
                except Exception as e:
                    errors.append(f"{st}: {type(e).__name__}: {e}")
            return ActionResult(device_id=action.device_id, action=action.action, success=not errors,
                                response={"readings": readings}, error="; ".join(errors) or None)
        affordance = (config.get("affordances") or {}).get(action.action)
        if not affordance:
            return ActionResult(device_id=action.device_id, action=action.action, success=False,
                                error=f"the Thing's TD has no affordance for '{action.action}'")
        form = _http_form(affordance.get("forms") or [])
        if not form:
            return ActionResult(device_id=action.device_id, action=action.action, success=False,
                                error="the Thing's TD offers no HTTP form for this affordance "
                                      "(only HTTP is executed; the action is governed but not run)")
        method = form.get("method") or ("PUT" if affordance["kind"] == "property" else "POST")
        url, rest = expand(form["href"], dict(action.params or {}))
        body = _body_for(affordance, rest)
        try:
            status, text = await asyncio.to_thread(self._request, method, url, body, config)
            return ActionResult(device_id=action.device_id, action=action.action, success=200 <= status < 300,
                                response={"status": status, "body": text[:500]})
        except urllib.error.HTTPError as e:
            return ActionResult(device_id=action.device_id, action=action.action, success=False,
                                error=f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        except Exception as e:
            return ActionResult(device_id=action.device_id, action=action.action, success=False,
                                error=f"{type(e).__name__}: {e}")

    async def get_state(self, device_id: str) -> dict | None:
        return None
