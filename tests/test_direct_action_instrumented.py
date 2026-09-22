"""A direct action is recorded like any other action.

_TimedExecutor is more than a timer: it records every action in device health
(the success-rate history behind /v1/health/devices and the reachability
refresh) and runs the independent verify_with check. Its own comment called it
the single chokepoint all execution paths funnel through -- but only
execute_intent wrapped the executor. POST /v1/device/action, the path the MCP
device-control tool uses, called the executor bare: a direct action left no
latency metric and no health record, so a lamp that failed on it did not count
as a failure anywhere. It was found on the reference hub, where a failed direct
action was missing from action_execution_seconds.

Both paths now go through DoSyncHub.instrumented(), which also no longer
depends on the metrics module -- losing latency numbers must not switch off
health and verification with them.
"""
from fastapi.testclient import TestClient

from dosync.models import ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory


def _register(srv, device_id):
    srv.hub.registry.register(CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id="turn_on", type="turn_on", description="")],
        emergency_capable=False, cert_tier=CertTier.BASIC))


def test_a_direct_action_is_timed_and_recorded_in_device_health():
    import dosync.server as srv
    from dosync import metrics

    def observed():
        return sum(r[2] for r in metrics.action_execution_seconds._data.values())

    _register(srv, "lamp-direct-1")
    before_metric = observed()
    before_health = srv.hub.db.get_device_health("lamp-direct-1")["total"]

    token = srv._auth_manager.generate_key(label="test-direct-instrumented")
    r = TestClient(srv.app, headers={"Authorization": f"Bearer {token}"}).post(
        "/v1/device/action", json={"device_id": "lamp-direct-1", "action": "turn_on"})
    assert r.status_code == 200

    assert observed() == before_metric + 1, "the direct action was not timed"
    assert srv.hub.db.get_device_health("lamp-direct-1")["total"] == before_health + 1, \
        "the direct action left no device-health record"


def test_instrumented_wraps_once_and_does_not_depend_on_metrics(monkeypatch):
    import dosync.hub as hub_module
    from dosync.execution import _TimedExecutor

    monkeypatch.setattr(hub_module, "_M", None)   # metrics unavailable
    hub = hub_module.DoSyncHub(db_path=":memory:")

    class _Bare:
        async def execute(self, action, urgency):
            return None

    wrapped = hub.instrumented(_Bare())
    assert isinstance(wrapped, _TimedExecutor), \
        "without metrics the executor was left unwrapped -- health and verification off too"
    assert hub.instrumented(wrapped) is wrapped, "wrapping twice would record every action twice"
