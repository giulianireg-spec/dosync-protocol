"""
Tests for dosync/geo.py — the shared geospatial helpers (the single source of truth
for the geofence formula).

Pure logic, fully offline.
"""

from dosync.geo import haversine_m, is_within_perimeter, EARTH_RADIUS_M

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  \u2713  {name}")
    else:
        _FAIL += 1
        print(f"  \u2717  {name}")


CLAT, CLON = -31.4201, -64.1888  # Córdoba


# ── haversine_m ───────────────────────────────────────────────────────────────

def test_zero_distance():
    check("same point is 0m", haversine_m(CLAT, CLON, CLAT, CLON) < 0.001)


def test_known_distance():
    # ~111.2 km per degree of latitude near the equator/mid-latitudes.
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    check("1 degree latitude is ~111km", 110000 < d < 112000)


def test_symmetry():
    a = haversine_m(CLAT, CLON, -31.40, -64.17)
    b = haversine_m(-31.40, -64.17, CLAT, CLON)
    check("distance is symmetric", abs(a - b) < 0.001)


def test_earth_radius_constant():
    check("earth radius is the standard mean value", EARTH_RADIUS_M == 6371000.0)


# ── is_within_perimeter ───────────────────────────────────────────────────────

def test_inside_perimeter():
    ok, reason = is_within_perimeter(CLAT, CLON, CLAT, CLON, 1000.0)
    check("center is inside", ok is True)
    check("inside has empty reason", reason == "")


def test_just_inside():
    # A point ~500m away, well inside a 1000m radius.
    ok, _ = is_within_perimeter(-31.4156, -64.1888, CLAT, CLON, 1000.0)
    check("500m point inside 1000m perimeter", ok is True)


def test_outside_perimeter():
    # ~5km away, outside a 1000m radius.
    ok, reason = is_within_perimeter(-31.465, -64.1888, CLAT, CLON, 1000.0)
    check("5km point outside 1000m perimeter", ok is False)
    check("breach reason mentions the geofence", "geofence" in reason)


def test_altitude_within_ceiling():
    ok, _ = is_within_perimeter(CLAT, CLON, CLAT, CLON, 1000.0,
                                alt=50.0, max_altitude_m=120.0)
    check("altitude within ceiling is ok", ok is True)


def test_altitude_exceeds_ceiling():
    ok, reason = is_within_perimeter(CLAT, CLON, CLAT, CLON, 1000.0,
                                     alt=200.0, max_altitude_m=120.0)
    check("altitude over ceiling is breach", ok is False)
    check("breach reason mentions ceiling", "ceiling" in reason)


def test_no_ceiling_allows_any_altitude():
    ok, _ = is_within_perimeter(CLAT, CLON, CLAT, CLON, 1000.0,
                                alt=5000.0, max_altitude_m=None)
    check("no ceiling allows any altitude", ok is True)


def test_horizontal_breach_takes_precedence():
    # Outside horizontally AND above ceiling → horizontal breach reported first.
    ok, reason = is_within_perimeter(-31.465, -64.1888, CLAT, CLON, 1000.0,
                                     alt=200.0, max_altitude_m=120.0)
    check("combined breach is not ok", ok is False)
    check("horizontal breach reported first", "geofence" in reason)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{_PASS}/{_PASS + _FAIL} geo helper tests passed.")
    if _FAIL:
        raise SystemExit(1)
