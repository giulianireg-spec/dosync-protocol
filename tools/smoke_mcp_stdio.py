"""Start the DoSync MCP server exactly as an agent would, and use it.

Reads the block `dosync-mcp --print-config` printed (a file, or stdin), starts
the command it names over stdio -- the way Claude Desktop, Cursor or any MCP
client does -- lists the tools and calls `dosync_get_status` against the hub.
Exit 0 only if the agent side works end to end. Used by CI (job `pipx-path`)
to protect the path a non-developer follows from the README.

    dosync-mcp --print-config --hub-url http://127.0.0.1:47200 > cfg.json
    python tools/smoke_mcp_stdio.py cfg.json
"""
import asyncio
import json
import os
import sys

# The hub's status text carries ✓; on Windows a captured stdout is cp1252.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


async def main(cfg: dict) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    entry = cfg["mcpServers"]["dosync"]
    params = StdioServerParameters(command=entry["command"], args=entry["args"],
                                   env={**os.environ, **entry["env"]})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print(f"{len(tools)} tools:", ", ".join(sorted(tools)))
            for needed in ("dosync_get_status", "dosync_fire_intent", "dosync_list_devices"):
                assert needed in tools, f"the agent would not see {needed}"
            res = await session.call_tool("dosync_get_status", {})
            text = "".join(getattr(c, "text", "") for c in res.content)
            assert not res.isError, text
            assert "version" in text.lower() or "devices" in text.lower(), text[:400]
            print("dosync_get_status answered:", text[:200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raw = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    sys.exit(asyncio.run(main(json.loads(raw))))
