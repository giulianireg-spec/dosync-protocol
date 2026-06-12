"""
DoSync Adapter Logic Validation

Covers the framework-agnostic, hardware-agnostic logic in the device adapters:
manifest construction and its compliance with the canonical tag vocabulary, plus
pure value conversions (percentage -> WiZ scale).

TESTING PHILOSOPHY (decided by architecture panel):
This suite tests *decision logic*, NOT *transport mechanism*. The adapters'
execute() methods perform real network I/O — UDP to WiZ bulbs, HTTP to Shelly/HA,
SMS via Twilio. That I/O is deliberately NOT mocked here: it is validated far
better by the certification suite running against the 33 physical devices on the
reference hub. Mocking sockets would re-test, worse, what the hardware already
proves. What the hardware CANNOT see is whether a manifest is built with
vocabulary-compliant tags — a manifest's tag composition is invisible to a
behavioural cert. That blind spot is exactly what this suite guards. If you came
here to add socket/HTTP mocks for execute(), that was a conscious omission — the
hardware + certify own that layer.

Run: python3 -m pytest tests/test_adapters.py -v
  or: python3 tests/test_adapters.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.adapters.wiz import wiz_manifest, WiZAdapter
from dosync.adapters.shelly import shelly_manifest
from dosync.adapters.matter import matter_manifest


# Tags that are NON-PORTABLE or imprecise per spec/TAG-VOCABULARY.md.
# Vendor names are not portable across hubs; "climate" on a light/cover is
# imprecise; "smart-plug" is non-canonical (the canonical tag is "plug");
# "door" is not a vocabulary tag (use "lock" for access, "door-sensor" for sensing).
DEPRECATED_TAGS = {"wiz", "shelly", "matter", "zigbee", "climate", "smart-plug", "door"}


def assert_no_deprecated_tags(manifest, label):
    bad = set(manifest.tags) & DEPRECATED_TAGS
    assert not bad, f"{label}: manifest contains deprecated tags {bad}: {manifest.tags}"


# ── WiZ manifest ──────────────────────────────────────────────────────────────

def test_wiz_manifest_no_deprecated_tags():
    """Regression: wiz_manifest used to inject ['light','wiz','climate']."""
    m = wiz_manifest("wiz-01", "Living Light", "192.168.1.50")
    assert_no_deprecated_tags(m, "wiz_manifest")
    assert "light" in m.tags, "wiz must keep the canonical 'light' role tag"


def test_wiz_manifest_caller_tags_and_room():
    m = wiz_manifest("wiz-01", "Living", "192.168.1.50",
                     tags=["emergency"], room="living-room")
    assert "emergency" in m.tags, "caller-supplied tag must be present"
    assert "living-room" in m.tags, "room must be added as a location tag"
    assert_no_deprecated_tags(m, "wiz_manifest+caller")


def test_wiz_manifest_stores_ip_in_adapter_config():
    m = wiz_manifest("wiz-01", "Living", "192.168.1.50")
    assert m.adapter == "wiz"
    assert m.adapter_config["ip"] == "192.168.1.50", "IP must be in adapter_config"


def test_wiz_manifest_dedupes_tags():
    """Passing a tag that's already in base must not duplicate it."""
    m = wiz_manifest("wiz-01", "Living", "192.168.1.50", tags=["light"])
    assert m.tags.count("light") == 1, "tags must be de-duplicated"


# ── Shelly manifest ───────────────────────────────────────────────────────────

def test_shelly_manifest_no_deprecated_tags():
    """Regression: shelly_manifest used to inject ['shelly','smart-plug',...]."""
    for dtype in ["relay", "dimmer", "plug", "rgbw"]:
        m = shelly_manifest("shelly-01", "Shelly", "192.168.1.60", device_type=dtype)
        assert_no_deprecated_tags(m, f"shelly_manifest({dtype})")


def test_shelly_plug_uses_canonical_plug_tag():
    """The canonical tag is 'plug', never 'smart-plug'."""
    m = shelly_manifest("shelly-01", "Shelly Plug", "192.168.1.60", device_type="plug")
    assert "plug" in m.tags, "shelly plug must carry canonical 'plug' tag"
    assert "smart-plug" not in m.tags


# ── Matter manifest ───────────────────────────────────────────────────────────

def test_matter_manifest_no_deprecated_tags():
    """Regression: matter_manifest used to inject ['matter', 'climate', 'door', ...]."""
    for dtype in ["light", "switch", "cover", "lock", "climate"]:
        m = matter_manifest("matter-01", "Matter", "light.x", device_type=dtype)
        assert_no_deprecated_tags(m, f"matter_manifest({dtype})")


def test_matter_lock_uses_canonical_access_tags():
    """A Matter lock must use canonical access tags, not the non-vocab 'door'."""
    m = matter_manifest("matter-lock-01", "Front Lock", "lock.front", device_type="lock")
    assert "lock" in m.tags, "matter lock must carry canonical 'lock' tag"
    assert "door" not in m.tags, "'door' is not a vocabulary tag"


def test_matter_climate_uses_thermostat_not_climate():
    """A climate device maps to the canonical 'thermostat' role, not 'climate'."""
    m = matter_manifest("matter-clim-01", "Thermostat", "climate.x", device_type="climate")
    assert "thermostat" in m.tags
    assert "climate" not in m.tags


# ── WiZ pure conversion (_pct_to_wiz) ─────────────────────────────────────────

def test_pct_to_wiz_boundaries():
    f = WiZAdapter._pct_to_wiz
    assert f(0) == 0, "0% maps to 0"
    assert f(100) == 255, "100% maps to 255"
    assert f(50) == 128, "50% maps to ~128 (rounded)"


def test_pct_to_wiz_clamps_out_of_range():
    f = WiZAdapter._pct_to_wiz
    assert f(-10) == 0, "negative clamps to 0"
    assert f(150) == 255, "above 100 clamps to 255"


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
    print(f"\n{passed}/{passed+failed} adapter tests passed.")
    sys.exit(1 if failed else 0)
