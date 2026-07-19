"""HA-BRIDGE-HYGIENE (2026-07-19) — housekeeping entities are not devices.

Home Assistant's own internals (sun times, backup status) surface as sensor.*
entities and used to be imported as DoSync "devices" — benchmark cause #3, the
largest remaining precision gap, and standard behavior every HA deployment
hits. Skipped by default; a deployment that genuinely wants them opts in.
"""
from dosync.adapters.homeassistant import HA_HOUSEKEEPING_PREFIXES, HABridge
from dosync.hub import DoSyncHub


def _bridge(**kw):
    hub = DoSyncHub(db_path=":memory:")
    return HABridge("http://x", "token", hub, **kw)


def _probe(bridge, entity_id):
    return bridge._state_to_manifest(
        {"entity_id": entity_id, "attributes": {"friendly_name": entity_id},
         "state": "x"})


def test_housekeeping_skipped_by_default():
    b = _bridge()
    for eid in ("sensor.sun_next_dawn", "sensor.sun_next_setting",
                "sensor.backup_backup_manager_state",
                "sensor.backup_next_scheduled_automatic_backup"):
        assert _probe(b, eid) is None, f"{eid} must not become a device"


def test_real_sensors_still_import():
    """The trailing underscore in the prefixes is load-bearing: a real sensor
    named 'sunroom_temperature' must NOT be caught by 'sun_'."""
    b = _bridge()
    for eid in ("sensor.sunroom_temperature",
                "binary_sensor.tv_philips_recording_ongoing",
                "light.living_lamp"):
        assert _probe(b, eid) is not None, f"{eid} is a real device"


def test_opt_in_imports_housekeeping():
    """A deployment that wants them says so — the bridge has a default, not an
    opinion it enforces."""
    b = _bridge(import_housekeeping=True)
    assert _probe(b, "sensor.sun_next_dawn") is not None


def test_env_opt_in(monkeypatch):
    monkeypatch.setenv("DOSYNC_HA_IMPORT_HOUSEKEEPING", "true")
    b = _bridge()   # no explicit param → reads env
    assert _probe(b, "sensor.sun_next_dawn") is not None


def test_deployment_declared_exclusions():
    b = _bridge(exclude_prefixes=["weather_"])
    assert _probe(b, "sensor.weather_humidity") is None
    assert _probe(b, "sensor.sunroom_temperature") is not None


def test_env_exclusions(monkeypatch):
    monkeypatch.setenv("DOSYNC_HA_EXCLUDE_ENTITIES", "weather_, pollen_")
    b = _bridge()
    assert _probe(b, "sensor.weather_humidity") is None
    assert _probe(b, "sensor.pollen_count") is None


def test_wiz_skip_still_works():
    """Regression: the pre-existing WiZ dedup filter must survive this change."""
    b = _bridge()
    assert _probe(b, "light.wiz_rgbw_tunable_cocina") is None


def test_prefixes_are_the_known_housekeeping_set():
    """Deliberately minimal: only integrations we KNOW are housekeeping. Growing
    this set is a conscious decision, not a drive-by."""
    assert HA_HOUSEKEEPING_PREFIXES == ("sun_", "backup_")
