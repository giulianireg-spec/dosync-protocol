"""An action that never left the hub must say so.

The reference deployment ran an SMS notifier whose manifest named no adapter.
Every `notify` action fell to the SimulatedExecutor, came back `success=True`,
and was logged at INFO as `Executed:`. The device looked healthy in every
listing, the intent reported actions taken, and no SMS was ever sent — for an
unknown length of time, on the deployment whose drills are the project's
evidence.

`success` and `simulated` answer different questions. `success=False` means
something went wrong; `simulated=True` means nothing went anywhere. Collapsing
them made the Data layer — the one layer that is supposed never to lie —
mislead by omission, and mislead the AI layer that reasons on top of it: an
agent told that `notify` succeeded during an emergency concludes the people who
needed to know were told, and stops looking for another way.

Three adapters (Shelly, Matter, BLE) already marked simulation inside
`response`. That is the right instinct in the wrong place: a caller should not
have to know which adapter answered to learn whether anything happened.
"""
import asyncio

import pytest

from dosync.executor import SimulatedExecutor
from dosync.models import (ActionResult, DeviceAction, SIMULATION_REASONS,
                           Urgency)


def _run(coro):
    return asyncio.run(coro)


def test_action_result_defaults_to_not_simulated():
    """Silence must mean "executed", or every existing caller changes meaning."""
    r = ActionResult(device_id="d", action="turn_on", success=True)
    assert r.simulated is False and r.simulated_reason is None


def test_the_simulated_executor_declares_itself():
    ex = SimulatedExecutor()
    r = _run(ex.execute(DeviceAction(device_id="d", action="turn_on", params={}),
                        Urgency.INFO))
    assert r.success is True
    assert r.simulated is True, \
        "the simulator reported an action as executed"
    assert r.simulated_reason in SIMULATION_REASONS


def test_a_simulated_failure_is_still_marked_simulated():
    """success=False and simulated=True is a real state: nothing was reached
    AND the simulation chose to fail. An operator debugging a failure needs to
    know the failure was invented."""
    ex = SimulatedExecutor()
    ex.set_device_behavior("d", always_fail=True)
    r = _run(ex.execute(DeviceAction(device_id="d", action="turn_on", params={}),
                        Urgency.INFO))
    assert r.success is False and r.simulated is True


def test_the_reason_travels_from_the_caller():
    """Only the caller knows why simulation happened."""
    ex = SimulatedExecutor()
    r = _run(ex.execute(DeviceAction(device_id="d", action="turn_on", params={}),
                        Urgency.INFO, reason="no_adapter_declared"))
    assert r.simulated_reason == "no_adapter_declared"


def test_every_declared_reason_is_a_known_one():
    assert set(SIMULATION_REASONS) == {
        "no_adapter_declared", "adapter_unavailable", "explicit_simulation"}


def test_a_device_with_no_adapter_falls_back_and_says_which_reason(caplog):
    """The end-to-end path that produced the finding."""
    from dosync.adapters import AdapterExecutor
    from dosync.hub import DoSyncHub
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory)

    hub = DoSyncHub(db_path=":memory:")
    hub.register_device(CapabilityManifest(
        device_id="notifier-x", device_name="notifier", manufacturer="t",
        model="t", firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["notification"], emergency_capable=False, sensors=[],
        actuators=[ActuatorSpec(id="notify", type="notify", description="n")]))
    executor = AdapterExecutor(hub, fallback_to_simulated=True)

    result = _run(executor.execute(
        DeviceAction(device_id="notifier-x", action="notify", params={}),
        Urgency.EMERGENCY))

    assert result.success is True
    assert result.simulated is True, \
        "a device with no adapter reported an executed action"
    assert result.simulated_reason == "no_adapter_declared"


def test_registration_warns_when_nothing_can_execute_the_device(caplog):
    """Discoverable at registration, and it was being discovered months later."""
    import logging
    from dosync.hub import DoSyncHub
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory)

    hub = DoSyncHub(db_path=":memory:")
    with caplog.at_level(logging.WARNING):
        hub.register_device(CapabilityManifest(
            device_id="orphan-01", device_name="orphan", manufacturer="t",
            model="t", firmware="1", category=DeviceCategory.ACTUATOR,
            tags=["light"], emergency_capable=False, sensors=[],
            actuators=[ActuatorSpec(id="turn_on", type="turn_on", description="on")]))
    assert any("no adapter" in r.message.lower() or "simulated" in r.message.lower()
               for r in caplog.records), \
        "registering a device nothing can execute produced no warning"


def test_a_sensor_only_device_does_not_warn(caplog):
    """A device with no actuators has nothing to execute; warning would be noise."""
    import logging
    from dosync.hub import DoSyncHub
    from dosync.models import (CapabilityManifest, DeviceCategory, SensorSpec)

    hub = DoSyncHub(db_path=":memory:")
    with caplog.at_level(logging.WARNING):
        hub.register_device(CapabilityManifest(
            device_id="sensor-01", device_name="sensor", manufacturer="t",
            model="t", firmware="1", category=DeviceCategory.SENSOR,
            tags=["sensor"], emergency_capable=False,
            sensors=[SensorSpec(id="motion", type="boolean", description="m")],
            actuators=[]))
    assert not any("no adapter" in r.message.lower() for r in caplog.records)
