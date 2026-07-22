"""get_event_loop() migration (2026-07-21).

asyncio.get_event_loop() is deprecated since 3.10 and scheduled to raise when no
loop is running. The risky site was hub.register_device: it fired an
`alert_anomaly` intent for a SECURITY condition (capabilities changed without a
firmware bump) inside a bare `except Exception: pass`. Under a future Python the
no-loop branch would have become unreachable and the security alert would have
vanished silently — the POL-2 failure mode. These tests pin that the alert fires
in BOTH contexts and that failures are reported, not swallowed.
"""
import asyncio
import logging

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, CertTier,
                           DeviceCategory, SensorSpec)


def _manifest(firmware="1.0", actuators=("turn_on",)):
    return CapabilityManifest(
        device_id="dev-1", device_name="Dev", manufacturer="t", model="t",
        firmware=firmware, category=DeviceCategory.ACTUATOR, tags=["light"],
        sensors=[SensorSpec("s", "boolean", "")], events=[],
        actuators=[ActuatorSpec(id=a, type=a, description="") for a in actuators],
        emergency_capable=False, cert_tier=CertTier.BASIC)


def _trigger_capability_anomaly(hub):
    """Register, then re-register with DIFFERENT capabilities but the SAME
    firmware version — the security condition that fires alert_anomaly."""
    hub.registry.register(_manifest(firmware="1.0", actuators=("turn_on",)))
    hub.register_device(_manifest(firmware="1.0", actuators=("turn_on", "unlock")))


def test_anomaly_alert_path_runs_without_a_loop(caplog):
    """Sync context (CLI, migrations, sync tests): no running loop. The alert
    must still be attempted — previously this branch would silently disappear."""
    hub = DoSyncHub(db_path=":memory:")
    with caplog.at_level(logging.DEBUG):
        _trigger_capability_anomaly(hub)      # must not raise
    anomalies = [e for e in hub.audit_log.entries()
                 if e.get("type") == "device_capability_anomaly"]
    assert len(anomalies) == 1, "the security anomaly itself must always be audited"


@pytest.mark.asyncio
async def test_anomaly_alert_path_runs_inside_a_loop():
    """Server context: a loop IS running, so the alert is scheduled without
    blocking registration."""
    hub = DoSyncHub(db_path=":memory:")
    _trigger_capability_anomaly(hub)          # must not raise
    await asyncio.sleep(0)                    # let the scheduled task start
    anomalies = [e for e in hub.audit_log.entries()
                 if e.get("type") == "device_capability_anomaly"]
    assert len(anomalies) == 1


class _NullExecutor:
    async def execute(self, action, urgency):
        raise AssertionError("not reached in these tests")


def test_registration_is_never_blocked_by_alert_failure(monkeypatch, caplog):
    """Best-effort stays best-effort: if executing the alert intent blows up,
    registration still completes — but the failure is LOGGED, not swallowed."""
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = _NullExecutor()

    async def _boom(*a, **k):
        raise RuntimeError("resolver exploded")
    monkeypatch.setattr(hub, "execute_intent", _boom)

    with caplog.at_level(logging.ERROR):
        _trigger_capability_anomaly(hub)      # must not raise

    # the device is still registered...
    assert hub.registry.get("dev-1") is not None
    # ...and the failure surfaced instead of vanishing
    assert any("FAILED to execute" in r.message or "FAILED to execute" in str(r.msg)
               for r in caplog.records), "alert failure must be reported, not swallowed"


def test_no_deprecated_get_event_loop_calls_remain():
    """Structural guard (AST, not grep): no CALL to get_event_loop anywhere in
    the package or the server. Comments mentioning it are fine."""
    import ast
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    files = list((repo / "dosync").rglob("*.py")) + [repo / "server.py"]
    offenders = []
    for f in files:
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_event_loop":
                offenders.append(f"{f.relative_to(repo)}:{node.lineno}")
    assert not offenders, f"deprecated get_event_loop() calls: {offenders}"


def test_missing_executor_is_reported_not_hidden(caplog):
    """The regression this migration uncovered: register_device called
    execute_intent WITHOUT its required executor, raising TypeError on every
    anomaly — swallowed by a bare except, so the security alert had never once
    fired. A hub with no executor wired must now SAY so."""
    hub = DoSyncHub(db_path=":memory:")
    assert hub.default_executor is None
    with caplog.at_level(logging.ERROR):
        _trigger_capability_anomaly(hub)
    assert any("NOT dispatched" in str(r.msg) or "NOT dispatched" in r.message
               for r in caplog.records), "a hub that cannot alert must say so"
    # and the anomaly itself is still audited regardless
    assert [e for e in hub.audit_log.entries()
            if e.get("type") == "device_capability_anomaly"]


@pytest.mark.asyncio
async def test_alert_actually_dispatches_when_executor_is_wired():
    """The positive case that was dead until 2026-07-21: with an executor wired,
    the anomaly alert intent is genuinely executed."""
    hub = DoSyncHub(db_path=":memory:")
    hub.default_executor = _NullExecutor()
    fired = []

    async def _capture(intent, executor, progress_cb=None):
        fired.append(intent)
        return None
    hub.execute_intent = _capture

    _trigger_capability_anomaly(hub)
    await asyncio.sleep(0)
    assert len(fired) == 1
    assert fired[0].intent.value == "alert_anomaly"
