"""Regression: the hub-wired resolver MUST read intent resolutions from the DB.

Found 2026-07-11: StateAwareResolver stores the hub as `self._hub`, but
_get_resolution looked up `self.hub` — always None — so every resolution came
back empty and the resolver ran only on the emergency-capable bonus. One line,
protocol-wide silent failure. These tests pin the wiring contract.
"""
from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           Intent, IntentClass, Urgency)


def _hub():
    return DoSyncHub(db_path=":memory:")


def test_universal_resolutions_are_not_empty():
    hub = _hub()
    expected_tags = {
        "ensure_safety":  {"emergency", "alarm", "communication", "notification"},
        "alert_anomaly":  {"communication", "notification", "sensor"},
        "control_access": {"lock"},
        "notify":         {"communication", "notification", "display"},
    }
    for name, tags in expected_tags.items():
        intent = Intent(intent_id="t", intent=IntentClass(name),
                        urgency=Urgency.INFO, context={})
        res = hub.resolver._get_resolution(intent)
        assert set(res.get("tags", [])) == tags, (
            f"{name}: resolution tags {res.get('tags')} != seeded {tags} — "
            "resolver is not reading the DB (hub wiring broken?)")


def test_report_status_resolution_is_deliberately_empty():
    hub = _hub()
    intent = Intent(intent_id="t", intent=IntentClass("report_status"),
                    urgency=Urgency.INFO, context={})
    res = hub.resolver._get_resolution(intent)
    assert res.get("tags") == [] and res.get("actuators") == []


def test_tag_matched_device_resolves_without_emergency():
    """A lock must resolve for control_access at ALERT urgency (no emergency bonus).
    This is the end-to-end behavior the broken wiring silently killed."""
    hub = _hub()
    hub.registry.register(CapabilityManifest(
        device_id="lock-1", device_name="Lock", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["lock", "entrance"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id="l", type="lock", description=""),
                   ActuatorSpec(id="u", type="unlock", description="")],
        emergency_capable=False, cert_tier="standard",
    ))
    intent = Intent(intent_id="t", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT, context={})
    plan = hub.resolver.resolve(intent)
    assert any(a.device_id == "lock-1" for a in plan.actions), (
        "lock did not resolve for control_access — resolution wiring broken")


def test_external_resolver_and_its_fallback_are_wired():
    """Regression (2026-07-11, second door): server.py replaces hub.resolver with
    an ExternalResolver whose local fallback did NOT receive the hub handle —
    every production explain() and every fallback resolution ran with empty
    resolutions. Both the wrapper and its fallback must read real resolutions."""
    from dosync.hub import ExternalResolver
    hub = _hub()
    ext = ExternalResolver(hub.registry, "http://resolver.invalid:9", hub_id="t", hub=hub)
    intent = Intent(intent_id="t", intent=IntentClass("control_access"),
                    urgency=Urgency.ALERT, context={})
    # ExternalResolver itself delegates resolution to the external service
    # (BaseResolver — no _get_resolution); the DB-reading path is its fallback.
    assert ext._fallback._get_resolution(intent).get("tags") == ["lock"]
    # explain flows through the fallback: it must show the real breakdown,
    # not the read-only empty-resolution branch
    exp = ext.explain(intent)
    assert exp["resolution_tags"] == ["lock"]
