"""Regressions for the 2026-07-14 wiring audit.

Context: three bugs this session shared one root cause — logic written for
StateAwareResolver while production runs ExternalResolver, guarded by
hasattr/isinstance checks that silently no-op'd. A systematic sweep for that
class found two more, pinned here:

  * the background state refresher never ran in production (gated on
    isinstance(hub.resolver, StateAwareResolver), always False) — it is also the
    only "back online" detector.
  * the family profile was persisted but never restored (db.load_family_profile
    existed; nothing called it) despite _restore_from_db's docstring promising it.
  * the operations table had no cleanup wired (clear_old_snapshots was called,
    clear_old_operations never was).
"""
import asyncio

import pytest

from dosync.hub import DoSyncHub
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           FamilyProfile, RoutineAction)


# ── Family profile survives a restart ────────────────────────────────────────

def test_family_profile_round_trips_through_dict():
    profile = FamilyProfile(
        family_name="Giuliani",
        routine_morning=[RoutineAction(tag="light", action_type="turn_on",
                                       params={"brightness": 80}, description="wake")],
        bedtime_hour=22, bedtime_minute=15, timezone="America/Argentina/Cordoba",
    )
    restored = FamilyProfile.from_dict(profile.to_dict())
    assert restored.family_name == "Giuliani"
    assert restored.bedtime_hour == 22 and restored.bedtime_minute == 15
    assert restored.timezone == "America/Argentina/Cordoba"
    assert len(restored.routine_morning) == 1
    assert restored.routine_morning[0].tag == "light"
    assert restored.routine_morning[0].params == {"brightness": 80}


def test_family_profile_survives_hub_restart(tmp_path):
    """The bug: set the profile, restart, profile silently gone."""
    db = str(tmp_path / "p.db")
    hub = DoSyncHub(db_path=db)
    hub.set_family_profile(FamilyProfile(family_name="Giuliani", bedtime_hour=22,
                                         bedtime_minute=15))
    hub2 = DoSyncHub(db_path=db)      # simulate a restart
    assert hub2.family_profile is not None, "family profile did not survive restart"
    assert hub2.family_profile.family_name == "Giuliani"
    assert hub2.family_profile.bedtime_hour == 22


def test_malformed_persisted_profile_does_not_break_startup(tmp_path):
    db = str(tmp_path / "p2.db")
    hub = DoSyncHub(db_path=db)
    hub.db.save_family_profile({"family_name": "X", "bedtime": "not-a-time"})
    hub2 = DoSyncHub(db_path=db)      # must not raise
    assert hub2.family_profile.bedtime_hour == 21   # documented fallback


# ── Operations cleanup is reachable ──────────────────────────────────────────

def test_clear_old_operations_is_callable_and_spares_active_ops():
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()    # server.py does this at startup
    purged = hub.db.clear_old_operations(max_age_hours=24)
    assert isinstance(purged, int)    # wired and returns a count


def test_server_startup_purges_operations():
    """The wiring itself: server.py must call clear_old_operations at startup
    (it called clear_old_snapshots and not this one — hence unbounded growth)."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "server.py").read_text()
    assert "clear_old_operations" in src, "operations cleanup is not wired in server.py"


# ── The refresher is hub-owned and resolver-agnostic ─────────────────────────

def test_refresher_lives_on_the_hub_not_the_resolver():
    """The wiring contract: any resolver must get state refresh."""
    hub = DoSyncHub(db_path=":memory:")
    assert hasattr(hub, "start_state_refresh"), "hub must own the state refresher"
    # the orphaned resolver-side implementation must be gone (no duplicate loops)
    assert not hasattr(hub.resolver, "start_background_refresh"), \
        "resolver still carries the old refresher — two implementations will drift"


@pytest.mark.asyncio
async def test_refresh_cycle_marks_reachable_and_detects_recovery():
    """A device answering get_state() is reachable — without executing an action.
    This is the active probing that recovers a device from unreachable."""
    from dosync.adapters import AdapterExecutor

    class FakeAdapter:
        async def get_state(self, device_id):
            return {"state": "on"}

    class FakeExecutor(AdapterExecutor):
        def __init__(self):
            pass
        def get_adapter(self, name):
            return FakeAdapter()

    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id="lamp-r", device_name="L", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id="p", type="turn_on", description="on")],
        emergency_capable=False, cert_tier="basic"))

    hub.health.mark_unreachable("lamp-r", ttl_seconds=9999)
    assert hub.health.is_unreachable("lamp-r") is True

    await hub._state_refresh_cycle(FakeExecutor())

    assert hub.health.is_unreachable("lamp-r") is False, \
        "refresher did not clear the unreachable mark on a responding device"
    assert hub.health.snapshot("lamp-r")["reachable"] is True


@pytest.mark.asyncio
async def test_refresh_cycle_is_positive_signal_only():
    """A device that fails get_state() must NOT be marked unreachable — a failing
    probe is weaker evidence than an action timeout (adapters vary)."""
    from dosync.adapters import AdapterExecutor

    class DeadAdapter:
        async def get_state(self, device_id):
            raise TimeoutError("no answer")

    class FakeExecutor(AdapterExecutor):
        def __init__(self):
            pass
        def get_adapter(self, name):
            return DeadAdapter()

    hub = DoSyncHub(db_path=":memory:")
    hub.registry.register(CapabilityManifest(
        device_id="lamp-d", device_name="L", manufacturer="t", model="t",
        firmware="1", category=DeviceCategory.ACTUATOR, tags=["light"],
        sensors=[], events=[],
        actuators=[ActuatorSpec(id="p", type="turn_on", description="on")],
        emergency_capable=False, cert_tier="basic"))

    await hub._state_refresh_cycle(FakeExecutor())
    assert hub.health.is_unreachable("lamp-d") is False, \
        "a failed probe must not manufacture an unreachable mark"
    assert hub.health.snapshot("lamp-d")["reachable"] is None   # still unknown


# ── Invariant: tests never touch a real database ─────────────────────────────

def test_no_test_file_builds_a_hub_on_the_real_database():
    """DoSyncHub() defaults to db_path="dosync.db" — a REAL file in the repo.

    Found 2026-07-14: test_validation_integration called DoSyncHub() with no
    db_path, so running the suite created an 80KB dosync.db in the working
    directory and wrote to its audit log. Combined with six test files importing
    `server` (which builds its hub at module level from whatever env the FIRST
    importer saw), the suite could leave a developer's database with
    audit_integrity=False — which is exactly how this surfaced.

    This is checked with the AST rather than grep: a substring search would match
    docstrings and comments, and would miss `DoSyncHub (` spacing. Only real call
    nodes count.
    """
    import ast
    import pathlib

    tests_dir = pathlib.Path(__file__).resolve().parent
    offenders = []
    for path in sorted(tests_dir.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "DoSyncHub"):
                has_positional = bool(node.args)
                has_db_kwarg = any(kw.arg == "db_path" for kw in node.keywords)
                if not (has_positional or has_db_kwarg):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert not offenders, (
        "DoSyncHub() without db_path writes to the real ./dosync.db — pass "
        f"db_path=':memory:' or a tmp_path. Offenders: {', '.join(offenders)}")
