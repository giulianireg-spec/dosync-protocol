"""
llm_resolver.py — DoSync LLM Resolver (provider-agnostic)

Implements the DoSync External Resolver Protocol (§5 of RESOLVER-SPEC-v0.3.md).
Uses the OpenAI-compatible Chat Completions API — the de facto standard adopted
by every major local and cloud LLM provider.

Compatible with any server that implements POST /v1/chat/completions:
    Ollama          — ollama serve  →  http://localhost:11434/v1
    LM Studio       — lmstudio      →  http://localhost:1234/v1
    llamafile       — ./model.llamafile --port 8080  →  http://localhost:8080/v1
    vllm            — vllm serve    →  http://hostname:8000/v1
    LocalAI         — localai       →  http://localhost:8080/v1
    OpenAI          —               →  https://api.openai.com/v1
    Mistral         —               →  https://api.mistral.ai/v1
    FamilyOS AI     — (future)      →  http://familyos.local/v1

Usage:
    pip install aiohttp
    python3 llm_resolver.py

Configuration (environment variables or CLI flags):
    LLM_BASE_URL    OpenAI-compatible base URL  (default: http://localhost:11434/v1)
    LLM_MODEL       Model name                  (default: llama3.2)
    LLM_API_KEY     API key if required         (default: none — local servers)
    LLM_TIMEOUT     Request timeout in seconds   (default: 10.0 — increase for cloud)
    LLM_CONCURRENCY Max parallel LLM requests    (default: 2 — increase for cloud providers)
    RESOLVER_PORT   Port to listen on           (default: 8080)

Hub configuration:
    DOSYNC_RESOLVER_URL=http://localhost:8080 uvicorn server:app --port 47200

Provider-specific setup examples:
    # Ollama
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.2

    # LM Studio
    LLM_BASE_URL=http://localhost:1234/v1 LLM_MODEL=llama-3.2-3b-instruct

    # OpenAI
    LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini LLM_API_KEY=sk-...

    # Mistral
    LLM_BASE_URL=https://api.mistral.ai/v1 LLM_MODEL=mistral-small LLM_API_KEY=...

Note on emergency intents:
    LLM inference takes 2-10 seconds depending on hardware and model size.
    For 'emergency' urgency, the hub's 5-second timeout will typically expire
    before the LLM responds — causing automatic fallback to CapabilityMatchingResolver.
    This is intentional: emergency responses should be deterministic and instant.
    Use this resolver for 'alert' and 'info' urgency where latency is acceptable.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import argparse
from typing import Optional
from aiohttp import web, ClientSession, ClientTimeout

# ── Configuration ─────────────────────────────────────────────────────────────

LLM_BASE_URL   = os.environ.get("LLM_BASE_URL",  "http://localhost:11434/v1")
LLM_MODEL      = os.environ.get("LLM_MODEL",     "llama3.2")
LLM_API_KEY    = os.environ.get("LLM_API_KEY",   "")
LLM_TIMEOUT    = float(os.environ.get("LLM_TIMEOUT", "10.0"))  # seconds — increase for cloud providers
RESOLVER_PORT  = int(os.environ.get("RESOLVER_PORT", "8080"))
RESOLVER_HOST  = os.environ.get("RESOLVER_HOST",  "0.0.0.0")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("llm-resolver")

# Concurrency limit — prevents overwhelming local LLM servers
# Set LLM_CONCURRENCY=4 for powerful cloud providers
_LLM_SEMAPHORE: asyncio.Semaphore | None = None  # initialized in main_async


# ── Prompt builder ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a physical space coordinator for the DoSync Protocol.
Your job is to decide which physical devices should respond to an intent and what
action each device should take. You receive an intent and a list of registered
devices, and you return a structured JSON ActionPlan.

You must:
- Only include devices that are relevant to the intent
- Only use actions that are listed in each device's actuator list
- Return valid JSON with no explanation, no markdown, no text after the closing brace
- Assign relevance_score between 0.0 and 100.0 based on how directly relevant each device is"""


def build_prompt(intent: dict, registry: list[dict]) -> str:
    """
    Builds the user prompt from the intent and device registry.

    The system prompt defines the coordinator role.
    The user prompt provides the specific intent and available devices.
    """
    intent_name = intent.get("intent", "unknown")
    urgency     = intent.get("urgency", "info")
    context     = intent.get("context", {})
    intent_id   = intent.get("intent_id", "unknown")

    # Build a concise device list — only include actionable devices
    device_lines = []
    for d in registry:
        actuators = [
            a.get("type") or a.get("id", "")
            for a in d.get("capabilities", {}).get("actuators", [])
            if a.get("type") or a.get("id")
        ]
        if not actuators:
            continue  # sensors-only devices cannot act
        tags      = d.get("tags", [])
        em_flag   = " [emergency_capable]" if d.get("emergency_capable") else ""
        device_lines.append(
            f"  {d['device_id']} | {d.get('device_name', '')} | "
            f"tags: {', '.join(tags)} | actuators: {', '.join(actuators)}{em_flag}"
        )

    devices_text  = "\n".join(device_lines) if device_lines else "  (no actionable devices registered)"
    context_text  = json.dumps(context, ensure_ascii=False) if context else "{}"

    # Domain hints for universal intents — custom intents are resolved by tags + context
    domain_hints = {
        "ensure_safety":  "emergency, alarm, light, lock, notification, communication",
        "alert_anomaly":  "alarm, notification, sensor, communication",
        "control_access": "lock (only — do not include alarms or cameras)",
        "notify":         "notification, communication, display, speaker",
        "report_status":  "sensor, notification, communication",
        "save_energy":    "light, plug, switch, thermostat, hvac",
        "away_mode":      "light, plug, lock, alarm, security",
        "set_environment":"thermostat, hvac, blinds, light, fan",
        "bedtime_routine":"light, blinds, thermostat",
        "morning_routine":"light, blinds, thermostat",
        "remind_chore":   "notification, communication, display",
    }
    hint = domain_hints.get(intent_name)
    hint_line = f"\nDOMAIN HINT for '{intent_name}': prioritize devices with tags — {hint}" if hint else \
                f"\nDOMAIN HINT: '{intent_name}' is a custom intent — reason from device tags and context"

    return f"""INTENT: {intent_name}
URGENCY: {urgency}
CONTEXT: {context_text}
{hint_line}

RULES:
1. EMERGENCY urgency → include ALL emergency_capable devices without exception
2. ALERT urgency → include devices whose tags match the intent domain
3. INFO urgency → include only directly relevant devices
4. Only use actions listed in each device's actuator list
5. Assign higher relevance_score to more directly relevant devices
6. If no devices are relevant, return an empty actions array

AVAILABLE DEVICES (device_id | name | tags | actuators):
{devices_text}

Respond with ONLY this JSON, nothing else, stop after the closing brace:
{{
  "intent_id": "{intent_id}",
  "urgency": "{urgency}",
  "actions": [
    {{
      "device_id": "<exact device_id from the list above>",
      "action": "<one of the listed actuators>",
      "params": {{}},
      "relevance_score": <0.0 to 100.0>
    }}
  ]
}}"""


# ── LLM API call (OpenAI-compatible) ─────────────────────────────────────────

async def query_llm(prompt: str, timeout_s: float = 10.0) -> Optional[str]:
    """
    Calls any OpenAI-compatible /v1/chat/completions endpoint.
    Returns the response text or None on error/timeout.
    """
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"

    payload = {
        "model":       LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.1,   # low for deterministic structured output
        "max_tokens":  4096,  # generous budget — some servers count input+output
        "stream":      False,
        "stop":        ["\n\n\n", "```"],  # prevent post-JSON prose
    }

    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

    sem = _LLM_SEMAPHORE
    try:
        async with (sem if sem else contextlib.nullcontext()):
            async with ClientSession(timeout=ClientTimeout(total=timeout_s)) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error("LLM provider returned HTTP %d: %s", resp.status, body[:200])
                        return None
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        log.error("LLM request timed out after %.1fs", timeout_s)
        return None
    except (KeyError, IndexError) as e:
        log.error("Unexpected LLM response format: %s", e)
        return None
    except Exception as e:
        log.error("LLM request failed: %s", e)
        return None


# ── Response parser ───────────────────────────────────────────────────────────

def parse_action_plan(
    text: str,
    intent_id: str,
    urgency: str,
    valid_device_ids: set[str],
) -> dict:
    """
    Extracts and validates the ActionPlan JSON from the LLM response.

    Handles common LLM failure modes:
    - Markdown code fences around JSON
    - Prose before or after the JSON object
    - Hallucinated device_ids (filtered against the registry)
    - Missing or malformed fields
    """
    empty = {"intent_id": intent_id, "urgency": urgency, "actions": []}

    if not text:
        return empty

    # Strip markdown code fences if present
    fence = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if fence:
        text = fence.group(1)

    # Extract the outermost JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        log.warning("No JSON object found in LLM response")
        return empty

    try:
        plan = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s | response was: %.200s", e, text)
        return empty

    # Validate and sanitize each action
    valid_actions = []
    for action in plan.get("actions", []):
        if not isinstance(action, dict):
            continue
        device_id = str(action.get("device_id", "")).strip()
        act_name  = str(action.get("action", "")).strip()

        if not device_id or not act_name:
            continue

        # Filter hallucinated device IDs — only accept IDs from the registry
        if valid_device_ids and device_id not in valid_device_ids:
            log.debug("Filtered hallucinated device_id: %s", device_id)
            continue

        valid_actions.append({
            "device_id":       device_id,
            "action":          act_name,
            "params":          action.get("params", {}),
            "relevance_score": float(action.get("relevance_score", 50.0)),
        })

    return {
        "intent_id": plan.get("intent_id", intent_id),
        "urgency":   plan.get("urgency",   urgency),
        "actions":   valid_actions,
    }


# ── HTTP handler ──────────────────────────────────────────────────────────────

async def handle_resolve(request: web.Request) -> web.Response:
    """POST /resolve — called by the DoSync hub on every intent."""
    t_start = time.monotonic()

    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body")

    intent   = body.get("intent",   {})
    registry = body.get("registry", [])

    intent_name = intent.get("intent", "unknown")
    urgency     = intent.get("urgency", "info")
    intent_id   = intent.get("intent_id", "unknown")

    # Build valid device ID set for hallucination filtering
    valid_ids = {d["device_id"] for d in registry if "device_id" in d}

    log.info("→ %s [%s] — %d devices", intent_name, urgency, len(registry))

    # Emergency always uses short timeout (LLM will fallback — by design)
    # Non-emergency uses LLM_TIMEOUT (default 10s, increase for cloud providers)
    timeout_s = 5.0 if urgency == "emergency" else LLM_TIMEOUT

    prompt       = build_prompt(intent, registry)
    log.debug("Prompt (%d chars):\n%s", len(prompt), prompt)

    raw_response = await query_llm(prompt, timeout_s)

    if raw_response is None:
        log.warning("LLM unavailable — returning 503 (hub will fallback to default resolver)")
        return web.json_response(
            {"intent_id": intent_id, "urgency": urgency, "actions": []},
            status=503,
        )

    action_plan = parse_action_plan(raw_response, intent_id, urgency, valid_ids)

    t_ms = (time.monotonic() - t_start) * 1000
    log.info("← %d actions | %.0fms | model=%s", len(action_plan["actions"]), t_ms, LLM_MODEL)

    return web.json_response(action_plan)


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — liveness and configuration check."""
    from urllib.parse import urlparse
    parsed = urlparse(LLM_BASE_URL)
    # Expose provider hostname only — not the full URL (may contain internal network info)
    provider_info = parsed.hostname or "unknown"
    return web.json_response({
        "status":   "ok",
        "resolver": "llm",
        "model":    LLM_MODEL,
        "provider": provider_info,
    })


# ── Startup check ─────────────────────────────────────────────────────────────

async def check_provider() -> bool:
    """Verify the LLM provider is reachable via GET /v1/models."""
    url = f"{LLM_BASE_URL.rstrip('/')}/models"
    headers = {}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    try:
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return True
                log.warning("Provider responded with HTTP %d", resp.status)
                return False
    except Exception as e:
        log.error("Cannot reach LLM provider at %s: %s", LLM_BASE_URL, e)
        return False


async def main_async(args):
    global _LLM_SEMAPHORE
    concurrency = int(os.environ.get("LLM_CONCURRENCY", "2"))
    _LLM_SEMAPHORE = asyncio.Semaphore(concurrency)
    log.info("LLM concurrency limit: %d parallel requests", concurrency)
    log.info("Checking LLM provider at %s ...", LLM_BASE_URL)
    ok = await check_provider()
    if ok:
        log.info("✓ Provider ready — model: %s", LLM_MODEL)
    else:
        log.warning("⚠ Provider not ready — will return 503 until reachable")

    app = web.Application()
    app.router.add_post("/resolve", handle_resolve)
    app.router.add_get("/health",   handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()

    log.info("DoSync LLM Resolver on http://%s:%d", args.host, args.port)
    log.info("Hub config: DOSYNC_RESOLVER_URL=http://localhost:%d", args.port)
    log.info("Press Ctrl+C to stop")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()


def main():
    global LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
    parser = argparse.ArgumentParser(
        description="DoSync LLM Resolver — OpenAI-compatible, provider-agnostic"
    )
    parser.add_argument("--base-url", default=LLM_BASE_URL,
                        help="OpenAI-compatible base URL (default: http://localhost:11434/v1)")
    parser.add_argument("--model",    default=LLM_MODEL,
                        help="Model name (default: llama3.2)")
    parser.add_argument("--api-key",  default=LLM_API_KEY,
                        help="API key for cloud providers (default: none)")
    parser.add_argument("--port",     type=int, default=RESOLVER_PORT,
                        help="Port to listen on (default: 8080)")
    parser.add_argument("--host",     default=RESOLVER_HOST,
                        help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--debug",    action="store_true",
                        help="Show full prompts in logs")
    args = parser.parse_args()

    LLM_BASE_URL = args.base_url
    LLM_MODEL    = args.model
    LLM_API_KEY  = args.api_key

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
