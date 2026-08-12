"""Deployment policy loader (POL-1).

The panel (2026-07-12) ruled that device preferences are deployment configuration,
not protocol and not reference-hub code. Until POL-1 that was theory: server.py
hard-coded one house's choices (no unlock 00:00-06:00, confirm alarms) into the
implementation everyone runs, and changing them meant editing the hub.

The loader's most important property is that it FAILS LOUDLY. A policy is usually a
restriction; a typo that silently skips one leaves the operator believing they are
protected when they are not. Every test below that asserts `raises` is asserting
that silence is not an option.
"""
import json

import pytest

from dosync.policies import (NeverAfterHoursPolicy, PolicyEngine,
                             RequireConfirmationPolicy)
from dosync.policy_config import (PolicyConfigError, configured_path,
                                  load_into, load_policies)


def _write(tmp_path, doc, name="policies.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


# ── Happy path ───────────────────────────────────────────────────────────────

def test_loads_declared_policies(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "never_after_hours", "actuator_types": ["unlock"],
         "blocked_hours_start": 0, "blocked_hours_end": 6, "reason": "night"},
        {"type": "require_confirmation", "actuator_types": ["alarm"]},
    ]})
    policies = load_policies(p)
    assert len(policies) == 2
    assert isinstance(policies[0], NeverAfterHoursPolicy)
    assert isinstance(policies[1], RequireConfirmationPolicy)


def test_empty_policy_list_is_legitimate(tmp_path):
    """Declaring no policies is a valid deployment, not an error."""
    p = _write(tmp_path, {"version": 1, "policies": []})
    assert load_policies(p) == []


def test_metadata_keys_are_ignored(tmp_path):
    """JSON has no comments: '_'-prefixed keys document the file for humans.
    A restriction whose reason nobody recorded is one nobody dares remove."""
    p = _write(tmp_path, {
        "_README": "ignore me",
        "version": 1,
        "policies": [{"_why": "documented reason", "_owner": "ops",
                      "type": "require_confirmation", "actuator_types": ["alarm"]}],
    })
    policies = load_policies(p)
    assert len(policies) == 1


def test_load_into_registers_on_the_engine(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "require_confirmation", "actuator_types": ["alarm"]}]})
    engine = PolicyEngine()
    before = len(engine._policies)
    load_into(engine, p)
    assert len(engine._policies) == before + 1


def test_hub_is_injected_where_needed(tmp_path):
    from dosync.hub import DoSyncHub
    hub = DoSyncHub(db_path=":memory:")
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "manual_control_active"}]})
    policies = load_policies(p, hub=hub)
    assert len(policies) == 1


# ── Fail loudly — the whole point ────────────────────────────────────────────

def test_unknown_type_raises_never_skips(tmp_path):
    """THE critical test. A typo in a policy type must stop the hub, not silently
    drop the protection the operator asked for."""
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "never_after_hourz", "actuator_types": ["unlock"]}]})   # typo
    with pytest.raises(PolicyConfigError) as e:
        load_policies(p)
    assert "unknown policy type" in str(e.value)
    assert "never_after_hourz" in str(e.value)
    assert "never_after_hours" in str(e.value)      # suggests the known types


def test_missing_file_raises(tmp_path):
    """A configured-but-absent policy file must not degrade into 'no policies'."""
    with pytest.raises(PolicyConfigError) as e:
        load_policies(tmp_path / "does-not-exist.json")
    assert "not found" in str(e.value)


def test_bad_arguments_name_the_offending_entry(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "require_confirmation", "actuator_types": ["alarm"]},
        {"type": "never_after_hours", "actuator_types": ["unlock"]},   # missing hours
    ]})
    with pytest.raises(PolicyConfigError) as e:
        load_policies(p)
    assert "policies[1]" in str(e.value), "must point at the entry that is wrong"
    assert "never_after_hours" in str(e.value)


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json")
    with pytest.raises(PolicyConfigError) as e:
        load_policies(p)
    assert "invalid JSON" in str(e.value)


def test_wrong_version_raises(tmp_path):
    p = _write(tmp_path, {"version": 99, "policies": []})
    with pytest.raises(PolicyConfigError):
        load_policies(p)


def test_missing_policies_field_raises(tmp_path):
    p = _write(tmp_path, {"version": 1})
    with pytest.raises(PolicyConfigError) as e:
        load_policies(p)
    assert "policies" in str(e.value)


def test_policies_must_be_a_list(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": {"type": "x"}})
    with pytest.raises(PolicyConfigError):
        load_policies(p)


def test_entry_without_type_raises(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": [{"actuator_types": ["alarm"]}]})
    with pytest.raises(PolicyConfigError) as e:
        load_policies(p)
    assert "type" in str(e.value)


# ── The layering invariant ───────────────────────────────────────────────────

def test_server_does_not_hardcode_deployment_policies():
    """server.py must construct only INFRASTRUCTURE policies. Deployment
    preferences (hours, device ids, perimeters) belong to the deployer's file —
    the whole point of POL-1. Checked with the AST: a substring search would match
    the explanatory comments that describe what was removed."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "server.py").read_text()
    constructed = {
        node.func.id
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    deployment_policies = {
        "NeverAfterHoursPolicy", "RequireConfirmationPolicy",
        "DeviceExclusionPolicy", "BlockIntentPolicy", "GeofencePolicy",
    }
    leaked = constructed & deployment_policies
    assert not leaked, (
        f"server.py hard-codes deployment policies: {sorted(leaked)}. These carry a "
        "specific deployment's values and belong in its DOSYNC_POLICIES file, not in "
        "the reference hub every other deployment runs.")


def test_shipped_example_file_actually_loads():
    """The example must be usable as-is: it is what a deployer copies."""
    import pathlib
    example = pathlib.Path(__file__).resolve().parent.parent / "examples" / "policies.deployment.json"
    assert example.exists(), "the documented example policy file is missing"
    policies = load_policies(example)
    assert policies, "the example declares no policies"
    names = {p.name for p in policies}
    # the two that used to be hard-coded must still be available to whoever wants them
    assert "never_after_hours" in names
    assert "require_confirmation" in names


def test_configured_path_reads_env(monkeypatch, tmp_path):
    # config_dirs() cascades to /etc/dosync, so "no policies configured" is only
    # true on a machine that is not configured. Asserting it without isolating
    # the cascade made this test pass on a development laptop and fail on the
    # reference deployment, where /etc/dosync/policies.json legitimately exists.
    from dosync import paths
    monkeypatch.setattr(paths, "config_dirs", lambda: [tmp_path / "dosync"])
    monkeypatch.delenv("DOSYNC_POLICIES", raising=False)
    assert configured_path() is None      # no policies is a legitimate state
    monkeypatch.setenv("DOSYNC_POLICIES", "/etc/dosync/policies.json")
    assert configured_path() == "/etc/dosync/policies.json"


# ── Integration: the loader's fail-loudly must survive server.py ─────────────

def _run_server_import(tmp_path, policies_path):
    """Import server in a subprocess (module caching makes in-process useless)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "DOSYNC_DB": ":memory:", "DOSYNC_AUTH": "false",
           "PYTHONPATH": str(repo), "DOSYNC_POLICIES": str(policies_path)}
    return subprocess.run([sys.executable, "-c", "import server"],
                          cwd=str(repo), capture_output=True, text=True, env=env)


def test_broken_policy_file_stops_the_hub(tmp_path):
    """The regression that unit tests could not see (2026-07-14).

    The loader raised correctly, but server.py wrapped policy setup in a bare
    `except Exception` that logged a warning and carried on — so a typo'd policy
    file produced a RUNNING hub with the operator's restrictions silently absent.
    The loader's tests all passed while the real thing was unprotected. A policy
    file that cannot be honored must stop the hub.
    """
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "never_after_hourz", "actuator_types": ["unlock"]}]})   # typo
    r = _run_server_import(tmp_path, p)
    assert r.returncode != 0, (
        "the hub started despite a broken policy file — it is running without the "
        "restrictions its operator declared")
    assert "unknown policy type" in (r.stdout + r.stderr)


def test_missing_policy_file_stops_the_hub(tmp_path):
    r = _run_server_import(tmp_path, tmp_path / "absent.json")
    assert r.returncode != 0, "configured policy file absent: must not start unprotected"


def test_valid_policy_file_starts_the_hub(tmp_path):
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "require_confirmation", "actuator_types": ["alarm"]}]})
    r = _run_server_import(tmp_path, p)
    assert r.returncode == 0, r.stdout + r.stderr

def _hub_wiring_probe(tmp_path, policies_path=None):
    """Run `import server` in a subprocess and report on server.HUB.policy_engine —
    the object the hub actually consults, NOT the module-level `policy_engine`."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    env = {**os.environ, "DOSYNC_DB": ":memory:", "DOSYNC_AUTH": "false",
           "PYTHONPATH": str(repo)}
    env.pop("DOSYNC_POLICIES", None)
    if policies_path is not None:
        env["DOSYNC_POLICIES"] = str(policies_path)
    code = (
        "import server, sys\n"
        "pe = server.hub.policy_engine\n"
        "sys.exit(3 if pe is None else 0) if True else None\n"
        "\n"
    )
    code = (
        "import server, sys\n"
        "pe = server.hub.policy_engine\n"
        "if pe is None: sys.exit(3)\n"
        "print('POLICIES=' + ','.join(sorted(p.name for p in pe._policies)))\n"
    )
    return subprocess.run([sys.executable, "-c", code],
                          cwd=str(repo), capture_output=True, text=True, env=env)


def test_hub_policy_engine_is_actually_wired_with_deployment_file(tmp_path):
    """The 2026-07-15 production incident, pinned on the RIGHT object.

    A NameError inside the policy setup block (a log call before `log` existed)
    was swallowed by the then-generic `except Exception` — right before the
    `hub.policy_engine = policy_engine` line. The engine existed, all seven
    policies registered and logged... and the hub never attached to it:
    production ran with hub.policy_engine=None and an emergency intent drove
    devices the operator had absolutely excluded.

    The validation at the time checked `server.policy_engine` — the module-level
    variable, which looked perfect — instead of `server.hub.policy_engine`, the
    object execute_intent consults. This test asserts on the hub's."""
    p = _write(tmp_path, {"version": 1, "policies": [
        {"type": "device_exclusion", "intent_classes": ["ensure_safety"],
         "excluded_device_ids": ["tv-x"], "bypass_on_emergency": False}]})
    r = _hub_wiring_probe(tmp_path, p)
    assert r.returncode == 0, (
        "hub.policy_engine is None — the hub is not attached to its policy engine\n"
        + r.stdout + r.stderr)
    assert "device_exclusion" in r.stdout, r.stdout


def test_hub_policy_engine_is_wired_even_without_deployment_file(tmp_path):
    """No DOSYNC_POLICIES is a legitimate state — but the INFRASTRUCTURE policies
    (rate limits, conflict resolution) must still reach the hub. The incident also
    silently dropped these."""
    r = _hub_wiring_probe(tmp_path, policies_path=None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "intent_rate_limit" in r.stdout, r.stdout

