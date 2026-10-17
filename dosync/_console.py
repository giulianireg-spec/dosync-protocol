"""Console output that survives Windows.

Python writes to a terminal on Windows in UTF-8, but to a pipe or a file in the
legacy code page (cp1252 and the like), which cannot encode the ✓ and ✗ the
commands print. `dosync-mcp --check` then died with UnicodeEncodeError as soon
as its output was redirected -- `dosync-mcp --print-config > cfg.json`, which
the docs suggest, or any script or CI job that captures it. Found by the
Windows CI job on 2026-10-17.
"""
import sys


def utf8_stdio() -> None:
    """Write stdout and stderr as UTF-8 whatever the platform's default."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - detached/closed streams
                pass
