"""
DoSync — MCP Server
====================
Exposes the DoSync hub as an MCP (Model Context Protocol) server.

Con esto, cualquier LLM que soporte MCP (Claude, ChatGPT, Cursor, etc.)
can then act through DoSync with no further configuration.

Usage:
    # Start as a standalone MCP server (stdio — for a desktop client)
    PYTHONPATH=. python3 dosync/mcp_server.py

    # With authentication
    DOSYNC_TOKEN=<tu-token> PYTHONPATH=. python3 dosync/mcp_server.py

Claude Desktop configuration (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "dosync": {
          "command": "python3",
          "args": ["/ruta/a/dosync/dosync/mcp_server.py"],
          "env": {
            "DOSYNC_TOKEN": "<tu-token>",
            "DOSYNC_HUB_URL": "http://localhost:47200"
          }
        }
      }
    }

Herramientas expuestas al LLM:
    dosync_fire_intent      — execute a semantic intent
    dosync_list_devices     — list registered devices
    dosync_get_status       — hub state and inferred occupancy
    dosync_send_event       — send a device event
    dosync_get_audit_log    — most recent audit log entries
    dosync_get_scenarios    — listar escenarios disponibles
"""

from __future__ import annotations
import asyncio
import contextlib
import hmac
import json
import logging
import os
import sys
from typing import Any

log = logging.getLogger("dosync.mcp")

try:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.models import InitializationOptions
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("mcp not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    import httpx
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False

# ── Configuration ─────────────────────────────────────────────────────────────

HUB_URL   = os.environ.get("DOSYNC_HUB_URL", "http://localhost:47200")
HUB_TOKEN = os.environ.get("DOSYNC_TOKEN", "")
CA_CERT    = os.environ.get("DOSYNC_CA_CERT", None)

# Polling timeout for async intents.
# Derived from DOSYNC_INTENT_TIMEOUT + 3s network margin.
# emergency default: 5 + 3 = 8s, info/alert default: 10 + 3 = 13s
_EMERGENCY_HUB_TIMEOUT = float(os.environ.get("DOSYNC_INTENT_TIMEOUT", "5"))
_DEFAULT_HUB_TIMEOUT   = float(os.environ.get("DOSYNC_INTENT_TIMEOUT", "10"))
MCP_EMERGENCY_TIMEOUT  = _EMERGENCY_HUB_TIMEOUT + 3
MCP_DEFAULT_TIMEOUT    = _DEFAULT_HUB_TIMEOUT + 3
POLL_INTERVAL          = 1.0  # seconds between polls

# ── HTTP helper ───────────────────────────────────────────────────────────────

async def hub_request(method: str, path: str, body: dict = None) -> dict:
    """Call the DoSync hub's REST API."""
    headers = {"Content-Type": "application/json"}
    if HUB_TOKEN:
        headers["Authorization"] = f"Bearer {HUB_TOKEN}"

    if not HTTP_AVAILABLE:
        # Fallback sin httpx — usar urllib
        import urllib.request
        import urllib.error
        data = json.dumps(body).encode() if body else None
        req  = urllib.request.Request(
            HUB_URL + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
        except Exception as e:
            return {"error": str(e)}

    async with httpx.AsyncClient(verify=CA_CERT if CA_CERT else True) as client:
        try:
            if method == "GET":
                r = await client.get(HUB_URL + path, headers=headers, timeout=10)
            else:
                r = await client.post(HUB_URL + path, headers=headers,
                                      json=body, timeout=10)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            return {"error": str(e)}


def fmt(data: dict) -> str:
    """Format the hub response for the LLM."""
    if "error" in data:
        return f"Error: {data['error']}"
    return json.dumps(data, indent=2, ensure_ascii=False)


# ── MCP Server ────────────────────────────────────────────────────────────────

# The version is declared here rather than at the transport, because the
# Streamable HTTP session manager builds its own initialization options from
# this object and never sees anything a transport constructs. Passing it only
# to the stdio path made the same server introduce itself as DoSync 0.6.3 over
# one transport and as the MCP SDK's version over the other — the SDK falls
# back to its own package version when a server declares none.
server = Server("dosync-hub", version=__import__("dosync").__version__)

# Checked here rather than left to fail at the first decorator. The 2.x SDK
# removed `Server.list_tools()`, so an operator with that version installed got
# `AttributeError: 'Server' object has no attribute 'list_tools'` from inside a
# module they never opened — a true statement about the wrong thing. The
# dependency is capped at `<2.0` now, but a cap does not help anyone who
# already has 2.x in their environment, which is exactly who this message is
# for.
if not hasattr(server, "list_tools"):
    import mcp as _mcp
    raise RuntimeError(
        "This MCP server is written against the 1.x SDK and the installed "
        f"version is {getattr(_mcp, '__version__', 'unknown')}. The 2.x SDK "
        "changed how tools are registered, so nothing here can start.\n\n"
        "  pipx inject --force dosync 'mcp>=1.0.0,<2.0'\n\n"
        "Porting to 2.x is tracked work, not a configuration problem.")


async def _intent_property_schema() -> dict:
    """Build the JSON-schema fragment for the `intent` argument by reading the
    intent classes the hub declares. Returns a property dict either with a live
    `enum` (hub reachable) or a plain string (hub unreachable — degrade gracefully;
    the hub validates anyway). Also surfaces, in the description, which intents are
    compositions so the AI knows they need geographic context."""
    base_desc = "Semantic intent class, as declared on the hub"
    try:
        listing = await hub_request("GET", "/v1/intent-classes")
        classes = listing.get("intent_classes") if isinstance(listing, dict) else None
        if classes:
            names = [c.get("name") for c in classes if c.get("name")]
            # Note which ones are compositions — they need structured context.
            composites = [c.get("name") for c in classes
                          if c.get("name") and c.get("composition_kind")]
            desc = base_desc
            if composites:
                desc += (". Composition intents " + ", ".join(sorted(composites))
                         + " require geographic context (e.g. center=[lat,lon], "
                           "radius_m, altitude_m) passed in the 'context' object.")
            if names:
                return {"type": "string", "description": desc, "enum": sorted(names)}
    except Exception:
        pass
    # Fallback: hub unreachable. Free-form string — the hub validates on fire.
    return {"type": "string",
            "description": base_desc + " (hub not queried — the hub will validate)"}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Declare the tools available to the LLM."""
    # Read the available intent classes from the hub — the single source of truth.
    # The hub declares them in /v1/intent-classes; the MCP reflects that rather than
    # carrying its own hardcoded copy (which inevitably diverges — that is how
    # inspect_area was invisible to the AI). This is the protocol's "everything is
    # declared" principle applied to the AI layer: a new intent declared on the hub
    # appears here with no code change. The enum is only a guide for the AI; the hub
    # is the real validator (it returns 422 for an unregistered intent), so if the
    # hub is unreachable when describing tools we degrade gracefully to a free-form
    # string and the AI can still fire a known intent by name.
    intent_schema = await _intent_property_schema()
    return [

        types.Tool(
            name="dosync_fire_intent",
            description=(
                "Execute a semantic intent on the DoSync hub. "
                "The hub resolves which devices act and how, "
                "from their declared capabilities. "
                "Use for: emergencies, routines, environment control, notifications."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intent": intent_schema,
                    "urgency": {
                        "type": "string",
                        "description": "Urgency level: info=routine, alert=high-priority (action likely), emergency=bypass all policies (immediate execution)",
                        "enum": ["info", "warning", "alert", "emergency"],
                        "default": "info",
                    },
                    "context": {
                        "type": "object",
                        "description": (
                            "Structured context passed to the intent's resolver. "
                            "Free-form by design — each intent reads what it needs. "
                            "Composition intents (e.g. inspect_area) need geographic "
                            "fields: device_id (the vehicle), center=[lat,lon], "
                            "radius_m, altitude_m. Home intents may use location, "
                            "message, etc. The hub and resolver interpret it."
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": "Subject of the intent (e.g. an operator-defined group or role)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Mensaje a incluir en notificaciones",
                    },
                    "location": {
                        "type": "string",
                        "description": "Relevant location tag, as declared by the deployment",
                    },
                },
                "required": ["intent"],
            },
        ),

        types.Tool(
            name="dosync_list_devices",
            description=(
                "List every device registered on the DoSync hub, with its "
                "capabilities, tags and adapter state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_tag": {
                        "type": "string",
                        "description": "Filter by tag (e.g. 'emergency', 'light', 'sensor')",
                    },
                    "emergency_only": {
                        "type": "boolean",
                        "description": "Show only devices with emergency_capable=true",
                        "default": False,
                    },
                },
            },
        ),

        types.Tool(
            name="dosync_discover_devices",
            description=(
                "Search every transport the hub can reach — WiFi broadcast, "
                "mDNS, SSDP, Bluetooth — and report what answered. Nothing is "
                "registered by scanning; each finding reports what the device "
                "announced itself as, and the result also says which transports "
                "were searched and which were skipped, because 'nothing found' "
                "means something different when a transport was never searched."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),

        types.Tool(
            name="dosync_adopt_device",
            description=(
                "Add a discovered device to the hub's inventory under a name "
                "the operator chooses. A device whose capabilities nobody has "
                "declared is adopted as inventory: known and visible, and the "
                "hub reports that it cannot act on it rather than pretending "
                "otherwise. Describing what it can do is a separate step — see "
                "dosync_describe_device."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string",
                                  "description": "Id from the discovery result"},
                    "device_name": {"type": "string",
                                    "description": "The name the operator will see"},
                    "adapter": {"type": "string",
                                "description": "Adapter that found it, if any"},
                    "ip": {"type": "string"},
                    "service_type": {"type": "string",
                                     "description": "What it announced itself as"},
                },
                "required": ["device_id", "device_name"],
            },
        ),

        types.Tool(
            name="dosync_describe_device",
            description=(
                "Everything the hub observed about a device, plus the format "
                "and the tag vocabulary needed to declare what it can do. Use "
                "this when a device is registered and the hub reports it cannot "
                "act on it: the answer contains what you need to write a "
                "declarative adapter for it. YOU DRAFT IT; THE OPERATOR SAVES "
                "AND APPROVES IT. Only declare an action you have concrete "
                "grounds to believe exists on this device — say in a comment "
                "where you do not know, rather than writing something plausible."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {"type": "string"},
                },
                "required": ["device_id"],
            },
        ),

        types.Tool(
            name="dosync_get_status",
            description=(
                "Current state of the DoSync hub: device count, inferred "
                "occupancy, audit log integrity, and database statistics."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        types.Tool(
            name="dosync_send_event",
            description=(
                "Send an event from a device to the hub. "
                "Use to inject sensor events (a detected fall, "
                "motion, a fault) or to integrate devices "
                "that have no native adapter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID of the device emitting the event",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Tipo de evento (ej: 'fall_detected', 'malfunction', 'motion')",
                    },
                    "severity": {
                        "type": "string",
                        "description": "Event severity: info=normal, warning=notable, alert=requires attention, emergency=critical",
                        "enum": ["info", "warning", "alert", "emergency"],
                        "default": "info",
                    },
                    "data": {
                        "type": "object",
                        "description": "Datos adicionales del evento",
                    },
                },
                "required": ["device_id", "event_id"],
            },
        ),

        types.Tool(
            name="dosync_get_audit_log",
            description=(
                "Read the most recent entries of the hub audit log. "
                "The log is tamper-evident: every entry is chained "
                "with SHA-256. Includes an integrity check."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "last_n": {
                        "type": "integer",
                        "description": "Cantidad de entradas a mostrar (default: 10)",
                        "default": 10,
                    },
                },
            },
        ),

        types.Tool(
            name="dosync_get_scenarios",
            description=(
                "Devuelve la lista de escenarios disponibles en DoSync "
                "with a description of when each one applies."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        types.Tool(
            name="dosync_control_device",
            description=(
                "Act directly on one named device. Use this when the request "
                "names the device or the action, rather than a goal — for a "
                "goal, fire an intent instead and let the hub resolve it. "
                "Which actions a device accepts comes from its own capability "
                "manifest; the enum below is the set this tool can express. "
                "Direct actions are governed like any other: they pass the "
                "policy engine under the reserved 'direct_control' class, the "
                "device arbiter, and the audit log. "
                "Convenience: device_id='all_lights' applies the action to "
                "every device tagged 'light' (client-side fan-out, one request "
                "per device — not a protocol feature)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "Device ID, or 'all_lights' for every device tagged 'light'",
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to execute",
                        "enum": ["turn_on", "turn_off", "set_brightness",
                                 "set_color", "set_color_temp", "set_effect"],
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brightness 0-100 (for set_brightness or turn_on)",
                    },
                    "r": {"type": "integer", "description": "Rojo 0-255"},
                    "g": {"type": "integer", "description": "Verde 0-255"},
                    "b": {"type": "integer", "description": "Azul 0-255"},
                    "kelvin": {
                        "type": "integer",
                        "description": "Temperatura de color en Kelvin (2200-6500)",
                    },
                    "effect": {
                        "type": "string",
                        "description": "Efecto Ambilight. Valores: FOLLOW_COLOR: HOT_LAVA, FOLLOW_COLOR: DEEP_WATER, FOLLOW_COLOR: FRESH_NATURE, FOLLOW_VIDEO: STANDARD, FOLLOW_VIDEO: VIVID, FOLLOW_AUDIO: ENERGY_ADAPTIVE_BRIGHTNESS, Mode: lounge",
                    },
                },
                "required": ["device_id", "action"],
            },
        ),

    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Execute a tool and return the result to the LLM."""

    # ── dosync_fire_intent ────────────────────────────────────────────────────
    if name == "dosync_fire_intent":
        intent   = arguments.get("intent")
        urgency  = arguments.get("urgency", "info")
        subject  = arguments.get("subject")
        message  = arguments.get("message", "")
        location = arguments.get("location", "")
        # Arbitrary structured context the AI fills per intent (center/radius_m/
        # altitude_m for a composition, etc.). Passed through to the hub as-is.
        ctx = dict(arguments.get("context") or {})

        # Merge the home-automation convenience fields into context for backward
        # compatibility, without overwriting anything the AI put in `context`.
        for k, v in (("message", message), ("location", location)):
            if v and k not in ctx:
                ctx[k] = v
        ctx.setdefault("trigger", "mcp_client")

        body = {
            "intent":  intent,
            "urgency": urgency,
            "subject": subject,
            "source":  "mcp",
            "context": ctx,
        }

        # Fire async — returns intent_id immediately, no blocking
        fire_result = await hub_request("POST", "/v1/intent/async", body)

        if "error" in fire_result:
            return [types.TextContent(type="text",
                text=f"❌ Error ejecutando intent '{intent}': {fire_result['error']}")]

        intent_id = fire_result.get("intent_id")
        if not intent_id:
            return [types.TextContent(type="text",
                text=f"❌ The hub returned no intent_id for '{intent}'")]

        # Poll until completed or timeout
        # Timeout = DOSYNC_INTENT_TIMEOUT + 3s network margin
        poll_timeout = MCP_EMERGENCY_TIMEOUT if urgency == "emergency" else MCP_DEFAULT_TIMEOUT
        import time as _mcp_time
        deadline = _mcp_time.monotonic() + poll_timeout
        result = None

        while _mcp_time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            poll = await hub_request("GET", f"/v1/intent/{intent_id}")
            if "error" in poll:
                break
            if poll.get("status") != "pending":
                result = poll
                break

        # Polling timeout — make one final aggressive attempt before giving up
        if result is None:
            for _ in range(3):
                await asyncio.sleep(1.5)
                final_poll = await hub_request("GET", f"/v1/intent/{intent_id}")
                if not final_poll.get("error") and final_poll.get("status") != "pending":
                    result = final_poll
                    break

        # Still no result before the deadline — but "no final result" is NOT "no
        # information". MCP-V13: read the partial progress the hub has been
        # publishing and report what ALREADY happened, instead of an opaque
        # "still processing". In an emergency, "8 actions already executed, 2
        # devices still pending" is the difference between a useful answer and a
        # blind one.
        if result is None:
            last = await hub_request("GET", f"/v1/intent/{intent_id}")
            partial = (last or {}).get("partial") or {}
            done = partial.get("results", [])
            n_done = partial.get("actions_completed", len(done))
            ok_done = [r for r in done if r.get("success")]

            text  = f"⏳ Intent '{intent}' [{urgency}] accepted — still executing after "
            text += f"{poll_timeout + 4.5:.0f}s, partial result below\n"
            if n_done:
                text += f"  Already completed: {len(ok_done)}/{n_done} action(s) succeeded so far\n"
                for r in ok_done[:8]:
                    text += f"    ✓ {r.get('device_id')} — {r.get('action')}\n"
                slow = [r for r in done if not r.get("success")]
                if slow:
                    text += f"  Still pending / unreachable: {len(slow)} device(s)\n"
            else:
                text += f"  No actions have completed yet — the hub is still resolving or every device is slow.\n"
            text += f"  Intent ID: {intent_id} — poll GET /v1/intent/{{id}} for the final result.\n"
            return [types.TextContent(type="text", text=text)]

        actions      = result.get("actions_taken", 0)
        failed       = result.get("failed_devices", [])
        aborted      = result.get("aborted_devices", [])
        results_list = result.get("results", [])
        intent_status = result.get("status", "unknown")

        # Determine display icon based on what actually happened:
        # - success / partial with some actions → ✅
        # - partial with 0 actions (all unreachable) → ⚠️ with clear explanation
        # - failed / blocked → ❌
        if intent_status == "success" or (intent_status in ("partial", "partial_abort") and actions > 0):
            icon = "✅"
        elif intent_status in ("partial", "partial_abort", "failed") and failed:
            icon = "⚠️"  # devices unreachable — protocol worked, hardware was off
        else:
            icon = "❌"

        text  = f"{icon} Intent '{intent}' [{urgency}] ejecutado\n"
        text += f"  Acciones completadas: {actions}\n"

        if failed:
            text += f"  No response from {len(failed)} device(s) — excluded for ~30 min\n"
        if aborted:
            text += f"  Cancelados por FailurePolicy: {len(aborted)} dispositivos\n"

        critical = [r for r in results_list
                   if r.get("success") and r.get("action") in
                   ("unlock","alarm","call","notify","turn_on","set_brightness")]
        if critical:
            text += "\nCritical actions executed:\n"
            for r in critical[:8]:
                resp = r.get("response", {})
                status_val = resp.get("status","ok") if isinstance(resp, dict) else "ok"
                text += f"  ✓ [{r['device_id']}] {r['action']} → {status_val}\n"

        return [types.TextContent(type="text", text=text)]
    elif name == "dosync_list_devices":
        filter_tag     = arguments.get("filter_tag", "")
        emergency_only = arguments.get("emergency_only", False)

        result = await hub_request("GET", "/v1/devices")

        if "error" in result:
            return [types.TextContent(type="text", text=f"Error: {result['error']}")]

        devices = result.get("devices", [])

        # Aplicar filtros
        if filter_tag:
            devices = [d for d in devices if filter_tag in d.get("tags", [])]
        if emergency_only:
            devices = [d for d in devices if d.get("emergency_capable")]

        if not devices:
            return [types.TextContent(type="text",
                    text="No devices matched the given filters.")]

        text = f"📡 {len(devices)} dispositivo(s) registrado(s):\n\n"
        for d in devices:
            emerg   = "🚨 " if d.get("emergency_capable") else "   "
            adapter = d.get("adapter") or "simulated"
            tags    = ", ".join(d.get("tags", []))
            caps    = d.get("capabilities", {})
            acts    = [a["type"] for a in caps.get("actuators", [])]
            text   += f"{emerg}{d['device_name']} [{d['device_id']}]\n"
            text   += f"     Adapter: {adapter}\n"
            text   += f"     Tags: {tags}\n"
            if acts:
                text += f"     Acciones: {', '.join(acts)}\n"
            text += "\n"

        return [types.TextContent(type="text", text=text)]

    # ── dosync_discover_devices ───────────────────────────────────────────────
    elif name == "dosync_discover_devices":
        result = await hub_request("GET", "/v1/discovery/scan")
        if "error" in result:
            return [types.TextContent(type="text",
                                      text=f"Scan failed: {result['error']}")]
        found = result.get("found", [])
        text = f"Found {len(found)} device(s).\n\n"
        for d in found:
            mark = "already registered" if d.get("registered") else "not registered"
            text += (f"  {d.get('device_name') or d.get('device_id')}\n"
                     f"    announced as : {d.get('service_type') or 'unidentified'}\n"
                     f"    address      : {d.get('ip', '')}\n"
                     f"    id           : {d.get('device_id')}\n"
                     f"    status       : {mark}\n\n")
        # Saying what was NOT searched matters as much as the findings: nothing
        # answering means something different when a transport was never asked.
        text += f"Searched: {', '.join(result.get('searched', [])) or 'nothing'}\n"
        skipped = result.get("not_searchable", [])
        if skipped:
            text += f"Not searched: {', '.join(skipped)}\n"
        return [types.TextContent(type="text", text=text)]

    # ── dosync_adopt_device ───────────────────────────────────────────────────
    elif name == "dosync_adopt_device":
        payload = {k: arguments.get(k, "") for k in
                   ("device_id", "device_name", "adapter", "ip", "service_type")}
        result = await hub_request("POST", "/v1/discovery/adopt", payload)
        if "error" in result:
            return [types.TextContent(type="text",
                                      text=f"Could not adopt: {result['error']}")]
        return [types.TextContent(type="text", text=(
            f"Adopted {payload['device_name']} ({payload['device_id']}).\n"
            "It is in the inventory now. If nothing has declared what it can do, "
            "the hub will say so rather than act on it — use "
            "dosync_describe_device to draft that description."))]

    # ── dosync_describe_device ────────────────────────────────────────────────
    elif name == "dosync_describe_device":
        device_id = arguments.get("device_id", "")
        devices = await hub_request("GET", "/v1/devices")
        if "error" in devices:
            return [types.TextContent(type="text",
                                      text=f"Could not read devices: {devices['error']}")]
        match = next((d for d in devices.get("devices", devices)
                      if d.get("device_id") == device_id), None)
        if match is None:
            return [types.TextContent(
                type="text",
                text=f"No device {device_id!r} is registered. Scan first with "
                     "dosync_discover_devices, then adopt it.")]
        # The same prompt the CLI assembles, so an agent working over MCP and a
        # person working from a terminal are given identical instructions.
        from pathlib import Path
        from dosync.adapter_drafting import build_prompt
        prompt = build_prompt(match, Path(__file__).resolve().parent.parent)
        return [types.TextContent(type="text", text=prompt)]

    # ── dosync_get_status ─────────────────────────────────────────────────────
    elif name == "dosync_get_status":
        result = await hub_request("GET", "/v1/status")

        if "error" in result:
            return [types.TextContent(type="text", text=f"Error: {result['error']}")]

        occupied   = result.get("occupied", False)
        ws_clients = result.get("ws_connections", 0)
        db         = result.get("db", {})
        integrity  = result.get("audit_integrity", True)

        text  = f"🏠 DoSync Hub v{result.get('version', '?')}\n\n"
        text += f"  Protocolo:    {result.get('protocol', '?')}\n"
        text += f"  Dispositivos: {result.get('devices', 0)}\n"
        text += f"  Occupancy:    {'occupied' if occupied else 'unoccupied'}\n"
        text += f"  Audit log:    {result.get('audit_entries', 0)} entradas "
        text += f"({'✓ intact' if integrity else '✗ compromised'})\n"
        text += f"  WS clientes:  {ws_clients}\n"
        text += f"  DB:           {db.get('db_size_kb', '?')} KB en {db.get('db_path', '?')}\n"
        if result.get("family_profile"):
            text += f"  Perfil:       Familia {result['family_profile']}\n"

        return [types.TextContent(type="text", text=text)]

    # ── dosync_send_event ─────────────────────────────────────────────────────
    elif name == "dosync_send_event":
        body = {
            "device_id": arguments.get("device_id"),
            "event_id":  arguments.get("event_id"),
            "severity":  arguments.get("severity", "info"),
            "data":      arguments.get("data", {}),
        }

        result = await hub_request("POST", "/v1/event", body)

        if "error" in result:
            text = f"❌ Error: {result['error']}"
        else:
            text = (f"✅ Event received by the hub:\n"
                    f"  Dispositivo: {result.get('device_id')}\n"
                    f"  Evento:      {result.get('event_id')}\n"
                    f"  Severidad:   {result.get('severity')}\n")

        return [types.TextContent(type="text", text=text)]

    # ── dosync_get_audit_log ──────────────────────────────────────────────────
    elif name == "dosync_get_audit_log":
        last_n = arguments.get("last_n", 10)
        result = await hub_request("GET", "/v1/audit")

        if "error" in result:
            return [types.TextContent(type="text", text=f"Error: {result['error']}")]

        entries   = result.get("entries", [])
        integrity = result.get("integrity", True)
        total     = result.get("count", 0)

        text  = f"📋 Audit Log DoSync\n"
        text += f"   Total: {total} entradas | "
        text += f"Integridad: {'✓ intact' if integrity else '✗ compromised'}\n\n"

        # Show the most recent N
        for entry in list(reversed(entries))[:last_n]:
            kind  = entry.get("type", "?")
            hash_ = entry.get("hash", "")[:10]
            extra = ""
            if kind == "intent_executed":
                extra = f" → {entry.get('intent')} [{entry.get('urgency')}]"
            elif kind == "device_event":
                extra = f" → {entry.get('device_id')}: {entry.get('event_id')}"
            elif kind == "phase_executed":
                extra = f" → fase '{entry.get('phase')}'"
            elif kind == "presence_updated":
                conf = entry.get('occ_confidence', 0)
                extra = f" → occupied={entry.get('occupied')} conf={conf:.0%}"
            text += f"  [{hash_}] {kind}{extra}\n"

        return [types.TextContent(type="text", text=text)]

    # ── dosync_get_scenarios ──────────────────────────────────────────────────
    elif name == "dosync_get_scenarios":
        text = """🏠 Escenarios disponibles en DoSync:

EMERGENCIAS (urgency=emergency):
  ensure_safety    — Someone is in danger. Opens access, alerts, sounds alarms.
  (+ send_event)   — Enviar evento smoke_detected para emergencia de incendio (3 fases).

NOTIFICACIONES (urgency=warning/info):
  notify           — Push information to any target (people, displays, channels).
  alert_anomaly    — An unexpected condition was detected.
  remind_chore     — Remind about a completed or pending task.

ENERGY:
  save_energy      — Nadie en casa, activar modo ahorro (luces, clima).
  away_mode        — Todos salieron, armar seguridad y bajar consumo.

RUTINAS:
  morning_routine  — Start-of-day routine declared by the deployment.
  bedtime_routine  — Hora de dormir: atenuar luces, bajar persianas.
  # Domain-specific intents (e.g. children arrival, shift change) can be registered via POST /v1/intent-classes

AMBIENTE:
  set_environment  — Ajustar luces, temperatura, persianas.
  control_access   — Bloquear/desbloquear puertas.
  monitor_health   — Activar monitoreo continuo de una persona.

INFORMATION:
  report_status    — Read the state of every sensor.

Niveles de urgencia:
  info      — rutinas, recordatorios
  warning   — non-critical alerts
  alert     — a condition that needs attention
  emergency — acts immediately, without confirmation
"""
        return [types.TextContent(type="text", text=text)]

    # ── dosync_control_device ─────────────────────────────────────────────────
    elif name == "dosync_control_device":
        device_id = arguments.get("device_id")
        action    = arguments.get("action")
        params    = {}
        if "brightness" in arguments: params["brightness"] = arguments["brightness"]
        if "r" in arguments:          params["r"] = arguments["r"]
        if "g" in arguments:          params["g"] = arguments["g"]
        if "b" in arguments:          params["b"] = arguments["b"]
        if "kelvin" in arguments:     params["kelvin"] = arguments["kelvin"]
        if "effect" in arguments:     params["effect"] = arguments["effect"]

        # all_lights: get all light devices and control them
        if device_id == "all_lights":
            devices_result = await hub_request("GET", "/v1/devices")
            if "error" in devices_result:
                return [types.TextContent(type="text",
                        text=f"Error listing devices: {devices_result['error']}")]

            light_devices = [
                d["device_id"] for d in devices_result.get("devices", [])
                # Selected on the role tag only. This used to read
                # ["light", "wiz"] — a vendor tag, the antipattern
                # TAG-VOCABULARY documents. Measured on the reference
                # deployment: identical selection either way, because no device
                # entered through the vendor tag alone.
                if "light" in d.get("tags", [])
            ]

            if not light_devices:
                return [types.TextContent(type="text",
                        text="No devices tagged 'light' are registered.")]

            results = []
            for did in light_devices:
                body = {"device_id": did, "action": action, "params": params}
                r = await hub_request("POST", "/v1/device/action", body)
                icon = "✓" if r.get("success") and not r.get("error") else "✗"
                results.append(f"  {icon} {did}")

            icon_on = "✅" if action == "turn_on" else "🌑"
            text  = f"{icon_on} {action} on {len(light_devices)} devices:\n"
            text += "\n".join(results)
            return [types.TextContent(type="text", text=text)]

        # Single device
        body   = {"device_id": device_id, "action": action, "params": params}
        result = await hub_request("POST", "/v1/device/action", body)

        if result.get("error"):
            text = f"❌ Error: {result['error']}"
        elif result.get("success"):
            text = f"✅ {device_id}: {action} ejecutado correctamente"
        else:
            text = f"⚠️ {device_id}: {action} failed"

        return [types.TextContent(type="text", text=text)]

    else:
        return [types.TextContent(
            type="text",
            text=f"Herramienta desconocida: {name}",
        )]


# ── Main ──────────────────────────────────────────────────────────────────────

#: Where a client reaches this server from, and therefore who may.
#:
#: `stdio` assumes the client can start the server as a subprocess, which means
#: they share a machine and a filesystem. That held for as long as the only
#: deployment was one person's laptop. It stops holding the moment the hub runs
#: somewhere the agent is not — a container, a Pi across the room, a supervised
#: appliance — and that is every real deployment. The transport is a protocol
#: decision, not a packaging one, which is why it lives here rather than in a
#: launcher script.
TRANSPORT = os.environ.get("DOSYNC_MCP_TRANSPORT", "stdio").strip().lower()
MCP_HOST  = os.environ.get("DOSYNC_MCP_HOST", "127.0.0.1")
MCP_PORT  = int(os.environ.get("DOSYNC_MCP_PORT", "47210"))


def _init_options():
    """Shared by both transports so they cannot describe different servers."""
    from mcp.server.lowlevel.server import NotificationOptions
    # Derived from the server object, which is the same one the HTTP transport
    # hands to the session manager. Two transports, one description.
    return server.create_initialization_options(
        notification_options=NotificationOptions())


class _Dispatch:
    """Routes MCP paths to the session manager and everything else to Starlette.

    Exists so that the lifespan Starlette owns still runs while `/mcp` and
    `/mcp/` reach the same handler without a redirect between them.
    """

    def __init__(self, handler, fallback):
        self._handler = handler
        self._fallback = fallback

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._fallback(scope, receive, send)
            return
        await self._handler(scope, receive, send)


async def _serve_http() -> None:
    """Serve over Streamable HTTP, for a client that is not on this machine.

    Nothing about the tools changes: the same `server` object handles both
    transports, so a tool cannot behave one way over stdio and another over the
    network. What changes is who can reach it, and that is the whole reason this
    function refuses to start without a token.
    """
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    if not HUB_TOKEN:
        raise RuntimeError(
            "DOSYNC_MCP_TRANSPORT=http needs DOSYNC_TOKEN.\n\n"
            "Over stdio the operating system is the boundary: whoever can start "
            "the process is already on the machine. A port has no such boundary "
            "— on a host network it is reachable by every device on the LAN, and "
            "the tools behind it open locks and stop machines. Refusing to start "
            "is the only honest default here.")

    # `session_idle_timeout` matters for a process that runs for weeks: a client
    # that disconnects without closing leaves its session in memory, and
    # sessions accumulate. Without a bound, an authenticated caller opening them
    # in a loop is an exhaustion path.
    manager = StreamableHTTPSessionManager(
        app=server, json_response=False,
        session_idle_timeout=float(
            os.environ.get("DOSYNC_MCP_SESSION_TIMEOUT", "900")))

    async def _mcp(scope, receive, send):
        # The bearer token the hub already issues. Not a second credential: the
        # same one an operator pastes into a client today.
        header = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header = value.decode("latin-1")
                break
        # `!=` on strings stops at the first differing byte, which leaks the
        # length of the matching prefix through response time. On a LAN, with
        # repeated measurements, that is enough to reconstruct a token byte by
        # byte. `compare_digest` takes the same time whatever the input.
        if not hmac.compare_digest(
                header.removeprefix("Bearer ").strip(), HUB_TOKEN):
            await JSONResponse(
                {"error": "Missing or invalid Authorization: Bearer <token>"},
                status_code=401)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)

    async def _health(_request):
        # Deliberately unauthenticated and deliberately empty: a supervisor
        # needs to know the process is up without holding a credential, and
        # nothing here should tell an unauthenticated caller what this hub is.
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def _lifespan(_app):
        async with manager.run():
            log.info("MCP over Streamable HTTP on %s:%s%s",
                     MCP_HOST, MCP_PORT, "/mcp")
            yield

    # Mounted at the root so `/mcp` and `/mcp/` are the same endpoint rather
    # than a redirect between them. Starlette answers a bare `/mcp` mount with a
    # 307, and an HTTP client that follows a redirect is not required to resend
    # the Authorization header or the POST body — a correctly configured client
    # would arrive unauthenticated at a URL it never chose.
    async def _root(scope, receive, send):
        path = scope.get("path", "")
        if path in ("/mcp", "/mcp/"):
            await _mcp({**scope, "path": "/"}, receive, send)
            return
        await app_routes(scope, receive, send)

    app_routes = Starlette(routes=[Route("/health", _health)],
                           lifespan=_lifespan)
    app = _Dispatch(_root, app_routes)
    config = uvicorn.Config(app, host=MCP_HOST, port=MCP_PORT,
                            log_level="warning")
    await uvicorn.Server(config).serve()


async def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    if TRANSPORT in ("http", "streamable-http", "streamable_http"):
        await _serve_http()
        return

    if TRANSPORT != "stdio":
        raise RuntimeError(
            f"Unknown DOSYNC_MCP_TRANSPORT={TRANSPORT!r}. "
            "Use 'stdio' (default) or 'http'.")

    if not HUB_TOKEN:
        log.warning(
            "DOSYNC_TOKEN is not set — requests to the hub may fail "
            "if authentication is enabled. "
            "Set DOSYNC_TOKEN=<token> to authenticate, or run the hub with "
            "DOSYNC_AUTH=false to turn authentication off (development only)."
        )

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, _init_options())


if __name__ == "__main__":
    asyncio.run(main())