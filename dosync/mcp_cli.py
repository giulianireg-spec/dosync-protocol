"""`dosync-mcp`: connect the agent of your choice to a DoSync hub.

DoSync is the nervous system between an agent (the mind) and devices (the
body); the agent is whichever one the person already uses. Any MCP client can
run the DoSync MCP server, but it needs three things written into its
configuration: a command that starts the server, the hub's address, and the
hub token. Until this existed a person had to work out the first one —
`python -m dosync.mcp_server` with *which* python? inside a pipx venv they never
see — which is exactly the step that stops someone who is not a developer.

    dosync-mcp                   run the MCP server (what the agent starts)
    dosync-mcp --print-config    print the block to paste into the agent's config
    dosync-mcp --check           say what is missing, in words

Configuration is by environment variable, as for the hub: DOSYNC_HUB_URL,
DOSYNC_TOKEN, DOSYNC_CA_CERT, DOSYNC_MCP_TRANSPORT (stdio | http).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys

MCP_REQUIREMENT = "mcp>=1.27.0,<2.0"
TOKEN_PLACEHOLDER = "PASTE-YOUR-HUB-TOKEN-HERE"


def _install_hint() -> str:
    # pipx writes this file into every venv it manages; the path itself varies
    # (PIPX_HOME, ~/.local/pipx, ~/Library/Application Support/pipx...).
    in_pipx = (os.path.exists(os.path.join(sys.prefix, "pipx_metadata.json"))
               or "pipx" in sys.prefix.replace("\\", "/").split("/"))
    if in_pipx:
        return f'pipx inject dosync "{MCP_REQUIREMENT}"'
    return f'{os.path.basename(sys.executable)} -m pip install "dosync[mcp]"'


def mcp_installed() -> bool:
    return importlib.util.find_spec("mcp") is not None


def client_config_paths() -> dict:
    """Where common desktop agents keep their MCP configuration, for this OS.
    Informational: every client documents its own, and new ones appear."""
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Darwin":
        claude = os.path.join(home, "Library", "Application Support", "Claude",
                              "claude_desktop_config.json")
    elif system == "Windows":
        claude = os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")),
                              "Claude", "claude_desktop_config.json")
    else:
        claude = os.path.join(home, ".config", "Claude", "claude_desktop_config.json")
    return {"Claude Desktop": claude,
            "Cursor": os.path.join(home, ".cursor", "mcp.json")}


def server_entry(hub_url: str, token: str | None, ca_cert: str | None) -> dict:
    """The `mcpServers.dosync` entry. The command is this interpreter, not a
    name on PATH: an agent started from a desktop launcher often does not see
    the PATH a terminal sees, and a pipx venv's python is never on it."""
    env = {"DOSYNC_HUB_URL": hub_url, "DOSYNC_TOKEN": token or TOKEN_PLACEHOLDER}
    if ca_cert:
        env["DOSYNC_CA_CERT"] = ca_cert
    return {"command": sys.executable, "args": ["-m", "dosync.mcp_server"], "env": env}


def print_config(args) -> int:
    hub_url = args.hub_url or os.environ.get("DOSYNC_HUB_URL", "http://localhost:47200")
    token = args.token if args.token is not None else os.environ.get("DOSYNC_TOKEN")
    ca = args.ca or os.environ.get("DOSYNC_CA_CERT")
    block = {"mcpServers": {"dosync": server_entry(hub_url, token, ca)}}
    print(json.dumps(block, indent=2))
    out = sys.stderr
    print("", file=out)
    print("Paste the block above into your agent's MCP configuration (merge it into", file=out)
    print("\"mcpServers\" if the file already has one), then restart the agent.", file=out)
    for name, where in client_config_paths().items():
        print(f"  {name}: {where}", file=out)
    entry = block["mcpServers"]["dosync"]
    env_flags = " ".join(f"-e {k}={v}" for k, v in entry["env"].items())
    print(f"  Command-line agents (e.g. Claude Code):\n    claude mcp add dosync {env_flags} -- "
          f"{entry['command']} {' '.join(entry['args'])}", file=out)
    if not token:
        print(f"\nReplace {TOKEN_PLACEHOLDER} with your hub token (the one the dashboard uses).", file=out)
    else:
        print("\nThe block contains your hub token: keep it as private as a password.", file=out)
    if not mcp_installed():
        print(f"\nThe MCP library is not installed here yet. Run first:\n  {_install_hint()}", file=out)
    return 0


def check(args) -> int:
    hub_url = args.hub_url or os.environ.get("DOSYNC_HUB_URL", "http://localhost:47200")
    token = args.token if args.token is not None else os.environ.get("DOSYNC_TOKEN", "")
    ok = True
    if mcp_installed():
        print("✓ MCP library installed")
    else:
        ok = False
        print(f"✗ MCP library missing — run: {_install_hint()}")
    import ssl
    import urllib.error
    import urllib.request
    req = urllib.request.Request(hub_url.rstrip("/") + "/v1/status",
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    ctx = None
    ca = args.ca or os.environ.get("DOSYNC_CA_CERT")
    if hub_url.startswith("https") and ca:
        ctx = ssl.create_default_context(cafile=ca)
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            status = json.loads(r.read() or b"{}")
        print(f"✓ Hub reachable at {hub_url} (version {status.get('version', '?')}, "
              f"{status.get('devices', '?')} device(s))")
    except urllib.error.HTTPError as e:
        ok = False
        if e.code == 401:
            print(f"✗ Hub at {hub_url} answered, but refused the token "
                  f"({'none given' if not token else 'wrong token'}). Set DOSYNC_TOKEN or pass --token.")
        else:
            print(f"✗ Hub at {hub_url} answered {e.code}.")
    except Exception as e:  # unreachable, DNS, TLS
        ok = False
        print(f"✗ Hub not reachable at {hub_url}: {e}. Is `dosync-hub` running, "
              "and is this the right address?")
    return 0 if ok else 1


def main(argv=None) -> None:
    from dosync._console import utf8_stdio
    utf8_stdio()
    p = argparse.ArgumentParser(
        prog="dosync-mcp",
        description="Run the DoSync MCP server, or print the configuration your agent needs.")
    p.add_argument("--print-config", action="store_true",
                   help="print the MCP configuration block for your agent and exit")
    p.add_argument("--check", action="store_true",
                   help="check that the MCP library is installed and the hub answers")
    p.add_argument("--hub-url", help="hub address (default: $DOSYNC_HUB_URL or http://localhost:47200)")
    p.add_argument("--token", help="hub token to write into the configuration (default: $DOSYNC_TOKEN)")
    p.add_argument("--ca", help="CA certificate for a hub served over TLS (default: $DOSYNC_CA_CERT)")
    args = p.parse_args(argv)

    if args.print_config:
        sys.exit(print_config(args))
    if args.check:
        sys.exit(check(args))

    if not mcp_installed():
        sys.exit("The MCP library is not installed, so the DoSync MCP server cannot start.\n"
                 f"Install it with:\n  {_install_hint()}")
    if args.hub_url:
        os.environ["DOSYNC_HUB_URL"] = args.hub_url
    if args.token is not None:
        os.environ["DOSYNC_TOKEN"] = args.token
    if args.ca:
        os.environ["DOSYNC_CA_CERT"] = args.ca
    import asyncio
    from dosync import mcp_server  # reads the environment at import
    asyncio.run(mcp_server.main())


if __name__ == "__main__":
    main()
