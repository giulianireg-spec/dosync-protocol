"""
Tests for dosync/route_composer.py — geometry + step composition for spatial
composition intents (the perimeter patrol of inspect_area).

Pure logic, fully offline: no drone, no socket, no hub.
"""

import math

from dosync.route_composer import (
    RouteComposer, destination_point, perimeter_waypoints,
)
from dosync.composite_operations import CompositeStep

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


# Local haversine so the test does not depend on the adapter module.
def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


CLAT, CLON = -31.4201, -64.1888  # Córdoba — the hub location


# ── destination_point (forward geodesic) ──────────────────────────────────────

def test_projection_distance_is_accurate():
    dlat, dlon = destination_point(CLAT, CLON, 0.0, 1000.0)
    measured = _haversine_m(CLAT, CLON, dlat, dlon)
    check("projecting 1000m measures back as ~1000m", abs(measured - 1000.0) < 1.0)


def test_projection_north_increases_latitude():
    dlat, dlon = destination_point(CLAT, CLON, 0.0, 500.0)
    check("north bearing increases latitude", dlat > CLAT)
    check("north bearing barely changes longitude", abs(dlon - CLON) < 0.001)


def test_projection_east_increases_longitude():
    dlat, dlon = destination_point(CLAT, CLON, 90.0, 500.0)
    check("east bearing increases longitude", dlon > CLON)


def test_projection_south_decreases_latitude():
    dlat, _ = destination_point(CLAT, CLON, 180.0, 500.0)
    check("south bearing decreases latitude", dlat < CLAT)


def test_four_bearings_are_distinct():
    pts = [destination_point(CLAT, CLON, b, 1000.0) for b in (0, 90, 180, 270)]
    check("four cardinal projections are distinct points", len(set(pts)) == 4)


# ── perimeter_waypoints ───────────────────────────────────────────────────────

def test_perimeter_has_requested_sides():
    verts = perimeter_waypoints(CLAT, CLON, 1000.0, sides=4)
    check("square perimeter has 4 vertices", len(verts) == 4)
    hexagon = perimeter_waypoints(CLAT, CLON, 1000.0, sides=6)
    check("hexagon perimeter has 6 vertices", len(hexagon) == 6)


def test_all_vertices_equidistant_from_center():
    verts = perimeter_waypoints(CLAT, CLON, 1000.0, sides=4)
    dists = [_haversine_m(CLAT, CLON, la, lo) for la, lo in verts]
    check("all vertices ~1000m from center",
          all(abs(d - 1000.0) < 1.0 for d in dists))


def test_perimeter_rejects_too_few_sides():
    raised = False
    try:
        perimeter_waypoints(CLAT, CLON, 1000.0, sides=2)
    except ValueError:
        raised = True
    check("perimeter with <3 sides rejected", raised)


def test_perimeter_rejects_nonpositive_radius():
    raised = False
    try:
        perimeter_waypoints(CLAT, CLON, 0.0, sides=4)
    except ValueError:
        raised = True
    check("perimeter with radius 0 rejected", raised)


def test_radius_scales():
    near = perimeter_waypoints(CLAT, CLON, 500.0, sides=4)
    far = perimeter_waypoints(CLAT, CLON, 1500.0, sides=4)
    dn = _haversine_m(CLAT, CLON, *near[0])
    df = _haversine_m(CLAT, CLON, *far[0])
    check("larger radius yields farther vertices", df > dn)


# ── RouteComposer.compose_inspect_area ────────────────────────────────────────

def test_inspect_area_step_count():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON), "sides": 4})
    # takeoff + 4 waypoints + return = 6
    check("inspect_area with 4 sides yields 6 steps", len(steps) == 6)


def test_inspect_area_step_order():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON)})
    check("first step is takeoff", steps[0].kind == "takeoff")
    check("first action is take_off", steps[0].action == "take_off")
    check("last step is return", steps[-1].kind == "return")
    check("last action is return_home", steps[-1].action == "return_home")
    check("middle steps are waypoints",
          all(s.kind == "waypoint" for s in steps[1:-1]))


def test_inspect_area_waypoints_carry_coordinates():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON)})
    wp = steps[1]
    check("waypoint has lat", "lat" in wp.params)
    check("waypoint has lon", "lon" in wp.params)
    check("waypoint has altitude", "alt" in wp.params)


def test_inspect_area_applies_altitude():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON), "altitude_m": 45})
    check("takeoff uses requested altitude", steps[0].params["alt"] == 45.0)
    check("waypoints use requested altitude", steps[1].params["alt"] == 45.0)


def test_inspect_area_defaults_applied():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON)})
    # defaults: radius 1000, altitude 30, 4 sides -> 6 steps
    check("default sides=4 gives 6 steps", len(steps) == 6)
    check("default altitude is 30", steps[0].params["alt"] == 30.0)


def test_inspect_area_requires_center():
    rc = RouteComposer()
    raised = False
    try:
        rc.compose_inspect_area("drone-01", {})  # no center
    except ValueError:
        raised = True
    check("inspect_area without center is rejected (never invent a position)", raised)


def test_inspect_area_targets_the_device():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-77", {"center": (CLAT, CLON)})
    check("all steps target the given device",
          all(s.device_id == "drone-77" for s in steps))


def test_steps_are_composite_steps():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON)})
    check("composer returns CompositeStep instances",
          all(isinstance(s, CompositeStep) for s in steps))


def test_perimeter_vertices_match_sides_context():
    rc = RouteComposer()
    steps = rc.compose_inspect_area("drone-01", {"center": (CLAT, CLON), "sides": 6})
    waypoints = [s for s in steps if s.kind == "waypoint"]
    check("sides=6 yields 6 waypoints", len(waypoints) == 6)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{_PASS}/{_PASS + _FAIL} route composer tests passed.")
    if _FAIL:
        raise SystemExit(1)
