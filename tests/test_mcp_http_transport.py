"""The MCP server's HTTP transport starts with the SDK the package declares.

The transport that lets an agent on another machine reach the hub was covered
only by tests that read mcp_server.py as text. It builds its session manager
with `session_idle_timeout`, a parameter the SDK gained in 1.27.0, while the
package declared `mcp>=1.0.0`: with any release before 1.27,
DOSYNC_MCP_TRANSPORT=http died at start with a TypeError, and with 1.0.0 the
server did not even import. This runs the real startup -- everything but the
socket -- so the declared floor is exercised, in CI, by the job that installs
the lowest versions the package allows.
"""
import asyncio
import sys
import types


def test_the_http_transport_builds_with_the_installed_sdk(monkeypatch):
    from dosync import mcp_server

    served = {}

    class _NoSocketServer:
        def __init__(self, config):
            served["app"] = config.app

        async def serve(self):
            served["started"] = True

    uvicorn_stub = types.SimpleNamespace(
        Config=lambda app, **kw: types.SimpleNamespace(app=app, **kw),
        Server=_NoSocketServer)
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn_stub)
    monkeypatch.setattr(mcp_server, "HUB_TOKEN", "test-token")

    asyncio.run(mcp_server._serve_http())
    assert served.get("started") and served.get("app") is not None, \
        "the HTTP transport did not get as far as serving"


def test_the_http_transport_refuses_to_start_without_a_token(monkeypatch):
    import pytest
    from dosync import mcp_server
    monkeypatch.setattr(mcp_server, "HUB_TOKEN", "")
    with pytest.raises(RuntimeError, match="needs DOSYNC_TOKEN"):
        asyncio.run(mcp_server._serve_http())


def test_every_place_that_states_the_sdk_range_states_the_same_one():
    """The range is written for four readers -- pip (the extra), a contributor
    (requirements-dev.txt), an operator installing by hand (the README) and one
    whose SDK is wrong (the server's error message). They cannot be one line,
    so they must agree: the message told operators to install `mcp>=1.0.0`, a
    range that includes versions this server cannot start on."""
    import re
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    extra = re.search(r'^mcp\s*=\s*\["mcp([^"]+)"\]', (repo / "pyproject.toml").read_text(), re.M).group(1)
    for rel in ("requirements-dev.txt", "README.md", "dosync/mcp_server.py"):
        ranges = set(re.findall(r"mcp(>=[0-9.]+,<[0-9.]+)", (repo / rel).read_text(encoding="utf-8")))
        assert ranges == {extra}, f"{rel} states {sorted(ranges)}, the [mcp] extra declares {extra}"
