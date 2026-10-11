"""A person can govern a hub without a terminal (panel U2-U5, 2026-10-11).

What SAMUEL reserves to a person -- releasing a held action, marking what must
wait, placing devices -- had endpoints and no page, so the guarantees existed
for developers only. These cover the pieces the dashboard stands on:

- GET /v1/confirmations lists every hold, so a console needs no intent ids;
- confirmation marks set from a console are stored by the hub, ADD to the
  deployment file, hold exactly like file policies, and need the operator
  credential ALWAYS (unmarking removes a restriction);
- GET /v1/governance and GET /v1/agent/connect answer what the pages show;
- `dosync-mcp` prints a configuration any MCP client can paste;
- certify mode does not pick up the machine's own policy file or .env.
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dosync.executor import SimulatedExecutor
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory, Intent,
                           IntentClass, Urgency)

REPO = Path(__file__).resolve().parent.parent
OP = {"X-DoSync-Operator-Token": "the-operator"}


def _dev(device_id, acts):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=[], sensors=[], events=[],
        actuators=[ActuatorSpec(a, a, a) for a in acts])


@pytest.fixture
def setup(monkeypatch):
    from fastapi.testclient import TestClient
    import dosync.server as srv
    from dosync.hub import DoSyncHub
    monkeypatch.setenv("DOSYNC_OPERATOR_TOKEN", "the-operator")
    monkeypatch.delenv("DOSYNC_CONFIGURATION_CREDENTIAL", raising=False)
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = SimulatedExecutor()
    hub.register_device(_dev("gate", ["open", "close"]))
    hub.register_device(_dev("lamp", ["turn_on", "turn_off"]))
    monkeypatch.setattr(srv, "hub", hub)
    with TestClient(srv.app) as c:
        srv.hub = hub
        yield c, hub


def _fire(hub, actions, iid):
    intent = Intent(intent=IntentClass("operate_device"), urgency=Urgency.INFO,
                    context={"proposed_actions": [{"device_id": d, "action": a} for d, a in actions]})
    intent.intent_id = iid
    return asyncio.run(hub.execute_intent(intent, SimulatedExecutor()))


def test_marks_need_the_operator_even_when_configuration_is_open(setup):
    client, hub = setup
    marks = {"marks": [{"actions": ["open"], "device_ids": ["gate"], "reason": "a person opens the gate"}]}
    r = client.put("/v1/governance/confirmation-marks", json=marks)
    assert r.status_code == 403 and "operator_credential_required" in r.text
    r = client.put("/v1/governance/confirmation-marks", json=marks,
                   headers={"X-DoSync-Operator-Token": "wrong"})
    assert r.status_code == 403
    r = client.put("/v1/governance/confirmation-marks", json=marks, headers=OP)
    assert r.status_code == 200, r.text
    assert r.json()["marks"][0]["actions"] == ["open"]
    # the agent's credential cannot unmark it either
    assert client.put("/v1/governance/confirmation-marks", json={"marks": []}).status_code == 403
    assert hub.confirmation_marks()[0]["device_ids"] == ["gate"]
    entry = [e for e in hub.audit_log.entries() if e.get("type") == "confirmation_marks_changed"]
    assert entry and entry[-1]["after"][0]["actions"] == ["open"]


def test_a_bad_mark_is_refused_whole(setup):
    client, hub = setup
    for bad in ([{"actions": []}], [{"actions": ["open"]}, {"device_ids": ["gate"]}],
                [{"actions": ["open"], "device_ids": "gate"}], "open"):
        r = client.put("/v1/governance/confirmation-marks", json={"marks": bad}, headers=OP)
        assert r.status_code == 422, (bad, r.text)
    assert hub.confirmation_marks() == []


def test_a_console_mark_holds_and_the_console_sees_it(setup):
    client, hub = setup
    client.put("/v1/governance/confirmation-marks", headers=OP,
               json={"marks": [{"actions": ["open"], "reason": "a person opens the gate"}]})
    assert client.get("/v1/confirmations").json()["pending"] == []
    r = _fire(hub, [("gate", "open"), ("lamp", "turn_on")], "hold-1")
    assert {(h["device_id"], h["action"]) for h in r.held_for_confirmation} == {("gate", "open")}
    assert ("lamp", "turn_on") in {(x.device_id, x.action) for x in r.results}
    listing = client.get("/v1/confirmations").json()
    assert listing["operator_configured"] is True
    [pend] = listing["pending"]
    assert pend["intent_id"] == "hold-1"
    assert pend["actions"] == [{"device_id": "gate", "action": "open", "reason": "a person opens the gate"}]
    # released the way the dashboard does it
    assert client.post("/v1/intents/hold-1/confirm", headers=OP).json()["status"] == "confirmed"
    assert client.get("/v1/confirmations").json()["pending"] == []


def test_governance_reports_both_sources(setup):
    client, hub = setup
    from dosync.policies import PolicyEngine, RequireConfirmationPolicy
    engine = PolicyEngine()
    engine.add(RequireConfirmationPolicy(["close"], device_ids=["gate"], reason="from the file"))
    hub.policy_engine = engine
    client.put("/v1/governance/confirmation-marks", headers=OP,
               json={"marks": [{"actions": ["turn_off"]}]})
    g = client.get("/v1/governance").json()
    assert g["configuration_credential"] == "hub" and g["operator_configured"] is True
    assert g["confirmation_marks"]["deployment_file"] == [
        {"actions": ["close"], "device_ids": ["gate"], "even_in_emergency": False, "reason": "from the file"}]
    assert g["confirmation_marks"]["hub"][0]["actions"] == ["turn_off"]
    # both hold: the file's and the console's
    r = _fire(hub, [("gate", "close"), ("lamp", "turn_off")], "hold-2")
    assert {(h["device_id"], h["action"]) for h in r.held_for_confirmation} == {
        ("gate", "close"), ("lamp", "turn_off")}


def test_agent_connect_gives_this_installations_command(setup):
    client, _ = setup
    info = client.get("/v1/agent/connect").json()
    assert info["command"] == sys.executable
    assert info["args"] == ["-m", "dosync.mcp_server"]
    # the page adds the token in the browser that already has it; the hub never echoes one
    assert "DOSYNC_TOKEN" not in json.dumps(info) and "env" not in info


def test_the_dashboard_offers_the_governance_pages():
    html = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("/v1/confirmations", "/v1/governance/confirmation-marks", "/v1/agent/connect",
                   "X-DoSync-Operator-Token", "openHolds()", "openGovernance()", "openAgent()"):
        assert needle in html, needle
    # the operator credential is kept for the tab only, never in localStorage
    assert "sessionStorage.setItem('dosync_operator'" in html
    assert "localStorage.setItem('dosync_operator'" not in html


def _mcp_cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "dosync.mcp_cli", *args], capture_output=True,
                          text=True, cwd=REPO, env={**os.environ, **(env or {})}, timeout=60)


def test_print_config_is_pasteable_and_uses_this_python():
    r = _mcp_cli("--print-config", "--hub-url", "https://hub.local:47200", "--token", "tok-123")
    assert r.returncode == 0, r.stderr
    entry = json.loads(r.stdout)["mcpServers"]["dosync"]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "dosync.mcp_server"]
    assert entry["env"] == {"DOSYNC_HUB_URL": "https://hub.local:47200", "DOSYNC_TOKEN": "tok-123"}
    # without a token it says so instead of inventing one
    r = _mcp_cli("--print-config", env={"DOSYNC_TOKEN": ""})
    assert "PASTE-YOUR-HUB-TOKEN-HERE" in r.stdout and "Replace" in r.stderr


def test_check_says_what_is_missing():
    r = _mcp_cli("--check", "--hub-url", "http://127.0.0.1:9")
    assert r.returncode == 1
    assert "not reachable" in r.stdout


def test_without_mcp_the_server_command_says_how_to_install(monkeypatch, capsys):
    from dosync import mcp_cli
    monkeypatch.setattr(mcp_cli, "mcp_installed", lambda: False)
    with pytest.raises(SystemExit) as e:
        mcp_cli.main([])
    assert "dosync[mcp]" in str(e.value) or "pipx inject" in str(e.value)


def test_certify_mode_ignores_a_policy_file_it_was_not_given(tmp_path):
    # A broken policies.json in the working directory stops a normal hub (a
    # policy must never fail quietly); a conformance hub must not read it at all.
    (tmp_path / "policies.json").write_text("{ not json")
    env = {k: v for k, v in os.environ.items() if k != "DOSYNC_POLICIES"}
    env.update({"DOSYNC_CERTIFY": "true", "PYTHONPATH": str(REPO)})
    code = "import dosync.server as s; print('policies', s.hub.policy_engine is not None, s.notifier)"
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "policies True None" in r.stdout
    assert "not loading" in r.stderr
