"""Console entry points for an installed DoSync.

`pip install dosync` should give you a hub you can actually run — not a library
you still have to wire up. This module is the thin layer that makes that true.
"""
import argparse
import os
import sys


def hub(argv=None) -> None:
    """Run the DoSync hub (console script: `dosync-hub`).

    Deliberately thin: every real setting is already an environment variable
    read by the application itself, because a deployment configures a hub
    through its environment (systemd, Docker, compose), not through flags that
    would then exist in two places and disagree. The flags below are only the
    ones you need before the application starts — where to listen.
    """
    parser = argparse.ArgumentParser(
        prog="dosync-hub",
        description="Run the DoSync hub.",
        epilog=(
            "Configuration is by environment variable, e.g.:\n"
            "  DOSYNC_DB=/var/lib/dosync/dosync.db   database path\n"
            "  DOSYNC_AUTH=true                      require bearer auth\n"
            "  DOSYNC_TOKEN=<token>                  the bearer token\n"
            "  DOSYNC_POLICIES=/etc/dosync/policies.json   deployment policies\n"
            "  DOSYNC_CERTIFY=true                   certification mode (in-memory DB)\n"
            "\nSee the deployment guide for the full list."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("DOSYNC_HOST", "127.0.0.1"),
                        help="interface to bind (default: 127.0.0.1 — loopback. "
                             "Use 0.0.0.0 to accept connections from the network)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DOSYNC_PORT", "47200")),
                        help="port to listen on (default: 47200)")
    parser.add_argument("--reload", action="store_true",
                        help="auto-reload on code changes (development only)")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug", "trace"])
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:  # pragma: no cover - install-time problem, not runtime
        sys.exit("uvicorn is required to run the hub: pip install 'dosync'")

    # Publish the real values so the application can report them accurately: the
    # startup log used to announce a hardcoded 47200 no matter where the hub was
    # actually listening.
    os.environ["DOSYNC_PORT"] = str(args.port)
    os.environ["DOSYNC_HOST"] = args.host

    # Binding to loopback is the right default — a hub reachable from the whole
    # network because nobody chose that is worse than one that needs a flag. But
    # the commonest deployment is a headless Raspberry Pi whose operator is on
    # SSH and wants the dashboard from their laptop, and "Uvicorn running on
    # http://127.0.0.1" does not tell them why their browser cannot connect.
    #
    # Found by installing from PyPI on a clean machine and trying to open the
    # dashboard from another one. The default does not change; the silence does.
    if args.host in ("127.0.0.1", "localhost", "::1"):
        print()
        print("  Listening on loopback only — reachable from this machine at")
        print(f"      http://localhost:{args.port}")
        print("  To reach it from another machine on your network:")
        print(f"      dosync-hub --host 0.0.0.0 --port {args.port}")
        print("  (that exposes the hub to your local network; it keeps requiring")
        print("   a token, and TLS is a separate step — see setup_pki.sh)")
        print()

    uvicorn.run("dosync.server:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)


if __name__ == "__main__":  # pragma: no cover
    hub()
