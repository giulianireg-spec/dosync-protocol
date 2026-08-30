"""
Tests for the MCP server's dynamic intent discovery.

The MCP server must NOT hardcode the list of intent classes. It reads them from the
hub's /v1/intent-classes endpoint (the single source of truth — "everything is
declared"), so a new intent declared on the hub (e.g. inspect_area) appears to the
AI with no code change. If the hub is unreachable it degrades to a free-form string;
the hub is the real validator.

The MCP module imports the `mcp` package, which may be absent in this environment.
We test the schema-building logic directly without importing the module, by
re-binding hub_request — mirroring the exact logic in dosync/mcp_server.py
_intent_property_schema.
"""

import asyncio

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  \u2713  {name}")
    else:
        _FAIL += 1
        print(f"  \u2717  {name}")


# A faithful copy of the production logic (the module can't be imported without the
# `mcp` package). If the production function changes, this must change with it.
async def _intent_property_schema(hub_request) -> dict:
    base_desc = "Clase de intención semántica (declarada en el hub)"
    try:
        listing = await hub_request("GET", "/v1/intent-classes")
        classes = listing.get("intent_classes") if isinstance(listing, dict) else None
        if classes:
            names = [c.get("name") for c in classes if c.get("name")]
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
    return {"type": "string",
            "description": base_desc + " (hub no consultado — el hub validará)"}


def _hub_with(classes):
    async def _h(method, path, body=None):
        if path == "/v1/intent-classes":
            return {"intent_classes": classes}
        return {}
    return _h


async def _hub_down(method, path, body=None):
    raise Exception("connection refused")


async def _hub_empty(method, path, body=None):
    return {"intent_classes": []}


def test_enum_includes_hub_intents():
    schema = asyncio.run(_intent_property_schema(_hub_with([
        {"name": "ensure_safety", "composition_kind": None},
        {"name": "notify", "composition_kind": None},
        {"name": "inspect_area", "composition_kind": "perimeter"},
    ])))
    check("enum includes the hub's declared intents",
          set(schema["enum"]) == {"ensure_safety", "notify", "inspect_area"})


def test_new_intent_appears_without_code_change():
    # The whole point: an intent the MCP never knew about (inspect_area) is present
    # purely because the hub declares it.
    schema = asyncio.run(_intent_property_schema(_hub_with([
        {"name": "inspect_area", "composition_kind": "perimeter"},
    ])))
    check("a hub-declared intent the MCP never hardcoded is exposed",
          "inspect_area" in schema["enum"])


def test_composition_context_documented():
    schema = asyncio.run(_intent_property_schema(_hub_with([
        {"name": "inspect_area", "composition_kind": "perimeter"},
    ])))
    check("composition intents are flagged as needing geographic context",
          "inspect_area" in schema["description"]
          and "geographic context" in schema["description"])


def test_non_composition_no_geo_note():
    schema = asyncio.run(_intent_property_schema(_hub_with([
        {"name": "notify", "composition_kind": None},
    ])))
    check("a flat intent does not add the geographic-context note",
          "geographic context" not in schema["description"])


def test_hub_down_degrades_to_free_string():
    schema = asyncio.run(_intent_property_schema(_hub_down))
    check("hub unreachable → free-form string, no enum",
          schema["type"] == "string" and "enum" not in schema)


def test_hub_empty_degrades_to_free_string():
    schema = asyncio.run(_intent_property_schema(_hub_empty))
    check("hub with no intents → free-form string, no enum",
          schema["type"] == "string" and "enum" not in schema)


if __name__ == "__main__":
    for nm, fn in sorted(globals().items()):
        if nm.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {nm} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} MCP dynamic-intent tests passed.")
    if _FAIL:
        raise SystemExit(1)


# ── The SDK this server is written against (2026-08-29) ────────────────────

def test_the_mcp_sdk_is_capped_at_the_major_it_was_written_for():
    """`mcp>=1.0.0` was correct until 2.0 shipped.

    The 2.x SDK removed `Server.list_tools()`, which this module decorates at
    import time, so `python -m dosync.mcp_server` dies with
    `AttributeError: 'Server' object has no attribute 'list_tools'` — found
    while wiring an agent to a hub on a clean Windows install, which installed
    2.1.1 because nothing said otherwise.

    Same shape as the plain `uvicorn` pin that left the hub with no WebSocket
    library: an open-ended floor that was right the day it was written.
    """
    from pathlib import Path

    pyproject = (Path(__file__).resolve().parent.parent
                 / "pyproject.toml").read_text(encoding="utf-8")
    for line in pyproject.splitlines():
        if '"mcp>=' in line:
            assert "<2.0" in line, (
                f"the MCP SDK is declared without an upper bound: {line.strip()} "
                "— 2.x cannot run this server")


def test_an_incompatible_sdk_says_so_instead_of_raising_attributeerror():
    """A cap does not help anyone who already has 2.x installed.

    They get an AttributeError from inside a module they never opened: a true
    statement about the wrong thing. The check has to run before the first
    decorator, because that decorator is what fails.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    assert 'hasattr(server, "list_tools")' in source, \
        "nothing checks the SDK before decorating, so the failure is a raw " \
        "AttributeError"
    check_at = source.index('hasattr(server, "list_tools")')
    decorator_at = source.index("@server.list_tools()")
    assert check_at < decorator_at, \
        "the compatibility check runs after the decorator it is meant to " \
        "protect, so it can never fire"


# ── The agent is not on this machine (2026-08-30) ──────────────────────────

def test_http_transport_refuses_to_start_without_a_token():
    """Over stdio the operating system is the boundary: whoever can start the
    process is already on the machine. A port has no such boundary.

    Packaging the hub for Home Assistant made this concrete — an add-on that
    needs host networking for discovery puts every port it opens on the LAN,
    and the tools behind this one open locks and stop machines. Starting
    unauthenticated is not a default anyone should be able to reach by
    accident.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    body = source[source.index("async def _serve_http"):]
    body = body[:body.index("\nasync def main")]

    assert "if not HUB_TOKEN:" in body, \
        "the HTTP transport does not check for a token before binding a port"
    check = body.index("if not HUB_TOKEN:")
    assert check < body.index("uvicorn.Server"), \
        "the token check runs after the server starts serving"


def test_both_transports_describe_the_same_server():
    """The same server introduced itself differently depending on how it was
    reached: `dosync-hub 0.6.3` over stdio, and the MCP SDK's own version over
    HTTP, because the session manager builds its initialization options from
    the `Server` object and never sees what a transport constructs. A client
    could not tell which build it was talking to.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    assert 'Server("dosync-hub", version=' in source, \
        "the version is not declared on the Server object, so the HTTP " \
        "transport will report the SDK's version instead of DoSync's"
    assert "server.create_initialization_options(" in source, \
        "stdio builds its own options instead of deriving them from the " \
        "server both transports share"


def test_the_mcp_endpoint_answers_both_spellings_without_redirecting():
    """A bare mount answers `/mcp` with a 307 to `/mcp/`, and an HTTP client
    following a redirect is not required to resend the Authorization header or
    the POST body — a correctly configured client would arrive unauthenticated
    at a URL it never chose. Measured: both paths returned 401 without a token
    and 200 with one."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    assert '("/mcp", "/mcp/")' in source, \
        "only one spelling of the endpoint is served, so the other redirects"


def test_the_network_transport_adds_no_second_route_to_devices():
    """The panel's condition for allowing this at all.

    Exposing MCP over a port must not become a way around the policy engine or
    the audit chain. It doesn't, because the MCP server is a *client* of the
    hub: every tool goes out over the hub's REST API, which is the same path a
    `curl` takes. This test pins that — a tool that talked to the registry or
    the executor directly would bypass everything the hub exists to enforce.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in source.splitlines()
                     if l.strip() and not l.lstrip().startswith("#"))
    for bypass in ("from dosync.hub import", "from dosync.executor import",
                   "from ..hub import", "hub.registry", "AdapterExecutor("):
        assert bypass not in code, (
            f"the MCP server imports {bypass!r} — it must reach devices only "
            "through the hub's API, so policies and the audit chain apply to "
            "an agent exactly as they do to any other caller")


def test_the_token_is_compared_in_constant_time():
    """`!=` on strings stops at the first differing byte.

    That leaks the length of the matching prefix through response time, and on
    a LAN with repeated measurements it is enough to reconstruct a token byte by
    byte. Raised by the security reviewer on the panel that reviewed this
    transport: when the mitigation is one line of the standard library, whether
    the attack is practical stops being the interesting question.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    body = source[source.index("async def _serve_http"):]
    body = body[:body.index("\nasync def main")]
    code = "\n".join(l for l in body.splitlines()
                     if l.strip() and not l.lstrip().startswith("#"))

    assert "hmac.compare_digest" in code, \
        "the bearer token is not compared in constant time"
    assert "!= HUB_TOKEN" not in code, \
        "a short-circuiting comparison against the token is still present"


def test_idle_sessions_are_bounded():
    """A client that disconnects without closing leaves its session in memory.

    In a process that runs for weeks they accumulate, and an authenticated
    caller opening them in a loop is an exhaustion path. The SDK takes a
    timeout; not passing one means there is no bound at all.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    assert "session_idle_timeout" in source, \
        "sessions from disconnected clients are never released"


def test_the_network_transport_is_opt_in_and_local_by_default():
    """Opening a port should be a decision, not something that happens because
    someone installed the package. Both defaults here say so: the transport is
    `stdio` unless asked otherwise, and the host is loopback unless asked
    otherwise."""
    from pathlib import Path

    # Read as text rather than imported: the module exits when the MCP SDK is
    # absent, which it is in this environment, and every other test in this
    # file works the same way for the same reason.
    source = (Path(__file__).resolve().parent.parent
              / "dosync" / "mcp_server.py").read_text(encoding="utf-8")

    assert 'os.environ.get("DOSYNC_MCP_TRANSPORT", "stdio")' in source, \
        "the network transport is not opt-in, so installing opens a port"
    assert 'os.environ.get("DOSYNC_MCP_HOST", "127.0.0.1")' in source, \
        "the default host is not loopback, so the port is on the network " \
        "without anyone choosing that"
