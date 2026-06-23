"""
Tests for the telemetry loop closure: altitude-based arrival confirmation (take_off)
and the unified arrival-target mechanism in the MAVLink listener.

These cover what closes the AI->drone closed loop end to end: a take_off now confirms
by reaching the commanded altitude (not just by dispatch ACK), so the supervisor can
advance to the first waypoint. Pure logic — a fake GLOBAL_POSITION_INT message drives
the listener; no socket, no SITL.
"""

import queue

from dosync.adapters.mavlink import (
    MAVLinkTelemetryListener, _TAKEOFF_ARRIVAL_FRACTION, _WAYPOINT_ARRIVAL_RADIUS_M,
)
from dosync.reconciler import TelemetryEvent

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


CLAT, CLON = -31.4201, -64.1888


class _GPI:
    """A fake GLOBAL_POSITION_INT message. lat/lon in 1e7 deg, relative_alt in mm."""
    def __init__(self, lat=CLAT, lon=CLON, alt_m=0.0):
        self.lat = int(lat * 1e7)
        self.lon = int(lon * 1e7)
        self.relative_alt = int(alt_m * 1000)

    def get_type(self):
        return "GLOBAL_POSITION_INT"


def _listener():
    q = queue.Queue()
    lis = MAVLinkTelemetryListener(
        device_id="drone-01", connection_factory=lambda: None, out_queue=q)
    return lis, q


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get())
    return events


# ── Altitude arrival (take_off) ───────────────────────────────────────────────

def test_takeoff_target_finishes_at_altitude():
    lis, q = _listener()
    lis.set_arrival_target("altitude", 30.0)
    lis._check_arrival(_GPI(alt_m=29.0))  # 29/30 = 96.7% >= 95% → arrived
    events = _drain(q)
    check("reaching ~target altitude emits FINISHED",
          any(e[1] == TelemetryEvent.FINISHED for e in events))
    check("target cleared after arrival", lis._get_arrival_target() is None)


def test_takeoff_not_finished_below_threshold():
    lis, q = _listener()
    lis.set_arrival_target("altitude", 30.0)
    lis._check_arrival(_GPI(alt_m=20.0))  # 20/30 = 66% < 95% → not yet
    check("below altitude threshold emits nothing", q.empty())
    check("target still pending", lis._get_arrival_target() is not None)


def test_takeoff_finishes_only_once():
    lis, q = _listener()
    lis.set_arrival_target("altitude", 30.0)
    lis._check_arrival(_GPI(alt_m=30.0))  # arrives
    lis._check_arrival(_GPI(alt_m=30.0))  # already cleared → nothing more
    events = _drain(q)
    check("altitude arrival fires exactly once",
          sum(1 for e in events if e[1] == TelemetryEvent.FINISHED) == 1)


def test_takeoff_fraction_boundary():
    lis, q = _listener()
    lis.set_arrival_target("altitude", 100.0)
    # Exactly at the fraction boundary should count as arrived.
    lis._check_arrival(_GPI(alt_m=100.0 * _TAKEOFF_ARRIVAL_FRACTION))
    check("exactly at arrival fraction counts as arrived", not q.empty())


# ── Position arrival still works (unchanged behavior via unified target) ───────

def test_position_target_still_finishes():
    lis, q = _listener()
    lis.set_arrival_target("position", (CLAT, CLON))
    lis._check_arrival(_GPI(lat=CLAT, lon=CLON, alt_m=30))  # at the point
    events = _drain(q)
    check("position arrival still emits FINISHED",
          any(e[1] == TelemetryEvent.FINISHED for e in events))


def test_position_far_no_finish():
    lis, q = _listener()
    lis.set_arrival_target("position", (CLAT, CLON))
    lis._check_arrival(_GPI(lat=-31.465, lon=CLON, alt_m=30))  # ~5km away
    check("far from position emits nothing", q.empty())


# ── Single active target (the sequencing invariant) ───────────────────────────

def test_setting_new_target_replaces_old():
    lis, _ = _listener()
    lis.set_arrival_target("altitude", 30.0)
    lis.set_arrival_target("position", (CLAT, CLON))
    target = lis._get_arrival_target()
    check("a new target replaces the previous one", target[0] == "position")


def test_altitude_target_not_triggered_by_position_message_alone():
    # An altitude target should NOT be satisfied by horizontal position; it needs
    # the altitude field to cross the threshold.
    lis, q = _listener()
    lis.set_arrival_target("altitude", 30.0)
    lis._check_arrival(_GPI(lat=CLAT, lon=CLON, alt_m=0.0))  # at home, on the ground
    check("altitude target ignores ground-level position", q.empty())


# ── Backward-compatible accessors ─────────────────────────────────────────────

def test_get_destination_returns_position_target():
    lis, _ = _listener()
    lis.set_arrival_target("position", (CLAT, CLON))
    check("_get_destination returns the position tuple",
          lis._get_destination() == (CLAT, CLON))


def test_get_destination_none_for_altitude_target():
    lis, _ = _listener()
    lis.set_arrival_target("altitude", 30.0)
    check("_get_destination is None when target is an altitude",
          lis._get_destination() is None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} telemetry-loop-closure tests passed.")
    if _FAIL:
        raise SystemExit(1)
