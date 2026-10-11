"""DoSync conforms to SAMUEL, as samuel-certify measures it -- and samuel-certify
needs nothing of DoSync to run.

samuel-certify (dosync/samuel_certify.py) is the suite any hub runs against the
SAMUEL HTTP binding. This serves the reference hub on a local port, in
conformance mode, and requires every check to pass. CI also runs it against a
hub started the way an implementer would start one (.github/workflows).
"""
import ast
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent.parent / "dosync" / "samuel_certify.py"


def test_the_suite_needs_only_the_standard_library():
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    tree = ast.parse(SUITE.read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            used |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "samuel-certify must not import from DoSync"
            used.add((node.module or "").split(".")[0])
    assert used <= stdlib, sorted(used - stdlib)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def conformance_hub(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    import dosync.server as srv
    from dosync.executor import SimulatedExecutor
    from dosync.hub import DoSyncHub
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "conformance-operator")
    monkeypatch.setattr(srv, "_certify_mode", True)
    monkeypatch.setattr(srv, "hub", DoSyncHub(db_path=":memory:"))
    monkeypatch.setattr(srv, "executor", SimulatedExecutor(failure_rate=0.0))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(srv.app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def test_the_reference_hub_conforms_to_samuel(conformance_hub):
    from dosync.samuel_certify import Hub, run
    report = run(Hub(conformance_hub, "", "conformance-operator"))
    failed = [f"{c.id} {c.title}: {c.detail}" for c in report.checks if not c.passed]
    assert report.conforms, failed
    assert len(report.checks) >= 20
