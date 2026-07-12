"""Semantics pinned by the 2026-07-11 panel decisions (F2b, F3b, F4a).

F3b — hard filter applies ONLY to all-specific resolutions; mixed resolutions
      treat specific tags as boost, not gate.
F2b — at EMERGENCY, an emergency_capable device whose resolution-scoped build
      yields zero actions falls back to its full capability set.
F4a — empty resolution = read-only status query: read_sensors on sensing
      devices, actuators never fire.
"""
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           Intent, IntentClass, SensorSpec, Urgency)


def _dev(did, tags, act_types=(), sensors=(), emergency=False):
    return CapabilityManifest(
        device_id=did, device_name=did, manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=list(tags),
        sensors=[SensorSpec(id=f"s{i}", type=t, description="")
                 for i, t in enumerate(sensors)],
        actuators=[ActuatorSpec(id=f"a{i}", type=t, description="")
                   for i, t in enumerate(act_types)],
        events=[], emergency_capable=emergency, cert_tier="standard",
    )


def _hub(*devices):
    hub = DoSyncHub(db_path=":memory:")
    for d in devices:
        hub.registry.register(d)
    return hub


def _resolve(hub, name, urgency, ctx=None):
    intent = Intent(intent_id="t", intent=IntentClass(name),
                    urgency=urgency, context=ctx or {})
    return {a.device_id for a in hub.resolver.resolve(intent).actions}, intent


# ── F3b ──────────────────────────────────────────────────────────────────────

def test_all_specific_resolution_still_gates():
    # control_access resolution = ["lock"]... "lock" is specific (not generic)
    hub = _hub(_dev("no-lock", ["light", "living-room"], act_types=["turn_on"]))
    got, _ = _resolve(hub, "control_access", Urgency.ALERT)
    assert got == set(), "all-specific resolution must gate non-matching devices"


def test_mixed_resolution_does_not_gate_generic_matches():
    # alert_anomaly = [communication, notification, sensor]: mixed (sensor/communication
    # generic + notification specific). A sensor-tagged device must participate.
    pir = _dev("pir-1", ["sensor", "motion"], sensors=["motion"])
    hub = _hub(pir)
    got, _ = _resolve(hub, "alert_anomaly", Urgency.ALERT)
    assert "pir-1" in got, "mixed resolution gated a generic-tag match (F3b violated)"


# ── F2b ──────────────────────────────────────────────────────────────────────

def test_emergency_capable_with_unmatched_actuators_acts_full_set():
    # Light whose actuator types are NOT in ensure_safety's actuator list at all
    lamp = _dev("lamp-1", ["light", "emergency"], act_types=["set_color"], emergency=True)
    hub = _hub(lamp)
    intent = Intent(intent_id="t", intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    plan = hub.resolver.resolve(intent)
    acts = [a for a in plan.actions if a.device_id == "lamp-1"]
    assert acts, "emergency_capable device produced zero actions in an emergency (F2b violated)"
    assert acts[0].action == "set_color"


def test_no_full_set_fallback_outside_emergency():
    lamp = _dev("lamp-2", ["communication"], act_types=["set_color"], emergency=True)
    hub = _hub(lamp)
    got, _ = _resolve(hub, "notify", Urgency.INFO)
    assert "lamp-2" not in got, "full-set fallback must not apply outside emergencies"


# ── F4a ──────────────────────────────────────────────────────────────────────

def test_report_status_reads_sensors_and_never_actuates():
    pir = _dev("pir-2", ["sensor"], sensors=["motion"])
    lamp = _dev("lamp-3", ["light"], act_types=["turn_on"])
    hub = _hub(pir, lamp)
    intent = Intent(intent_id="t", intent=IntentClass("report_status"),
                    urgency=Urgency.INFO, context={})
    plan = hub.resolver.resolve(intent)
    assert {a.device_id for a in plan.actions} == {"pir-2"}
    assert all(a.action == "read_sensors" for a in plan.actions), \
        "a status query fired an actuator (F4a violated)"


def test_explain_mirrors_read_only_semantics():
    pir = _dev("pir-3", ["sensor"], sensors=["motion"])
    lamp = _dev("lamp-4", ["light"], act_types=["turn_on"])
    hub = _hub(pir, lamp)
    intent = Intent(intent_id="t", intent=IntentClass("report_status"),
                    urgency=Urgency.INFO, context={})
    exp = hub.resolver.explain(intent)
    inc = {d["device_id"] for d in exp["included"]}
    assert inc == {"pir-3"}
    assert "read-only" in exp["note"] or "read_sensors" in exp["note"]
