"""
Tests for composition routing in DoSyncHub.execute_intent — the hub branch that sends
a composition intent (declared with composition_kind) to execute_composite_intent,
while leaving flat intents on the normal path and failing explicitly on an unknown
kind.

Real hub (in-memory DB, real PolicyEngine), simulated executor and telemetry.
"""

import asyncio

from dosync.hub import DoSyncHub
from dosync.models import Intent, IntentClass, Urgency
from dosync.policies import PolicyEngine
from dosync.operation_supervisor import SupervisorConfig

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


class _SimExecutor:
    def __init__(self):
        self.dispatched = []

    async def execute(self, action, urgency):
        self.dispatched.append(action.action)

        class _R:
            success = True
            error = None
        return _R()


def _make_hub():
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    hub.policy_engine = PolicyEngine()
    # Simulate telemetry confirming arrival on the 2nd poll of each operation.
    _polls = {}

    def fake_read(operation_id):
        row = hub.db.get_operation(operation_id)
        if row and row.get("state") in ("failed", "rejected", "cancelled", "interrupted"):
            return row.get("state")
        _polls[operation_id] = _polls.get(operation_id, 0) + 1
        return "completed" if _polls[operation_id] >= 2 else "in_progress"

    hub._read_operation_state = fake_read

    # Make the supervisor poll fast in execute_composite_intent (it builds its own
    # SupervisorConfig default of 0.2s; patch execute_composite_intent to inject a
    # fast config so tests run instantly).
    _orig = hub.execute_composite_intent

    async def fast_composite(intent, executor, context, guard_set=None, config=None):
        return await _orig(intent, executor, context,
                           guard_set=guard_set,
                           config=config or SupervisorConfig(poll_interval_s=0.001,
                                                             step_timeout_s=5.0))
    hub.execute_composite_intent = fast_composite
    return hub


def _declare_inspect_area(hub):
    hub.db.save_intent_class("inspect_area", "info", ["aerial"], ["take_off", "go_to"],
                             "Inspect an area's perimeter", "robotics",
                             composition_kind="perimeter")


def _run(coro):
    return asyncio.run(coro)


# ── Composition intents route to the composite path ───────────────────────────

def test_composition_intent_routes_to_composite():
    hub = _make_hub()
    _declare_inspect_area(hub)
    ex = _SimExecutor()
    intent = Intent(IntentClass("inspect_area"),
                    {"device_id": "drone-01", "center": (CLAT, CLON), "radius_m": 1000},
                    urgency=Urgency.INFO)
    result = _run(hub.execute_intent(intent, ex))
    check("composition intent succeeds via execute_intent", result.success)
    check("result status is the composite terminal state", result.status == "completed")
    check("the full route was dispatched",
          ex.dispatched == ["take_off", "go_to", "go_to", "go_to", "go_to", "return_home"])


def test_composition_routing_audit_trail():
    hub = _make_hub()
    _declare_inspect_area(hub)
    ex = _SimExecutor()
    intent = Intent(IntentClass("inspect_area"),
                    {"device_id": "drone-01", "center": (CLAT, CLON), "radius_m": 1000},
                    urgency=Urgency.INFO)
    _run(hub.execute_intent(intent, ex))
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("composite lifecycle is audited via execute_intent",
          "composite_started" in types and "composite_finished" in types)


# ── Flat intents are untouched ────────────────────────────────────────────────

def test_flat_intent_does_not_route_to_composite():
    hub = _make_hub()
    # A normal universal intent (notify) — no composition_kind.
    ex = _SimExecutor()
    intent = Intent(IntentClass("notify"), {"message": "hello"}, urgency=Urgency.INFO)
    result = _run(hub.execute_intent(intent, ex))
    # It went the flat path: no composite audit entries.
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("flat intent produces no composite_started",
          "composite_started" not in types)
    check("flat intent returns an IntentResult", hasattr(result, "intent_id"))


# ── Unknown composition_kind fails explicitly ─────────────────────────────────

def test_unknown_kind_fails_explicitly():
    hub = _make_hub()
    # Declare an intent with a kind the hub has no composer for.
    hub.db.save_intent_class("survey_grid", "info", ["aerial"], ["take_off"],
                             "Grid survey", "robotics", composition_kind="grid")
    ex = _SimExecutor()
    intent = Intent(IntentClass("survey_grid"),
                    {"device_id": "drone-01", "center": (CLAT, CLON), "radius_m": 1000},
                    urgency=Urgency.INFO)
    result = _run(hub.execute_intent(intent, ex))
    check("unknown kind returns failure", not result.success)
    check("unknown kind does NOT silently fall to flat path", result.status == "failed")
    check("unknown kind dispatched nothing", ex.dispatched == [])
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("unknown kind is audited", "composite_unknown_kind" in types)


# ── Missing context fails cleanly ─────────────────────────────────────────────

def test_composition_missing_device_id_fails():
    hub = _make_hub()
    _declare_inspect_area(hub)
    ex = _SimExecutor()
    intent = Intent(IntentClass("inspect_area"),
                    {"center": (CLAT, CLON), "radius_m": 1000},  # no device_id
                    urgency=Urgency.INFO)
    result = _run(hub.execute_intent(intent, ex))
    check("missing device_id returns failure", not result.success)
    types = [e.get("type") for e in hub.audit_log.entries()]
    check("rejection is audited", "composite_rejected" in types)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} composition routing tests passed.")
    if _FAIL:
        raise SystemExit(1)
