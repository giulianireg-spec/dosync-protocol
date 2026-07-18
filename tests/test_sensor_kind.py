"""SENSOR-KIND (panel 2026-07-14, shipped 2026-07-17).

A DHT measuring the room and a lamp reporting its own brightness both "sense",
but "read the environment" and "read every device's self-state" are different
questions. SensorSpec.kind distinguishes them WITHOUT hiding anything (a lamp's
brightness stays truthfully declared — hiding it would be the TV mistake), and
the deployment decides which question a bare status query means.

Found by the benchmark: report_status resolved 28 devices against 14 expected,
precision 0.5 — every WiZ bulb swept into "what's the status?" because it
declares brightness/state. The 14 unexpected were, to the last one, device-state
readers.
"""
import os

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                           Intent, IntentClass, SensorSpec, Urgency)


# ── The field itself ─────────────────────────────────────────────────────────

def test_kind_defaults_to_environment():
    """Every existing manifest stays byte-for-byte valid."""
    s = SensorSpec("temp", "temperature", "Temp")
    assert s.kind == "environment"


def test_kind_serializes_and_restores():
    """Round-trip through to_dict (which uses __dict__) and the hub's restore
    path. Legacy persisted manifests have no 'kind' key and must default."""
    m = CapabilityManifest(
        device_id="d1", device_name="D", manufacturer="t", model="t", firmware="1",
        category=DeviceCategory.ACTUATOR, tags=["light"], actuators=[], events=[],
        sensors=[SensorSpec("brightness", "integer", "B", kind="device_state")],
        emergency_capable=False, cert_tier=CertTier.BASIC)
    d = m.to_dict()
    assert d["capabilities"]["sensors"][0]["kind"] == "device_state"

    legacy = {"id": "temp", "type": "temperature"}      # pre-kind manifest
    restored = SensorSpec(
        id=legacy["id"], type=legacy["type"],
        kind=legacy.get("kind", "environment"))
    assert restored.kind == "environment"


# ── The F4a scope ────────────────────────────────────────────────────────────

def _hub():
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id="dht-01", device_name="DHT", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.SENSOR, tags=["sensor"],
        actuators=[], events=[],
        sensors=[SensorSpec("temp", "temperature", "Temp")],
        emergency_capable=False, cert_tier="basic"))
    hub.registry.register(CapabilityManifest(
        device_id="wiz-01", device_name="WiZ", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"],
        actuators=[], events=[],
        sensors=[SensorSpec("brightness", "integer", "B", kind="device_state"),
                 SensorSpec("state", "boolean", "S", kind="device_state")],
        emergency_capable=False, cert_tier="basic"))
    hub.registry.register(CapabilityManifest(
        device_id="thermo-01", device_name="T", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.HYBRID, tags=["climate"],
        actuators=[], events=[],
        sensors=[SensorSpec("current_temp", "temperature", "amb"),
                 SensorSpec("target_temp", "temperature", "set",
                            kind="device_state")],
        emergency_capable=False, cert_tier="basic"))
    return hub


def _plan(hub, ctx, env=None, monkeypatch=None):
    if monkeypatch is not None:
        if env is None:
            monkeypatch.delenv("DOSYNC_STATUS_SCOPE", raising=False)
        else:
            monkeypatch.setenv("DOSYNC_STATUS_SCOPE", env)
    intent = Intent(intent_id="t", intent=IntentClass("report_status"),
                    urgency=Urgency.INFO, context=ctx)
    plan = hub.resolver.resolve(intent)
    return {a.device_id: a.params["sensor_ids"] for a in plan.actions}


def test_no_scope_reads_everything_backward_compatible(monkeypatch):
    """A deployment that has expressed no preference sees today's behavior:
    the protocol gained a distinction, not an opinion."""
    got = _plan(_hub(), {}, env=None, monkeypatch=monkeypatch)
    assert got == {"dht-01": ["temp"],
                   "wiz-01": ["brightness", "state"],
                   "thermo-01": ["current_temp", "target_temp"]}


def test_environment_scope_filters_per_sensor(monkeypatch):
    """THE test: scope=environment drops device_state-only devices from the plan
    entirely, and — the per-sensor grain — keeps the thermostat but reads ONLY
    current_temp (measures the room), filtering the setpoint."""
    got = _plan(_hub(), {"scope": "environment"}, env=None, monkeypatch=monkeypatch)
    assert got == {"dht-01": ["temp"], "thermo-01": ["current_temp"]}
    assert "wiz-01" not in got


def test_env_var_sets_deployment_default(monkeypatch):
    got = _plan(_hub(), {}, env="environment", monkeypatch=monkeypatch)
    assert "wiz-01" not in got


def test_context_overrides_deployment_default(monkeypatch):
    """Per-query context wins over the deployment default, in both directions."""
    got = _plan(_hub(), {"scope": "all"}, env="environment", monkeypatch=monkeypatch)
    assert "wiz-01" in got


def test_invalid_scope_warns_and_falls_back(monkeypatch):
    """A status query is read-only and harmless; refusing it over a typo'd
    preference would be disproportionate. Warn and use the deployment default."""
    got = _plan(_hub(), {"scope": "everythingg"}, env=None, monkeypatch=monkeypatch)
    assert "wiz-01" in got      # fell back to "all"
    got = _plan(_hub(), {"scope": "everythingg"}, env="environment",
                monkeypatch=monkeypatch)
    assert "wiz-01" not in got  # fell back to the deployment default


# ── Adapters declare the truth ───────────────────────────────────────────────

def test_wiz_declares_device_state():
    from dosync.adapters.wiz import wiz_manifest
    m = wiz_manifest("wiz-test-01", "Test", "192.0.2.1")
    kinds = {s.id: s.kind for s in m.sensors}
    assert kinds == {"brightness": "device_state", "state": "device_state"}


def test_ha_domain_map_kinds():
    """The map's kinds match what each sensor MEASURES, not what device owns it:
    a thermostat's current_temp is environment (it measures the room) while its
    target_temp is device_state (a setpoint). sensor/binary_sensor stay
    environment via the default."""
    from dosync.adapters.homeassistant import HA_DOMAIN_MAP
    def kinds(domain):
        return {s.id: s.kind for s in HA_DOMAIN_MAP[domain]["sensors"]}

    assert kinds("light") == {"state": "device_state", "brightness": "device_state"}
    assert kinds("switch") == {"state": "device_state"}
    assert kinds("media_player") == {"state": "device_state"}
    assert kinds("climate") == {"current_temp": "environment",
                                "target_temp": "device_state"}
    assert kinds("sensor") == {"value": "environment"}
    assert kinds("binary_sensor") == {"state": "environment"}


# ── The data migration (manage.py db migrate-sensor-kind) ────────────────────

def _legacy(device_id, adapter, manufacturer, sensors, **kw):
    return {"device_id": device_id, "device_name": device_id, "manufacturer": manufacturer,
            "model": "m", "firmware": "1", "category": kw.get("category", "actuator"),
            "tags": kw.get("tags", []), "emergency_capable": False, "cert_tier": "basic",
            "adapter": adapter, "adapter_config": kw.get("adapter_config", {}),
            "capabilities": {"sensors": sensors, "actuators": [], "events": []}}


def test_migration_patches_come_from_the_adapters():
    """The kind rules are the adapters' declarations, not a duplicated table.
    WiZ brightness/state -> device_state; HA per HA_DOMAIN_MAP (climate keeps
    current_temp as environment — it measures the room); unknown devices and
    already-correct sensors untouched."""
    from manage import _sensor_kind_patches

    wiz = _legacy("wiz-cocina-01", "wiz", "Philips WiZ",
                  [{"id": "brightness", "type": "integer"},
                   {"id": "state", "type": "boolean"}])
    assert sorted(_sensor_kind_patches(wiz)) == [
        ("brightness", "environment", "device_state"),
        ("state", "environment", "device_state")]

    ha_climate = _legacy("ha-climate-living", "homeassistant", "HA",
                         [{"id": "current_temp", "type": "temperature"},
                          {"id": "target_temp", "type": "temperature"}])
    assert _sensor_kind_patches(ha_climate) == [
        ("target_temp", "environment", "device_state")]     # current_temp stays

    ha_binary = _legacy("ha-binary_sensor-x", "homeassistant", "HA",
                        [{"id": "state", "type": "boolean"}], category="sensor")
    assert _sensor_kind_patches(ha_binary) == []            # environment already

    dht = _legacy("rpi-dht22-01", "gpio", "custom",
                  [{"id": "temperature", "type": "temperature"}], category="sensor")
    assert _sensor_kind_patches(dht) == []                  # not ours to touch


def test_migration_end_to_end_only_adds_kind(tmp_path):
    """Apply against a real DB: kinds land, everything else — adapter_config
    with the lamp IP above all (the API path strips it, which is WHY this is a
    data migration) — survives byte-for-byte. And it is idempotent."""
    import copy
    import subprocess
    import sys
    from pathlib import Path

    from dosync.db import DoSyncDB

    repo = Path(__file__).resolve().parent.parent
    dbp = tmp_path / "mig.db"
    db = DoSyncDB(str(dbp)); db.init()
    wiz = _legacy("wiz-cocina-01", "wiz", "Philips WiZ",
                  [{"id": "brightness", "type": "integer", "unit": "%"},
                   {"id": "state", "type": "boolean"}],
                  tags=["light", "cocina", "emergency"],
                  adapter_config={"ip": "192.168.100.12", "port": 38899})
    db.save_device(wiz["device_id"], copy.deepcopy(wiz))

    r = subprocess.run([sys.executable, "manage.py", "--db", str(dbp),
                        "db", "migrate-sensor-kind", "--apply"],
                       cwd=str(repo), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    db2 = DoSyncDB(str(dbp)); db2.init()
    m = db2.load_devices()[0]
    kinds = {s["id"]: s["kind"] for s in m["capabilities"]["sensors"]}
    assert kinds == {"brightness": "device_state", "state": "device_state"}
    assert m["adapter_config"] == {"ip": "192.168.100.12", "port": 38899}
    assert m["tags"] == ["light", "cocina", "emergency"]
    assert m["capabilities"]["sensors"][0]["unit"] == "%"

    r2 = subprocess.run([sys.executable, "manage.py", "--db", str(dbp),
                         "db", "migrate-sensor-kind"],
                        cwd=str(repo), capture_output=True, text=True)
    assert "Nothing to do" in r2.stdout, "the migration must be idempotent"
