"""
DoSync — shared geospatial helpers.
===================================

The single source of truth for the geofence math. Before this module the
haversine formula was copied in three places (the GeofencePolicy, the MAVLink
listener's waypoint-arrival check, and the RouteComposer). The expert panel flagged
that as the one real trap: two defenses in two moments (admission + in-flight
monitoring) are GOOD, but the same FORMULA living in several copies is a latent bug —
fix it in one place and the others drift. So the rule lives here, once, and every
caller invokes it.

Pure functions, no dependencies beyond the stdlib, fully unit-testable.
"""

from __future__ import annotations

import math

# Mean Earth radius in meters. One constant, shared by every distance calculation.
EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters.

    The canonical distance function for DoSync. Accurate to well within a meter at
    the scales the protocol cares about (perimeters of a few km).
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


def is_within_perimeter(lat: float, lon: float,
                        center_lat: float, center_lon: float,
                        max_radius_m: float,
                        alt: float | None = None,
                        max_altitude_m: float | None = None) -> tuple[bool, str]:
    """Is a point inside the permitted perimeter (a horizontal circle plus an
    optional altitude ceiling)?

    Returns (ok, reason). When ok is False, reason explains the breach in
    human-readable terms — the same wording the GeofencePolicy (admission) and the
    in-flight geofence guard (monitoring) both surface, so a breach reads identically
    whether it was caught before takeoff or during flight.

    This is the shared rule. The two callers differ only in WHEN they call it and on
    WHICH point: the policy checks a go_to's *target* before dispatch; the guard
    checks the vehicle's *reported position* during flight. Same rule, two moments.
    """
    distance = haversine_m(center_lat, center_lon, lat, lon)
    if distance > max_radius_m:
        return (False,
                f"({lat:.5f}, {lon:.5f}) is {distance:.0f}m from center — "
                f"outside the {max_radius_m:.0f}m geofence.")
    if max_altitude_m is not None and alt is not None and alt > max_altitude_m:
        return (False,
                f"altitude {alt:.0f}m exceeds the ceiling of {max_altitude_m:.0f}m.")
    return (True, "")
