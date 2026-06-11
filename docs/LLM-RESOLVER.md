# DoSync LLM Resolver

A standalone ExternalResolver that uses any OpenAI-compatible LLM to resolve
DoSync intents into ActionPlans. Provider-agnostic by design.

## Compatible providers

Any server implementing `POST /v1/chat/completions`:

| Provider | Base URL | Notes |
|---|---|---|
| **Ollama** | `http://localhost:11434/v1` | `ollama serve` — local, free |
| **LM Studio** | `http://localhost:1234/v1` | GUI for local models |
| **llamafile** | `http://localhost:8080/v1` | Single-file local server |
| **vllm** | `http://hostname:8000/v1` | High-performance local inference |
| **LocalAI** | `http://localhost:8080/v1` | OpenAI-compatible local server |
| **OpenAI** | `https://api.openai.com/v1` | Cloud, requires API key |
| **Mistral** | `https://api.mistral.ai/v1` | Cloud, requires API key |
| **FamilyOS AI** | `http://familyos.local/v1` | Future local AI infrastructure |

## Requirements

```bash
pip install aiohttp
```

A running LLM server with at least one model loaded.

## Quick start

```bash
# With Ollama (local)
ollama pull llama3.2
python3 llm_resolver.py

# With LM Studio (local)
python3 llm_resolver.py --base-url http://localhost:1234/v1 --model llama-3.2-3b-instruct

# With OpenAI (cloud)
python3 llm_resolver.py \
  --base-url https://api.openai.com/v1 \
  --model gpt-4o-mini \
  --api-key sk-your-key-here
```

Then configure the hub:
```bash
DOSYNC_RESOLVER_URL=http://localhost:8080 uvicorn server:app --host 0.0.0.0 --port 47200
```

## Configuration

| Variable | CLI flag | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | `--base-url` | `http://localhost:11434/v1` | OpenAI-compatible base URL |
| `LLM_MODEL` | `--model` | `llama3.2` | Model name |
| `LLM_API_KEY` | `--api-key` | _(none)_ | API key for cloud providers |
| `RESOLVER_PORT` | `--port` | `8080` | Port to listen on |
| `RESOLVER_HOST` | `--host` | `0.0.0.0` | Bind address |

## Hardware requirements and performance

The LLM resolver performance depends entirely on the inference backend:

| Hardware | Model | Approx. response time |
|---|---|---|
| Raspberry Pi 5 (CPU only) | llama3.2:1b | ~40–60s |
| Raspberry Pi 5 (CPU only) | llama3.2:3b | >60s (timeout) |
| Modern laptop with GPU | llama3.2:3b | ~3–8s |
| Cloud API (OpenAI, Mistral) | gpt-4o-mini / mistral-small | ~1–3s |

For real-time use on Pi CPU, point to a cloud provider or a more powerful machine on the local network. The fallback to `CapabilityMatchingResolver` ensures the hub always responds even when the LLM is too slow.

## Note on emergency intents

LLM inference takes 2–10 seconds. For `emergency` urgency, the hub's 5-second
timeout typically expires before the LLM responds, triggering automatic fallback
to `CapabilityMatchingResolver`. **This is intentional** — emergency responses
must be deterministic and instant. Use the LLM resolver for `alert` and `info`
urgency where latency is acceptable and contextual reasoning adds value.

## Architecture

```
DoSync Hub
    │ POST /resolve (intent + full device registry)
    ▼
llm_resolver.py                    ← this file
    │ POST /v1/chat/completions
    ▼
Any OpenAI-compatible LLM server
(Ollama · LM Studio · OpenAI · FamilyOS AI · ...)
    │ JSON response
    ▼
parse + validate ActionPlan
    │ hallucinated device_ids filtered against registry
    └──► ActionPlan back to hub
```

## How it works

1. Hub calls `POST /resolve` with the intent and full device registry
2. Resolver builds a structured prompt with the intent, context, and device list
3. LLM reasons about which devices are relevant and what actions to take
4. Response is parsed and validated — hallucinated device IDs are filtered
5. ActionPlan is returned to the hub

If the LLM is unavailable or times out, the resolver returns HTTP 503 and the
hub automatically falls back to the built-in `CapabilityMatchingResolver`.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
