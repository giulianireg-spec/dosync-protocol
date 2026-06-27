"""
DoSync — Route composer (turns a composition intent into an ordered step sequence).
===================================================================================

The semantic resolver answers "which device and which capability for this intent?"
— it does matching and returns a flat ActionPlan (parallel actions). A perimeter
inspection is a different kind of problem: it is GEOMETRY (where are the waypoints
around the center?) plus ORDER (take off, then each waypoint in sequence, then come
home). That is route planning, not semantic matching.

The expert panel placed this in a SEPARATE component (not a resolver subclass)
because the BaseResolver contract is `resolve(intent) -> ActionPlan`, and an
ActionPlan is FLAT — it cannot express "these steps run in order, each waiting for
the previous to actually arrive." A route is a CompositeOperation, not an ActionPlan.
So the composer lives here, and the hub routes a composition intent to it instead of
to the flat resolver.

GENERIC IN FORM, perimeter as the first concrete case. The composer produces an
ordered list of CompositeStep. Today it knows one shape — a perimeter around a
center — but the structure (takeoff → waypoints → return) generalizes to any spatial
patrol. The navigation math (projecting waypoints around a center) lives here,
visible and testable, not buried inside a semantic resolver.

This module has NO drone dependencies and NO hub dependencies. It is pure geometry
plus step construction, fully unit-testable on its own.

DESIGN NOTE — who plans the route: the brain (AI) or the nervous system (DoSync)?
This is a subtle but defining boundary, so it is worth stating explicitly.

  The AI (the brain) decides the GOAL and its STRATEGIC PARAMETERS: *that* an area
  should be inspected, the center, the radius, the altitude, the shape. None of that
  is invented here — it all arrives as the intent and its context, authored by the
  AI. This composer never decides whether to fly, where, or how high.

  This composer (the nervous system / cerebellum) does only the DETERMINISTIC
  TRANSLATION of that goal into exact motor geometry: given "perimeter, 1km, centered
  here," there is exactly one correct set of corner coordinates. That is computation,
  not decision — the same way the cerebellum turns "walk to the kitchen" into precise
  joint kinematics without the conscious mind computing a single angle. That is why
  compose_inspect_area RAISES if the center is missing: it refuses to invent a
  decision that belongs to the brain.

  Crucially, this composer is NOT mandatory. It is a translation SERVICE the AI uses
  when it wants to delegate the geometry. An AI that prefers to plan every waypoint
  itself — irregular paths, avoiding a zone, optimizing for wind — simply emits its
  own go_to sequence directly and skips the composer entirely, exactly as a brain can
  move a single limb deliberately instead of delegating the gait to the cerebellum.

  In one line: the brain decides the goal and the parameters; the nervous system
  translates that goal into exact motor geometry and keeps the loop closed during
  execution. This file is translation, never autonomous planning.
"""

from __future__ import annotations

import math

from .composite_operations import CompositeStep


# Mean Earth radius in meters (same constant used by the haversine helpers elsewhere,
# kept local so this module depends on nothing).
_EARTH_RADIUS_M = 6371000.0


def destination_point(lat: float, lon: float, bearing_deg: float,
                      distance_m: float) -> tuple[float, float]:
    """Given a start point, a compass bearing, and a distance, return the
    destination (lat, lon). This is the INVERSE of haversine (which measures the
    distance between two known points); here we know one point, a direction, and a
    distance, and we project the second point.

    Standard great-circle (spherical) forward geodesic. Accurate to well within a
    meter at the scales DoSync cares about (perimeters of a few km), and it has no
    dependency on a projection library.

    bearing_deg: 0 = North, 90 = East, 180 = South, 270 = West.
    """
    ang = distance_m / _EARTH_RADIUS_M          # angular distance in radians
    brg = math.radians(bearing_deg)
    p1 = math.radians(lat)
    l1 = math.radians(lon)

    p2 = math.asin(
        math.sin(p1) * math.cos(ang)
        + math.cos(p1) * math.sin(ang) * math.cos(brg)
    )
    l2 = l1 + math.atan2(
        math.sin(brg) * math.sin(ang) * math.cos(p1),
        math.cos(ang) - math.sin(p1) * math.sin(p2),
    )
    # Normalize longitude to [-180, 180].
    lon2 = (math.degrees(l2) + 540) % 360 - 180
    return (math.degrees(p2), lon2)


def perimeter_waypoints(center_lat: float, center_lon: float,
                        radius_m: float, sides: int = 4,
                        start_bearing_deg: float = 0.0) -> list[tuple[float, float]]:
    """Compute the vertices of a regular polygon (default: square) inscribed in a
    circle of `radius_m` around the center. These are the corners the vehicle flies
    to in order to patrol the perimeter.

    sides=4 gives a square (the natural perimeter of a property); higher values
    approximate a circular patrol. The vertices are returned in clockwise order
    starting from `start_bearing_deg`.
    """
    if sides < 3:
        raise ValueError("a perimeter needs at least 3 sides")
    if radius_m <= 0:
        raise ValueError("radius must be positive")
    step = 360.0 / sides
    return [
        destination_point(center_lat, center_lon,
                          (start_bearing_deg + i * step) % 360, radius_m)
        for i in range(sides)
    ]


class RouteComposer:
    """Composes an ordered CompositeStep sequence for a spatial composition intent.

    Today it handles one intent shape — `inspect_area` — by building a perimeter
    patrol around a center: take off, fly each perimeter vertex in order, return
    home. The hub routes a composition intent here instead of to the flat resolver,
    then wraps the returned steps in a CompositeOperation for the supervisor to drive.

    Stateless and dependency-free: give it a device id and a context, get back steps.
    """

    # Sensible defaults; every value can be overridden via the intent context so a
    # deployment configures its own patrol without code changes.
    DEFAULT_RADIUS_M = 1000.0
    DEFAULT_ALTITUDE_M = 30.0
    DEFAULT_SIDES = 4

    def compose_inspect_area(self, device_id: str, context: dict) -> list[CompositeStep]:
        """Build the step sequence for an `inspect_area` intent.

        Required context:
          center: (lat, lon)  — the patrol center. The panel's design: this is the
                                hub's own location (the system's "home"), so the
                                perimeter is centered on where the coordinating mind
                                physically sits.
        Optional context:
          radius_m, altitude_m, sides, start_bearing_deg — override the defaults.

        Returns: take_off → go_to(each vertex) → return_home, as ordered CompositeSteps.
        Raises ValueError if the center is missing — we never invent a location.
        """
        center = context.get("center")
        if not center or len(center) != 2:
            raise ValueError(
                "inspect_area requires context['center'] = (lat, lon) — "
                "the patrol center (the hub's location). We never invent a position."
            )
        center_lat, center_lon = float(center[0]), float(center[1])
        radius_m = float(context.get("radius_m", self.DEFAULT_RADIUS_M))
        altitude_m = float(context.get("altitude_m", self.DEFAULT_ALTITUDE_M))
        sides = int(context.get("sides", self.DEFAULT_SIDES))
        start_bearing = float(context.get("start_bearing_deg", 0.0))

        vertices = perimeter_waypoints(center_lat, center_lon, radius_m,
                                       sides=sides, start_bearing_deg=start_bearing)

        steps: list[CompositeStep] = []

        # 1. Take off to patrol altitude.
        steps.append(CompositeStep(
            device_id=device_id, action="take_off",
            params={"alt": altitude_m}, kind="takeoff",
        ))

        # 2. Fly each perimeter vertex in order, at patrol altitude.
        for i, (wlat, wlon) in enumerate(vertices):
            steps.append(CompositeStep(
                device_id=device_id, action="go_to",
                params={"lat": wlat, "lon": wlon, "alt": altitude_m},
                kind="waypoint",
            ))

        # 3. Return home and land. The panel: a patrol always ends back at base; the
        #    return is part of the operation, not optional.
        steps.append(CompositeStep(
            device_id=device_id, action="return_home",
            params={}, kind="return",
        ))

        return steps
