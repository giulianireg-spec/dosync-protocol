"""
DoSync — MCP Server
====================
Expone el hub DoSync como un servidor MCP (Model Context Protocol).

Con esto, cualquier LLM que soporte MCP (Claude, ChatGPT, Cursor, etc.)
puede controlar el hogar via DoSync sin configuración adicional.

Uso:
    # Arrancar como servidor MCP standalone (stdio — para Claude Desktop)
    PYTHONPATH=. python3 dosync/mcp_server.py

    # Con autenticación
    DOSYNC_TOKEN=<tu-token> PYTHONPATH=. python3 dosync/mcp_server.py

Configuración en Claude Desktop (~/.config/claude/claude_desktop_config.json):
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
    dosync_fire_intent      — ejecutar una intención semántica
    dosync_list_devices     — listar dispositivos registrados
    dosync_get_status       — estado del hub + ocupación
    dosync_send_event       — enviar un evento de dispositivo
    dosync_get_audit_log    — últimas entradas del audit log
    dosync_get_scenarios    — listar escenarios disponibles
"""

from __future__ import annotations
import asyncio
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

# ── Configuración ─────────────────────────────────────────────────────────────

HUB_URL   = os.environ.get("DOSYNC_HUB_URL", "http://localhost:47200")
HUB_TOKEN = os.environ.get("DOSYNC_TOKEN", "")

# ── HTTP helper ───────────────────────────────────────────────────────────────

async def hub_request(method: str, path: str, body: dict = None) -> dict:
    """Llama a la API REST del hub DoSync."""
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

    async with httpx.AsyncClient() as client:
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
    """Formatea la respuesta del hub para el LLM."""
    if "error" in data:
        return f"Error: {data['error']}"
    return json.dumps(data, indent=2, ensure_ascii=False)


# ── MCP Server ────────────────────────────────────────────────────────────────

server = Server("dosync-hub")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """Define las herramientas disponibles para el LLM."""
    return [

        types.Tool(
            name="dosync_fire_intent",
            description=(
                "Ejecuta una intención semántica en el hub DoSync. "
                "El hub resuelve automáticamente qué dispositivos actúan y cómo, "
                "basándose en sus capacidades declaradas. "
                "Usar para: emergencias, rutinas, control del ambiente, notificaciones."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Clase de intención",
                        "enum": [
                            "ensure_safety", "notify_family", "report_status",
                            "set_environment", "control_access", "monitor_health",
                            "save_energy", "remind_chore", "alert_anomaly",
                            "bedtime_routine", "morning_routine", "away_mode",
                        ],
                    },
                    "urgency": {
                        "type": "string",
                        "description": "Nivel de urgencia",
                        "enum": ["info", "warning", "alert", "emergency"],
                        "default": "info",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Sujeto de la intención (ej: 'abuela', 'niños')",
                    },
                    "message": {
                        "type": "string",
                        "description": "Mensaje a incluir en notificaciones",
                    },
                    "location": {
                        "type": "string",
                        "description": "Ubicación relevante (ej: 'dormitorio', 'sala')",
                    },
                },
                "required": ["intent"],
            },
        ),

        types.Tool(
            name="dosync_list_devices",
            description=(
                "Lista todos los dispositivos registrados en el hub DoSync "
                "con sus capacidades, tags, y estado del adapter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_tag": {
                        "type": "string",
                        "description": "Filtrar por tag (ej: 'emergency', 'light', 'sensor')",
                    },
                    "emergency_only": {
                        "type": "boolean",
                        "description": "Mostrar solo dispositivos con emergency_capable=true",
                        "default": False,
                    },
                },
            },
        ),

        types.Tool(
            name="dosync_get_status",
            description=(
                "Obtiene el estado actual del hub DoSync: "
                "cantidad de dispositivos, ocupación del hogar, "
                "integridad del audit log, y estadísticas de la base de datos."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        types.Tool(
            name="dosync_send_event",
            description=(
                "Envía un evento desde un dispositivo al hub. "
                "Usar para simular eventos de sensores (caída detectada, "
                "movimiento, avería, etc.) o para integrar dispositivos "
                "que no tienen adapter nativo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID del dispositivo que emite el evento",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Tipo de evento (ej: 'fall_detected', 'malfunction', 'motion')",
                    },
                    "severity": {
                        "type": "string",
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
                "Obtiene las últimas entradas del audit log del hub. "
                "El log es tamper-evident: cada entrada está encadenada "
                "con SHA-256. Incluye verificación de integridad."
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
                "con descripción de cuándo usar cada uno."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),

        types.Tool(
            name="dosync_control_device",
            description=(
                "Control directo de un dispositivo específico. "
                "Usar cuando el usuario pide controlar un dispositivo concreto: "
                "'apagá las luces', 'poné las luces en rojo', 'subí el brillo', etc. "
                "Para luces WiZ soporta: turn_on, turn_off, set_brightness (0-100), "
                "set_color (r/g/b 0-255), set_color_temp (kelvin 2200-6500). "
                "Para apagar TODAS las luces, llamar con device_id='all_lights'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "device_id": {
                        "type": "string",
                        "description": "ID del dispositivo o 'all_lights' para todas las luces",
                    },
                    "action": {
                        "type": "string",
                        "description": "Acción a ejecutar",
                        "enum": ["turn_on", "turn_off", "set_brightness",
                                 "set_color", "set_color_temp"],
                    },
                    "brightness": {
                        "type": "integer",
                        "description": "Brillo 0-100 (para set_brightness o turn_on)",
                    },
                    "r": {"type": "integer", "description": "Rojo 0-255"},
                    "g": {"type": "integer", "description": "Verde 0-255"},
                    "b": {"type": "integer", "description": "Azul 0-255"},
                    "kelvin": {
                        "type": "integer",
                        "description": "Temperatura de color en Kelvin (2200-6500)",
                    },
                },
                "required": ["device_id", "action"],
            },
        ),

    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Ejecuta una herramienta y retorna el resultado al LLM."""

    # ── dosync_fire_intent ────────────────────────────────────────────────────
    if name == "dosync_fire_intent":
        intent   = arguments.get("intent")
        urgency  = arguments.get("urgency", "info")
        subject  = arguments.get("subject")
        message  = arguments.get("message", "")
        location = arguments.get("location", "")

        body = {
            "intent":  intent,
            "urgency": urgency,
            "subject": subject,
            "context": {
                "message":          message,
                "location":         location,
                "trigger":          "mcp_client",
                "emergency_number": "911",
            },
        }

        result = await hub_request("POST", "/v1/intent", body)

        if "error" in result:
            text = f"❌ Error ejecutando intent '{intent}': {result['error']}"
        else:
            actions      = result.get("actions_taken", 0)
            failed       = result.get("failed_devices", [])
            results_list = result.get("results", [])
            core_success = actions > 0
            icon = "✅" if core_success else "⚠️"

            text  = f"{icon} Intent '{intent}' [{urgency}] ejecutado\n"
            text += f"  Acciones completadas: {actions}\n"

            if failed:
                text += f"  Sin respuesta ({len(failed)} dispositivos apagados físicamente) — no afecta el intent\n"

            critical = [r for r in results_list
                       if r.get("success") and r.get("action") in
                       ("unlock","alarm","call","notify","turn_on","set_brightness")]
            if critical:
                text += "\nAcciones críticas ejecutadas:\n"
                for r in critical[:8]:
                    resp = r.get("response", {})
                    status = resp.get("status","ok") if isinstance(resp, dict) else "ok"
                    text += f"  ✓ [{r['device_id']}] {r['action']} → {status}\n"

        return [types.TextContent(type="text", text=text)]

    # ── dosync_list_devices ───────────────────────────────────────────────────
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
                    text="No se encontraron dispositivos con los filtros aplicados.")]

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
        text += f"  Ocupación:    {'Sí, hay alguien en casa' if occupied else 'Casa vacía'}\n"
        text += f"  Audit log:    {result.get('audit_entries', 0)} entradas "
        text += f"({'✓ íntegro' if integrity else '✗ comprometido'})\n"
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
            text = (f"✅ Evento recibido por el hub:\n"
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
        text += f"Integridad: {'✓ íntegra' if integrity else '✗ comprometida'}\n\n"

        # Mostrar las últimas N
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
  ensure_safety    — Alguien se cayó, hay peligro. Abre puertas, llama emergencias, activa alarma.
  (+ send_event)   — Enviar evento smoke_detected para emergencia de incendio (3 fases).

NOTIFICACIONES (urgency=warning/info):
  notify_family    — Alertar a la familia sobre algo importante.
  alert_anomaly    — Consumo eléctrico anormal, algo inesperado detectado.
  remind_chore     — Recordar una tarea doméstica (lavarropas terminó, etc.).

ENERGÍA:
  save_energy      — Nadie en casa, activar modo ahorro (luces, clima).
  away_mode        — Todos salieron, armar seguridad y bajar consumo.

RUTINAS:
  morning_routine  — Buenos días: persianas, cafetera, clima.
  bedtime_routine  — Hora de dormir: atenuar luces, bajar persianas.

AMBIENTE:
  set_environment  — Ajustar luces, temperatura, persianas.
  control_access   — Bloquear/desbloquear puertas.
  monitor_health   — Activar monitoreo continuo de una persona.

INFORMACIÓN:
  report_status    — Leer el estado de todos los sensores.

Niveles de urgencia:
  info      — rutinas, recordatorios
  warning   — alertas no críticas
  alert     — situación que requiere atención
  emergency — actúa inmediatamente sin confirmación
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

        # all_lights: get all light devices and control them
        if device_id == "all_lights":
            devices_result = await hub_request("GET", "/v1/devices")
            if "error" in devices_result:
                return [types.TextContent(type="text",
                        text=f"Error obteniendo dispositivos: {devices_result['error']}")]

            light_devices = [
                d["device_id"] for d in devices_result.get("devices", [])
                if any(t in d.get("tags", []) for t in ["light", "wiz"])
            ]

            if not light_devices:
                return [types.TextContent(type="text",
                        text="No se encontraron dispositivos de luz registrados.")]

            results = []
            for did in light_devices:
                body = {"device_id": did, "action": action, "params": params}
                r = await hub_request("POST", "/v1/device/action", body)
                icon = "✓" if r.get("success") and not r.get("error") else "✗"
                results.append(f"  {icon} {did}")

            icon_on = "✅" if action == "turn_on" else "🌑"
            text  = f"{icon_on} {action} en {len(light_devices)} luces:\n"
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
            text = f"⚠️ {device_id}: {action} falló"

        return [types.TextContent(type="text", text=text)]

    else:
        return [types.TextContent(
            type="text",
            text=f"Herramienta desconocida: {name}",
        )]


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    if not HUB_TOKEN:
        log.warning(
            "DOSYNC_TOKEN no configurado — los requests al hub pueden fallar "
            "si la autenticación está habilitada. "
            "Usar: DOSYNC_AUTH=false para deshabilitar, o "
            "DOSYNC_TOKEN=<token> para autenticar."
        )

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        from mcp.server.models import InitializationOptions
        from mcp.server.lowlevel.server import NotificationOptions
        caps = server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        )

        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="dosync-hub",
                server_version="0.1.0",
                capabilities=caps,
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())