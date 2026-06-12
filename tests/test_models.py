"""
DoSync Core Models Validation

Covers the type system in models.py: the Urgency/Severity separation,
IntentClass format validation and equality semantics, string→enum coercion
in __post_init__, and manifest serialization (including the to_public_dict
privacy guarantee that hides adapter_config).

Run: python3 -m pytest tests/test_models.py -v
  or: python3 tests/test_models.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.models import (
    Urgency, Severity, DeviceCategory, CertTier, FailurePolicy,
    IntentClass, ContextSignalType,
    EventSpec, DeviceEvent, ActuatorSpec, SensorSpec,
    CapabilityManifest,
)


# ── IntentClass format validation ─────────────────────────────────────────────

def test_intent_class_accepts_valid_names():
    for name in ["notify", "ensure_safety", "save_energy", "a", "x1_y2"]:
        ic = IntentClass(name)
        assert ic.value == name, f"valid name '{name}' must be accepted"


def test_intent_class_rejects_invalid_names():
    for bad in ["Notify", "ensure-safety", "1intent", "save energy", "_leading", ""]:
        try:
            IntentClass(bad)
            assert False, f"invalid name '{bad}' must raise ValueError"
        except ValueError:
            pass  # expected


def test_intent_class_value_property():
    """IntentClass.value must return the string for Enum-style access."""
    ic = IntentClass("notify")
    assert ic.value == "notify"
    assert isinstance(ic.value, str)


def test_intent_class_equality_with_string():
    """IntentClass must compare equal to its string form."""
    ic = IntentClass("notify")
    assert ic == "notify", "IntentClass must equal its string value"
    assert ic == IntentClass("notify"), "two equal IntentClasses must be equal"
    assert ic != IntentClass("ensure_safety")


def test_intent_class_hashable_and_usable_as_dict_key():
    """IntentClass must hash consistently with its string form."""
    d = {IntentClass("notify"): 1}
    assert d[IntentClass("notify")] == 1, "IntentClass must work as a dict key"
    assert hash(IntentClass("notify")) == hash("notify"), \
        "hash must match the underlying string"


def test_intent_class_universal_constants():
    """The five universal intents must be attached as constants."""
    assert IntentClass.ENSURE_SAFETY == "ensure_safety"
    assert IntentClass.ALERT_ANOMALY == "alert_anomaly"
    assert IntentClass.CONTROL_ACCESS == "control_access"
    assert IntentClass.REPORT_STATUS == "report_status"
    assert IntentClass.NOTIFY == "notify"


# ── Urgency / Severity separation ─────────────────────────────────────────────

def test_urgency_values():
    assert Urgency.INFO.value == "info"
    assert Urgency.WARNING.value == "warning"
    assert Urgency.ALERT.value == "alert"
    assert Urgency.EMERGENCY.value == "emergency"


def test_severity_values():
    assert Severity.INFO.value == "info"
    assert Severity.WARNING.value == "warning"
    assert Severity.ALERT.value == "alert"
    assert Severity.EMERGENCY.value == "emergency"


def test_urgency_and_severity_are_distinct_types():
    """The historic bug: treating Urgency and Severity as interchangeable.
    They share string values but are different enum types."""
    assert Urgency.EMERGENCY is not Severity.EMERGENCY, \
        "Urgency and Severity must be distinct enum members"
    assert type(Urgency.EMERGENCY) is not type(Severity.EMERGENCY), \
        "they must be different types — a low-severity emergency must be expressible"


def test_urgency_from_string():
    assert Urgency("emergency") == Urgency.EMERGENCY
    assert Severity("warning") == Severity.WARNING


def test_invalid_enum_value_raises():
    for bad in ["critical", "high", "URGENT", ""]:
        try:
            Urgency(bad)
            assert False, f"invalid urgency '{bad}' must raise"
        except ValueError:
            pass


# ── String → enum coercion in __post_init__ ───────────────────────────────────

def test_event_spec_coerces_severity_string():
    """EventSpec receives raw strings from FastAPI; must coerce to Severity."""
    spec = EventSpec(id="fall", severity="emergency")
    assert spec.severity is Severity.EMERGENCY, \
        "string severity must be coerced to the Severity enum"


def test_event_spec_keeps_enum_severity():
    spec = EventSpec(id="fall", severity=Severity.ALERT)
    assert spec.severity is Severity.ALERT, "enum severity must pass through unchanged"


def test_device_event_coerces_severity_string():
    ev = DeviceEvent(device_id="pir-1", event_id="motion", severity="warning")
    assert ev.severity is Severity.WARNING, "DeviceEvent must coerce severity string"


def test_device_event_to_dict_serializes_severity_as_string():
    ev = DeviceEvent(device_id="pir-1", event_id="motion", severity=Severity.ALERT)
    d = ev.to_dict()
    assert d["severity"] == "alert", "to_dict must serialize severity back to a string"


# ── CapabilityManifest serialization + privacy ────────────────────────────────

def make_manifest(adapter_config=None):
    return CapabilityManifest(
        device_id="wiz-01",
        device_name="Living Light",
        manufacturer="Signify",
        model="WiZ",
        firmware="1.0",
        category=DeviceCategory.ACTUATOR,
        tags=["light", "living-room"],
        actuators=[ActuatorSpec(id="turn_on", type="turn_on")],
        emergency_capable=True,
        adapter="wiz",
        adapter_config=adapter_config or {"ip": "192.168.1.50", "token": "secret"},
    )


def test_manifest_to_dict_includes_adapter_config():
    m = make_manifest()
    d = m.to_dict()
    assert d["adapter_config"]["ip"] == "192.168.1.50", \
        "internal to_dict must include adapter_config for routing"


def test_manifest_to_public_dict_hides_adapter_config():
    """Privacy guarantee: public API responses must never leak adapter_config."""
    m = make_manifest()
    pub = m.to_public_dict()
    assert "adapter_config" not in pub, \
        "to_public_dict must strip adapter_config (IPs, tokens)"
    # But the public-safe fields must remain
    assert pub["device_id"] == "wiz-01"
    assert pub["emergency_capable"] is True


def test_manifest_category_serialized_as_string():
    m = make_manifest()
    d = m.to_dict()
    assert d["category"] == "actuator", "category enum must serialize to its string value"


def test_manifest_without_adapter_omits_adapter_keys():
    m = CapabilityManifest(
        device_id="sim-01", device_name="Sim", manufacturer="x", model="y",
        firmware="1.0", category=DeviceCategory.SENSOR, tags=["sensor"],
    )
    d = m.to_dict()
    assert "adapter" not in d, "manifest without adapter must omit the adapter key"


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} model tests passed.")
    sys.exit(1 if failed else 0)
