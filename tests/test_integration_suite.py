"""Smoke test for the integration suite (C2): it must run, classify outcomes,
and stay distinct from conformance (certify.py)."""
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _serve(port, ready):
    import os
    os.environ["DOSYNC_CERTIFY"] = "1"
    os.environ["DOSYNC_AUTH"] = "false"
    import uvicorn, server
    ready.set()
    uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="error")


def test_integration_runs_and_classifies(tmp_path):
    port = 47261
    ready = threading.Event()
    t = threading.Thread(target=_serve, args=(port, ready), daemon=True)
    t.start()
    ready.wait(timeout=5)
    time.sleep(2.5)

    base = f"http://127.0.0.1:{port}"
    # register one emergency-capable actuator so I01 can execute
    import urllib.request, json
    req = urllib.request.Request(
        base + "/v1/devices/register",
        data=json.dumps({
            "device_id": "int-suite-01", "device_name": "S", "manufacturer": "t",
            "model": "t", "firmware": "1", "category": "actuator",
            "tags": ["light", "emergency"],
            "actuators": [{"id": "p", "type": "turn_on", "description": "on"}],
            "emergency_capable": True, "cert_tier": "emergency",
        }).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=5)

    out_json = tmp_path / "integ.json"
    r = subprocess.run(
        [sys.executable, "integration.py", "--host", "127.0.0.1",
         "--port", str(port), "--json", str(out_json)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT a protocol conformance run" in r.stdout
    assert out_json.exists()
    report = json.loads(out_json.read_text())
    assert report["kind"] == "physical-execution"       # distinct from a cert
    assert "dosync_integration_version" in report
    # I01 (ensure_safety on a registered emergency actuator) should have executed
    i01 = next(x for x in report["results"] if x["name"].startswith("I01"))
    assert i01["outcome"] in ("executed", "partial"), i01
    # audit check always structural
    i04 = next(x for x in report["results"] if x["name"].startswith("I04"))
    assert i04["outcome"] == "executed"
