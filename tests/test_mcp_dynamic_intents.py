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
