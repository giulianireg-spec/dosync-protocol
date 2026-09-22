"""Device health is recorded once per real action, and never for a simulated one.

Two recorders wrote the same row: AdapterExecutor recorded every adapter
execution, and _TimedExecutor -- the wrapper every path goes through -- recorded
it again. Every real action counted twice in a device's success rate. The
existing chokepoint test asserted `total >= 1`, which two rows satisfy.

And _TimedExecutor counted simulated results too. A device whose adapter is
missing is answered by SimulatedExecutor with success=True and simulated=True --
nothing reached the device -- and that result went into its health history as a
success and refreshed it as reachable. On the reference hub, sensors whose
adapter was unavailable read as 100% healthy.
"""
import asyncio

from dosync.adapters import AdapterExecutor, DoSyncAdapter
from dosync.hub import DoSyncHub
from dosync.models import (ActionResult, ActuatorSpec, CapabilityManifest, CertTier,
                           DeviceAction, DeviceCategory, Urgency)


class _Failing(DoSyncAdapter):
    @property
    def adapter_name(self):
        return "fake-failing"

    async def connect(self, config):
        return True

    async def disconnect(self):
        pass

    async def execute(self, action, urgency):
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=False, error="the bulb did not answer")


def _hub_with(device_id, adapter):
    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"], sensors=[],
        events=[], actuators=[ActuatorSpec(id="turn_on", type="turn_on", description="")],
        emergency_capable=False, cert_tier=CertTier.BASIC, adapter=adapter))
    executor = AdapterExecutor(hub)
    executor.register(_Failing())
    return hub, hub.instrumented(executor)


def _run(executor, device_id):
    return asyncio.run(executor.execute(DeviceAction(device_id=device_id, action="turn_on"),
                                        Urgency.INFO))


def test_a_real_action_is_recorded_exactly_once():
    hub, executor = _hub_with("lamp-real", "fake-failing")
    _run(executor, "lamp-real")
    assert hub.db.get_device_health("lamp-real")["total"] == 1


def test_a_simulated_action_is_not_a_health_signal():
    hub, executor = _hub_with("sensor-no-adapter", "not-installed")
    result = _run(executor, "sensor-no-adapter")
    assert result.simulated is True
    assert hub.db.get_device_health("sensor-no-adapter")["total"] == 0, \
        "a simulated result went into the device's health history"
    assert hub.health.snapshot("sensor-no-adapter")["reachable"] is not True, \
        "a device nothing reached was marked reachable"


def test_simulated_actions_are_labelled_in_the_metrics():
    from dosync import metrics

    def count(label):
        return sum(rec[2] for key, rec in metrics.action_execution_seconds._data.items()
                   if label in str(key))

    _, executor = _hub_with("sensor-no-adapter-2", "not-installed")
    before_sim, before_ok = count("simulated"), count("success")
    _run(executor, "sensor-no-adapter-2")
    assert count("simulated") == before_sim + 1
    assert count("success") == before_ok, "a simulated action was counted as a success"
