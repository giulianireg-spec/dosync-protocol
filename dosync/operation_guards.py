"""
DoSync — Operation guards (real-time in-flight monitoring for composite operations).
====================================================================================

The PolicyEngine is PRE-FLIGHT AUTHORIZATION: it judges a plan once, before dispatch,
and answers ALLOW/BLOCK/CONFIRM/MODIFY. These guards are IN-FLIGHT MONITORING: the
supervisor calls them on every tick while a composite operation is live, and they
answer "keep going" (None) or "abort, here's why" (a reason string). Two different
safety systems for two different moments — the panel was explicit that they must not
be merged (incompatible contracts), but they SHARE the geofence calculation (in
dosync/geo.py) so the formula never lives in two copies.

This is the brain watching the body's vital signs during the trip — not the
environment (no obstacle/“tree” sensing; that needs sensors the body does not have
yet), but everything the vehicle itself reports: position (still inside the fence?),
link (still alive?), mode (did a human take over?), battery, time-in-state.

DESIGN (validated by the expert panel):
  * Each guard is an independent, composable object with a name and a check(comp)
    method returning a reason-or-None. A GuardSet runs them in order; the first to
    fire wins (like the PolicyEngine's first-block-wins, shared PATTERN, separate
    component).
  * The guards consume a CONTEXT object the supervisor refreshes from reconciled
    state + telemetry each tick — they never read the raw telemetry queue (that is
    the consumer's job; guards are decision, not perception).
  * First version: DETECT and report a clear abort reason. Differentiated REACTIONS
    (link-loss → return home; human took control → let go; battery → land here) are
    a documented second layer; the supervisor's safe default today is to abort, and
    the caller routes the vehicle home. Keeping the first version to "detect + abort
    with a clear reason" is honest and already large.
  * The human-takeover guard is special and the panel flagged it: when a human is
    flying, the right behavior is NOT to fight for control. The guard fires (so the
    supervisor stops dispatching), and the safe meaning is "let go," distinct from a
    fault. This is recorded in the reason so the second-layer reaction can tell them
    apart later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geo import is_within_perimeter


@dataclass
class GuardContext:
    """A snapshot the supervisor refreshes each tick from reconciled state +
    telemetry. The guards read THIS, never the raw queue. Every field is optional so
    a deployment that cannot supply a signal simply leaves it None and the
    corresponding guard abstains (it never invents a reading)."""
    device_id: str
    # Current reported position (from telemetry). None = unknown this tick.
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    # Seconds since the last telemetry message arrived. None = unknown.
    seconds_since_telemetry: Optional[float] = None
    # Whether a human currently holds manual control (operation interrupted).
    manual_control_active: bool = False
    # Battery remaining, 0..100. None = unknown.
    battery_percent: Optional[float] = None
    # Seconds the current mission step has been running. None = unknown.
    seconds_in_step: Optional[float] = None


class BaseGuard:
    """A composable in-flight guard. Subclasses implement check(ctx) -> reason|None."""

    @property
    def name(self) -> str:
        raise NotImplementedError

    def check(self, ctx: GuardContext) -> Optional[str]:
        raise NotImplementedError


class GeofenceGuard(BaseGuard):
    """Fires if the vehicle's REPORTED position leaves the permitted perimeter.

    This is the in-flight twin of GeofencePolicy. The policy checks a go_to's TARGET
    before dispatch; this checks where the vehicle ACTUALLY is, every tick — catching
    a vehicle blown off course by wind, GPS drift, or any drift the pre-flight check
    could not foresee. Both call the same is_within_perimeter (dosync/geo.py): one
    rule, two moments (admission + monitoring). Cinturón y airbag.
    """

    def __init__(self, center_lat: float, center_lon: float,
                 max_radius_m: float, max_altitude_m: Optional[float] = None):
        self._center_lat = center_lat
        self._center_lon = center_lon
        self._max_radius_m = max_radius_m
        self._max_altitude_m = max_altitude_m

    @property
    def name(self) -> str:
        return "geofence_in_flight"

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.lat is None or ctx.lon is None:
            return None  # no position this tick — abstain, never invent
        ok, reason = is_within_perimeter(
            ctx.lat, ctx.lon, self._center_lat, self._center_lon,
            self._max_radius_m, alt=ctx.alt, max_altitude_m=self._max_altitude_m)
        if not ok:
            return f"vehicle left the perimeter in flight: {reason}"
        return None


class LinkLossGuard(BaseGuard):
    """Fires if telemetry has gone silent for longer than the allowed gap — the link
    is presumed lost. Silence is not success: a vehicle we cannot hear is a vehicle
    we are not supervising, and the safe response is to stop and let it come home."""

    def __init__(self, max_silence_s: float = 5.0):
        self._max_silence_s = max_silence_s

    @property
    def name(self) -> str:
        return "link_loss"

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.seconds_since_telemetry is None:
            return None
        if ctx.seconds_since_telemetry > self._max_silence_s:
            return (f"telemetry link silent for {ctx.seconds_since_telemetry:.0f}s "
                    f"(> {self._max_silence_s:.0f}s) — link presumed lost")
        return None


class ManualControlGuard(BaseGuard):
    """Fires when a human has taken manual control. The panel flagged this as
    special: the right behavior is NOT to fight the pilot. The guard fires so the
    supervisor stops dispatching; the reason marks it as a handover (not a fault) so a
    later reaction layer can 'let go' rather than 'return home'."""

    @property
    def name(self) -> str:
        return "manual_control"

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.manual_control_active:
            return ("human took manual control — DoSync yields (handover, not a "
                    "fault); stop dispatching and let the pilot fly")
        return None


class BatteryGuard(BaseGuard):
    """Fires when battery falls below the safe threshold. The vehicle needs enough
    charge to get home; below the floor, the mission must stop."""

    def __init__(self, min_percent: float = 25.0):
        self._min_percent = min_percent

    @property
    def name(self) -> str:
        return "battery"

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.battery_percent is None:
            return None
        if ctx.battery_percent < self._min_percent:
            return (f"battery {ctx.battery_percent:.0f}% below the {self._min_percent:.0f}% "
                    f"floor — not enough charge to continue safely")
        return None


class StepTimeoutGuard(BaseGuard):
    """Fires when a single step has run longer than allowed — a stalled step that the
    supervisor's own backstop would also catch, surfaced here as a named guard so the
    reason is explicit. Belt for the supervisor's suspenders."""

    def __init__(self, max_step_s: float = 120.0):
        self._max_step_s = max_step_s

    @property
    def name(self) -> str:
        return "step_timeout"

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.seconds_in_step is None:
            return None
        if ctx.seconds_in_step > self._max_step_s:
            return (f"step running for {ctx.seconds_in_step:.0f}s "
                    f"(> {self._max_step_s:.0f}s) — stalled")
        return None


@dataclass
class GuardSet:
    """An ordered, composable set of guards. Runs them in order on each tick; the
    first to fire wins (shared pattern with the PolicyEngine's first-block-wins,
    separate component). A deployment registers only the guards it wants — a vehicle
    with no battery telemetry simply does not add a BatteryGuard.

    The GuardSet exposes a `make_guard_fn(context_provider)` that adapts it to the
    supervisor's GuardFn hook: the supervisor passes a CompositeOperation, the
    provider turns it (plus live telemetry) into a GuardContext, and the set runs.
    """
    guards: list = field(default_factory=list)

    def add(self, guard: BaseGuard) -> "GuardSet":
        self.guards.append(guard)
        return self

    def check(self, ctx: GuardContext) -> Optional[str]:
        """Run all guards in order; return the first firing guard's reason (prefixed
        with its name), or None if all pass."""
        for guard in self.guards:
            reason = guard.check(ctx)
            if reason is not None:
                return f"[{guard.name}] {reason}"
        return None

    def make_guard_fn(self, context_provider):
        """Adapt this set to the supervisor's GuardFn(comp) -> reason|None.

        context_provider: a callable taking the live CompositeOperation and returning
        a GuardContext (built from reconciled state + the latest telemetry). Keeping
        this injection here means the GuardSet stays pure and testable: tests supply a
        trivial provider; production supplies one that reads live telemetry.
        """
        def guard_fn(comp) -> Optional[str]:
            ctx = context_provider(comp)
            if ctx is None:
                return None
            return self.check(ctx)
        return guard_fn
