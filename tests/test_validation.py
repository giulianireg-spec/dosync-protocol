"""
DoSync Parameter Schema Validation Tests (protocol v0.3)

Covers the params_schema → JSON Schema contract change:
  - The validation module: well-formed-schema check, params validation,
    graceful degradation when jsonschema is absent.
  - The migrated manifests (WiZ, HA): every params_schema is valid JSON Schema
    draft 2020-12, and validates real params correctly.

Run: python3 tests/test_validation.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.validation import (
    is_valid_json_schema, validate_params, validate_manifest_schemas,
    jsonschema_available,
)


# ── Well-formed-schema check (manifest integrity) ──────────────────────────────

def test_valid_schema_accepted():
    schema = {"type": "object",
              "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}}}
    ok, err = is_valid_json_schema(schema)
    assert ok is True, err


def test_empty_schema_is_valid():
    """Empty {} means 'no parameters' — valid (turn_on, turn_off, etc.)."""
    ok, err = is_valid_json_schema({})
    assert ok is True


def test_malformed_schema_rejected():
    """A schema with an invalid constraint type must be caught — only meaningful
    when jsonschema is installed."""
    if not jsonschema_available():
        return  # degradation path covered separately
    bad = {"type": "object",
           "properties": {"x": {"type": "integer", "minimum": "not-a-number"}}}
    ok, err = is_valid_json_schema(bad)
    assert ok is False
    assert err and "invalid json schema" in err.lower()


# ── Params validation (execution policy) ───────────────────────────────────────

def test_valid_params_pass():
    schema = {"type": "object",
              "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}},
              "required": ["brightness"]}
    ok, err = validate_params(schema, {"brightness": 80})
    assert ok is True, err


def test_out_of_range_params_rejected():
    """The whole point: brightness 150 must be caught before reaching the device."""
    if not jsonschema_available():
        return
    schema = {"type": "object",
              "properties": {"brightness": {"type": "integer", "minimum": 0, "maximum": 100}},
              "required": ["brightness"]}
    ok, err = validate_params(schema, {"brightness": 150})
    assert ok is False
    assert err is not None


def test_wrong_type_params_rejected():
    if not jsonschema_available():
        return
    schema = {"type": "object",
              "properties": {"brightness": {"type": "integer"}}}
    ok, err = validate_params(schema, {"brightness": "bright"})
    assert ok is False


def test_missing_required_param_rejected():
    if not jsonschema_available():
        return
    schema = {"type": "object",
              "properties": {"kelvin": {"type": "integer"}},
              "required": ["kelvin"]}
    ok, err = validate_params(schema, {})
    assert ok is False


def test_empty_schema_accepts_anything():
    ok, err = validate_params({}, {"whatever": 123})
    assert ok is True


# ── Migrated manifests produce valid JSON Schema ───────────────────────────────

def test_wiz_manifest_schemas_all_valid():
    from dosync.adapters.wiz import wiz_manifest
    m = wiz_manifest("wiz-test", "WiZ Test", "1.2.3.4")
    problems = validate_manifest_schemas(m)
    assert problems == [], f"WiZ manifest has schema problems: {problems}"


def test_wiz_brightness_validates_real_params():
    """End-to-end: pull the real migrated brightness schema and check it rejects
    out-of-range values."""
    if not jsonschema_available():
        return
    from dosync.adapters.wiz import wiz_manifest
    m = wiz_manifest("wiz-test", "WiZ Test", "1.2.3.4")
    bright = next(a for a in m.actuators if a.type == "set_brightness")
    ok_good, _ = validate_params(bright.params_schema, {"brightness": 50})
    ok_bad, _ = validate_params(bright.params_schema, {"brightness": 999})
    assert ok_good is True
    assert ok_bad is False


def test_wiz_color_validates_rgb_range():
    if not jsonschema_available():
        return
    from dosync.adapters.wiz import wiz_manifest
    m = wiz_manifest("wiz-test", "WiZ Test", "1.2.3.4")
    color = next(a for a in m.actuators if a.type == "set_color")
    ok_good, _ = validate_params(color.params_schema, {"r": 255, "g": 0, "b": 128})
    ok_bad, _ = validate_params(color.params_schema, {"r": 300, "g": 0, "b": 0})
    assert ok_good is True
    assert ok_bad is False


# ── Graceful degradation ───────────────────────────────────────────────────────

def test_degradation_when_jsonschema_absent(monkeypatch=None):
    """When jsonschema is unavailable, validation must not fail hard — it returns
    valid with a warning. We simulate absence by toggling the module flag."""
    import dosync.validation as v
    original = v._JSONSCHEMA_AVAILABLE
    try:
        v._JSONSCHEMA_AVAILABLE = False
        ok_schema, _ = v.is_valid_json_schema({"type": "object"})
        ok_params, _ = v.validate_params({"type": "object"}, {"anything": 1})
        assert ok_schema is True  # degrades to accept, not fail
        assert ok_params is True
    finally:
        v._JSONSCHEMA_AVAILABLE = original


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
    print(f"\n{passed}/{passed+failed} validation tests passed.")
    sys.exit(1 if failed else 0)
