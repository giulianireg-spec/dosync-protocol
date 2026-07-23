"""Compatibility shim — the hub application now lives in `dosync/server.py`.

It moved so that it ships INSIDE the installed package: before this, `pip install
dosync` gave you the library but no runnable hub, because the application sat at
the repository root where packaging cannot reach it. Everything that referenced
the old location keeps working unchanged:

    uvicorn server:app          # systemd units, Dockerfile, docs
    import server               # the test suite

This is an ALIAS, not a re-export. `sys.modules[__name__] = _server` makes the
name `server` resolve to the very same module object as `dosync.server`, so
there is exactly one module: attributes set through either name (the suite does
`server.executor = ...`) are seen by both. A plain `from dosync.server import *`
would create a second module whose mutations the application would never see —
a silent divergence, which is precisely the failure class this project refuses.

New code should import `dosync.server` directly.
"""
import sys

from dosync import server as _server

sys.modules[__name__] = _server
