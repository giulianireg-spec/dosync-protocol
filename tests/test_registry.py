"""The capability registry, tested against its own module.

The sixth extraction moved CapabilityRegistry out of hub.py. Its behaviour --
the inverted tag index, the emergency index, the register/unregister
bookkeeping and the on_register callback -- is exercised here against
`dosync.registry` directly, not only through the hub that consults it.
"""
from dosync.registry import CapabilityRegistry
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory)


def _device(device_id, tags=(), actuator_types=(), *, emergency=False):
    return CapabilityManifest(
        device_id=device_id, device_name=device_id, manufacturer="test",
        model="test", firmware="1.0", category=DeviceCategory.ACTUATOR,
        tags=list(tags), emergency_capable=emergency, sensors=[],
        actuators=[ActuatorSpec(id=t, type=t, description=t)
                   for t in actuator_types],
    )


def test_find_by_tags_is_a_union():
    r = CapabilityRegistry()
    r.register(_device("a", tags=["light", "kitchen"]))
    r.register(_device("b", tags=["light", "bedroom"]))
    r.register(_device("c", tags=["lock"]))
    assert {m.device_id for m in r.find_by_tags(["light"])} == {"a", "b"}
    assert {m.device_id for m in r.find_by_tags(["kitchen", "lock"])} == {"a", "c"}


def test_find_by_required_tags_is_an_intersection():
    r = CapabilityRegistry()
    r.register(_device("a", tags=["light", "kitchen"]))
    r.register(_device("b", tags=["light", "bedroom"]))
    assert {m.device_id for m in r.find_by_required_tags({"light", "kitchen"})} == {"a"}, \
        "intersection must require ALL tags, not any of them"


def test_emergency_and_actuator_indexes():
    r = CapabilityRegistry()
    r.register(_device("siren", tags=["alarm"], actuator_types=["sound"], emergency=True))
    r.register(_device("lamp", tags=["light"], actuator_types=["turn_on"]))
    assert {m.device_id for m in r.find_emergency_capable()} == {"siren"}
    assert {m.device_id for m in r.find_by_actuator("turn_on")} == {"lamp"}


def test_reregister_updates_the_tag_index():
    r = CapabilityRegistry()
    r.register(_device("d", tags=["light"]))
    r.register(_device("d", tags=["lock"]))          # same id, new tags
    assert r.find_by_tags(["light"]) == [], "a stale tag left the id in the index"
    assert {m.device_id for m in r.find_by_tags(["lock"])} == {"d"}


def test_unregister_clears_the_indexes():
    r = CapabilityRegistry()
    r.register(_device("x", tags=["light"], emergency=True))
    r.unregister("x")
    assert r.get("x") is None
    assert r.find_by_tags(["light"]) == []
    assert r.find_emergency_capable() == []


def test_on_register_callback_fires():
    r = CapabilityRegistry()
    seen = []
    r.on_register(lambda m: seen.append(m.device_id))
    r.register(_device("e"))
    assert seen == ["e"]
