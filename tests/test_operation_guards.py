"""
Tests for dosync/operation_guards.py — real-time in-flight guards and the GuardSet
that composes them.

Pure logic, fully offline: each guard is fed a GuardContext snapshot and checked.
"""

from dosync.operation_guards import (
    GuardContext, GuardSet, BaseGuard,
    GeofenceGuard, LinkLossGuard, ManualControlGuard, BatteryGuard, StepTimeoutGuard,
)

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


# ── GeofenceGuard ─────────────────────────────────────────────────────────────

def test_geofence_guard_inside_passes():
    g = GeofenceGuard(CLAT, CLON, 1000.0)
    ctx = GuardContext("drone-01", lat=CLAT, lon=CLON)
    check("inside perimeter passes", g.check(ctx) is None)


def test_geofence_guard_outside_fires():
    g = GeofenceGuard(CLAT, CLON, 1000.0)
    ctx = GuardContext("drone-01", lat=-31.465, lon=CLON)  # ~5km
    reason = g.check(ctx)
    check("outside perimeter fires", reason is not None)
    check("reason mentions leaving in flight", "in flight" in reason)


def test_geofence_guard_altitude_ceiling():
    g = GeofenceGuard(CLAT, CLON, 1000.0, max_altitude_m=120.0)
    ctx = GuardContext("drone-01", lat=CLAT, lon=CLON, alt=200.0)
    check("altitude over ceiling fires", g.check(ctx) is not None)


def test_geofence_guard_abstains_without_position():
    g = GeofenceGuard(CLAT, CLON, 1000.0)
    ctx = GuardContext("drone-01")  # no lat/lon this tick
    check("no position -> abstain (never invent)", g.check(ctx) is None)


# ── LinkLossGuard ─────────────────────────────────────────────────────────────

def test_link_loss_fires_after_silence():
    g = LinkLossGuard(max_silence_s=5.0)
    ctx = GuardContext("drone-01", seconds_since_telemetry=8.0)
    check("link silent too long fires", g.check(ctx) is not None)


def test_link_loss_passes_when_fresh():
    g = LinkLossGuard(max_silence_s=5.0)
    ctx = GuardContext("drone-01", seconds_since_telemetry=0.5)
    check("fresh telemetry passes", g.check(ctx) is None)


def test_link_loss_abstains_without_data():
    g = LinkLossGuard(max_silence_s=5.0)
    ctx = GuardContext("drone-01")
    check("no telemetry timing -> abstain", g.check(ctx) is None)


# ── ManualControlGuard ────────────────────────────────────────────────────────

def test_manual_control_fires_when_active():
    g = ManualControlGuard()
    ctx = GuardContext("drone-01", manual_control_active=True)
    reason = g.check(ctx)
    check("manual control fires", reason is not None)
    check("reason marks it a handover, not a fault", "handover" in reason)


def test_manual_control_passes_when_inactive():
    g = ManualControlGuard()
    ctx = GuardContext("drone-01", manual_control_active=False)
    check("no manual control passes", g.check(ctx) is None)


# ── BatteryGuard ──────────────────────────────────────────────────────────────

def test_battery_fires_below_floor():
    g = BatteryGuard(min_percent=25.0)
    ctx = GuardContext("drone-01", battery_percent=15.0)
    check("battery below floor fires", g.check(ctx) is not None)


def test_battery_passes_above_floor():
    g = BatteryGuard(min_percent=25.0)
    ctx = GuardContext("drone-01", battery_percent=80.0)
    check("battery above floor passes", g.check(ctx) is None)


def test_battery_abstains_without_data():
    g = BatteryGuard(min_percent=25.0)
    ctx = GuardContext("drone-01")
    check("no battery reading -> abstain", g.check(ctx) is None)


# ── StepTimeoutGuard ──────────────────────────────────────────────────────────

def test_step_timeout_fires():
    g = StepTimeoutGuard(max_step_s=120.0)
    ctx = GuardContext("drone-01", seconds_in_step=150.0)
    check("step running too long fires", g.check(ctx) is not None)


def test_step_timeout_passes():
    g = StepTimeoutGuard(max_step_s=120.0)
    ctx = GuardContext("drone-01", seconds_in_step=30.0)
    check("step within time passes", g.check(ctx) is None)


# ── GuardSet composition ──────────────────────────────────────────────────────

def _full_set():
    return (GuardSet()
            .add(ManualControlGuard())
            .add(GeofenceGuard(CLAT, CLON, 1000.0, max_altitude_m=120.0))
            .add(LinkLossGuard(max_silence_s=5.0))
            .add(BatteryGuard(min_percent=25.0))
            .add(StepTimeoutGuard(max_step_s=120.0)))


def test_guardset_all_pass():
    gs = _full_set()
    ctx = GuardContext("drone-01", lat=CLAT, lon=CLON, alt=30,
                       seconds_since_telemetry=0.5, manual_control_active=False,
                       battery_percent=80, seconds_in_step=10)
    check("all guards pass -> None", gs.check(ctx) is None)


def test_guardset_first_fire_wins():
    # Manual control is registered first; even with other breaches present, its
    # reason is the one returned.
    gs = _full_set()
    ctx = GuardContext("drone-01", lat=-31.99, lon=-64.99,  # outside perimeter
                       manual_control_active=True,           # AND human in control
                       battery_percent=5)                    # AND battery low
    reason = gs.check(ctx)
    check("first-registered firing guard wins", "manual_control" in reason)


def test_guardset_names_the_firing_guard():
    gs = _full_set()
    ctx = GuardContext("drone-01", lat=CLAT, lon=CLON, battery_percent=10)
    reason = gs.check(ctx)
    check("reason is prefixed with the guard name", reason.startswith("[battery]"))


def test_guardset_empty_passes():
    gs = GuardSet()
    ctx = GuardContext("drone-01")
    check("empty guard set never fires", gs.check(ctx) is None)


def test_guardset_only_registered_guards_run():
    # A deployment with no battery telemetry simply does not add BatteryGuard;
    # a low (or absent) battery reading then cannot fire anything.
    gs = GuardSet().add(GeofenceGuard(CLAT, CLON, 1000.0))
    ctx = GuardContext("drone-01", lat=CLAT, lon=CLON, battery_percent=1)
    check("unregistered guard cannot fire", gs.check(ctx) is None)


# ── make_guard_fn (adapter to the supervisor hook) ────────────────────────────

def test_make_guard_fn_adapts_to_supervisor():
    gs = _full_set()

    class FakeComp:
        composite_id = "comp_test"
        device_id = "drone-01"

    # A provider that turns the composite into a breaching context.
    def provider(comp):
        return GuardContext(comp.device_id, lat=-31.465, lon=CLON)  # outside

    guard_fn = gs.make_guard_fn(provider)
    reason = guard_fn(FakeComp())
    check("guard_fn returns the breach reason", reason is not None)
    check("guard_fn surfaces the geofence guard", "geofence_in_flight" in reason)


def test_make_guard_fn_none_context_passes():
    gs = _full_set()
    guard_fn = gs.make_guard_fn(lambda comp: None)  # provider yields nothing
    check("None context -> guard_fn returns None", guard_fn(object()) is None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{_PASS}/{_PASS + _FAIL} operation guard tests passed.")
    if _FAIL:
        raise SystemExit(1)
