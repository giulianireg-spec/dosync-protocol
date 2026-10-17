"""Every command's output survives a stream in a legacy code page (Windows,
redirected). dosync-mcp --check died with UnicodeEncodeError in the Windows CI
job on 2026-10-17."""
import io
import sys

import pytest


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_a_cp1252_stream_becomes_utf8(monkeypatch, stream_name):
    from dosync._console import utf8_stdio
    raw = io.BytesIO()
    legacy = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, stream_name, legacy)
    utf8_stdio()
    print("✓ MCP library installed", file=getattr(sys, stream_name))
    getattr(sys, stream_name).flush()
    assert raw.getvalue().decode("utf-8") == "✓ MCP library installed\n"


def test_dosync_mcp_check_with_a_legacy_stdout(monkeypatch, capsys):
    from dosync import mcp_cli
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    with pytest.raises(SystemExit):
        mcp_cli.main(["--check", "--hub-url", "http://127.0.0.1:9"])
    sys.stdout.flush()
    out = raw.getvalue().decode("utf-8")
    assert "Hub not reachable" in out
