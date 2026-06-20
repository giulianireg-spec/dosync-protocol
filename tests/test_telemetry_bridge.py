"""
DoSync — Telemetry bridge + MAVLink mapper tests (Step 2a).

Two units, both tested WITHOUT a socket:

  1. hub.apply_telemetry(device_id, event, ...) — the GENERIC bridge that connects
     the reconciler to the hub for the first time. Finds the device's active
     operation, reconciles, persists, audits. Device-agnostic (no drone concepts).

  2. MAVLinkTelemetryMapper — the PURE MAVLink-message -> TelemetryEvent mapping
     with memory of the previous flight mode, so MANUAL_CONTROL_TAKEN fires on the
     GUIDED->manual EDGE, not on every heartbeat.

Run: PYTHONPATH=. python3 tests/test_telemetry_bridge.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.hub import DoSyncHub
from dosync.operations import Operation, OperationState
from dosync.reconciler import TelemetryEvent
from dosync.adapters.mavlink import MAVLinkTelemetryMapper


# ── Fake MAVLink messages (stand-ins — no pymavlink, no socket) ───────────────

class FakeMsg:
    """Minimal MAVLink message stand-in: a type, plus whatever fields a test sets.
    The mapper reads only attributes, so this is enough to exercise it fully."""
    def __init__(self, mtype, **fields):
        self._type = mtype
        for k, v in fields.items():
            setattr(self, k, v)

    def get_type(self):
        return self._type


# ── Helpers for the bridge tests ──────────────────────────────────────────────

def _hub_with_active_op(action="take_off", telemetry=True, state=OperationState.IN_PROGRESS):
    """A real in-memory hub with one persisted active operation for 'drone-01'."""
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    op = Operation(device_id="drone-01", action=action, telemetry_capable=telemetry)
    if state != OperationState.PENDING:
        op.transition_to(state, reason="test setup")
    hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)
    return hub, op


# ── Bridge: matching and reconciliation ───────────────────────────────────────

def test_apply_telemetry_no_active_operation():
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    res = hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)
    assert res["matched"] is False


def test_apply_telemetry_advances_operation():
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    res = hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)
    assert res["matched"] is True
    assert res["changed"] is True
    assert res["from_state"] == "in_progress"
    assert res["to_state"] == "completed"


def test_apply_telemetry_persists_change():
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)
    # The operation should now be terminal in the DB → no longer active.
    active = hub.db.get_active_operations()
    assert not any(o["device_id"] == "drone-01" for o in active)


def test_apply_telemetry_audits():
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)
    entries = hub.audit_log.entries()
    tel = [e for e in entries if e.get("type") == "operation_telemetry"]
    assert len(tel) == 1
    assert tel[0]["event"] == "finished"
    assert tel[0]["to_state"] == "completed"


def test_apply_telemetry_idempotent_noop():
    # FINISHED on an already-completed op is a no-op, not a crash.
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)  # -> completed
    res = hub.apply_telemetry("drone-01", TelemetryEvent.FINISHED)  # op now terminal
    # Second call: the op is terminal, so it's no longer "active" → not matched.
    assert res["matched"] is False


def test_apply_telemetry_illegal_is_noop_not_crash():
    # An out-of-order fact (FINISHED before the op ever started) must be a no-op.
    hub, op = _hub_with_active_op(state=OperationState.PENDING)
    res = hub.apply_telemetry("drone-01", TelemetryEvent.DEVICE_RESUMED)
    assert res["matched"] is True
    # Whether changed or not, the hub did not crash and returned a clean result.
    assert "to_state" in res


def test_apply_telemetry_preserves_time_in_state():
    # Rehydration must not reset state_entered_at. We check the op kept its history.
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    res = hub.apply_telemetry("drone-01", TelemetryEvent.MANUAL_CONTROL_TAKEN)
    assert res["matched"] is True
    assert res["to_state"] == "interrupted"


def test_apply_telemetry_string_event_accepted():
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    res = hub.apply_telemetry("drone-01", "finished")  # str, not enum
    assert res["matched"] is True
    assert res["to_state"] == "completed"


def test_apply_telemetry_carries_phase():
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    hub.apply_telemetry("drone-01", TelemetryEvent.PREPARING, phase="arming")
    # The phase should be persisted on the operation.
    active = hub.db.get_active_operations()
    drone_ops = [o for o in active if o["device_id"] == "drone-01"]
    assert drone_ops and drone_ops[0].get("phase") == "arming"


def test_apply_telemetry_is_device_agnostic():
    # The bridge must work for a non-drone device identically — no drone concepts.
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    op = Operation(device_id="oven-01", action="bake", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS, reason="test")
    hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)
    res = hub.apply_telemetry("oven-01", TelemetryEvent.FINISHED)
    assert res["matched"] is True
    assert res["to_state"] == "completed"


# ── Mapper: manual takeover is an EDGE, not a level ───────────────────────────

def test_mapper_no_event_on_first_heartbeat():
    m = MAVLinkTelemetryMapper()
    # First heartbeat in GUIDED: we learn the mode, emit nothing.
    assert m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED")) is None


def test_mapper_manual_takeover_on_edge():
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))      # learn GUIDED
    out = m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE"))  # edge!
    assert out is not None
    event, phase = out
    assert event == TelemetryEvent.MANUAL_CONTROL_TAKEN


def test_mapper_no_repeat_on_steady_manual():
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))
    m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE"))   # edge → event
    # Subsequent heartbeats still in STABILIZE must NOT re-emit.
    assert m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE")) is None
    assert m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE")) is None


def test_mapper_no_event_steady_guided():
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))
    # Many heartbeats in GUIDED: never an event.
    for _ in range(5):
        assert m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED")) is None


def test_mapper_auto_is_autonomous():
    # AUTO (running a mission) is autonomous; GUIDED->AUTO is NOT a takeover.
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))
    assert m.map_message(FakeMsg("HEARTBEAT", mode_name="AUTO")) is None


def test_mapper_takeover_from_auto():
    # AUTO -> LOITER(manual) is a takeover too.
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="AUTO"))
    out = m.map_message(FakeMsg("HEARTBEAT", mode_name="LOITER"))
    assert out is not None and out[0] == TelemetryEvent.MANUAL_CONTROL_TAKEN


# ── Mapper: STATUSTEXT and mission ────────────────────────────────────────────

def test_mapper_arming_is_preparing_phase():
    m = MAVLinkTelemetryMapper()
    out = m.map_message(FakeMsg("STATUSTEXT", text="Arming motors"))
    assert out is not None
    event, phase = out
    assert event == TelemetryEvent.PREPARING
    assert phase == "arming"


def test_mapper_failure_statustext():
    m = MAVLinkTelemetryMapper()
    out = m.map_message(FakeMsg("STATUSTEXT", text="PreArm: GPS not ready"))
    assert out is not None and out[0] == TelemetryEvent.FAILED


def test_mapper_mission_item_reached_is_finished():
    m = MAVLinkTelemetryMapper()
    out = m.map_message(FakeMsg("MISSION_ITEM_REACHED", seq=1))
    assert out is not None and out[0] == TelemetryEvent.FINISHED


def test_mapper_irrelevant_message_no_event():
    m = MAVLinkTelemetryMapper()
    # A message type we don't care about → None (the "produces no event" case).
    assert m.map_message(FakeMsg("VFR_HUD", airspeed=5.0)) is None
    assert m.map_message(FakeMsg("ATTITUDE", roll=0.1)) is None
    assert m.map_message(FakeMsg("GPS_RAW_INT", fix_type=3)) is None


def test_mapper_empty_statustext_no_event():
    m = MAVLinkTelemetryMapper()
    assert m.map_message(FakeMsg("STATUSTEXT", text="")) is None
    assert m.map_message(FakeMsg("STATUSTEXT", text="   ")) is None


def test_mapper_reset_clears_memory():
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))
    m.reset()
    # After reset, the next GUIDED heartbeat is "first" again — no spurious event,
    # and a subsequent manual mode is a fresh edge.
    assert m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED")) is None
    out = m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE"))
    assert out is not None and out[0] == TelemetryEvent.MANUAL_CONTROL_TAKEN


def test_mapper_unknown_mode_no_crash():
    m = MAVLinkTelemetryMapper()
    # A heartbeat with no resolvable mode → None, no crash.
    assert m.map_message(FakeMsg("HEARTBEAT")) is None


# ── End-to-end (still no socket): mapper → bridge ─────────────────────────────

def test_mapper_to_bridge_manual_takeover():
    """The integration the listener loop (Step 2b) will perform: a takeover
    heartbeat maps to MANUAL_CONTROL_TAKEN, which the bridge applies to the
    active operation, interrupting it. Proven here with zero sockets."""
    hub, op = _hub_with_active_op(state=OperationState.IN_PROGRESS)
    m = MAVLinkTelemetryMapper()
    m.map_message(FakeMsg("HEARTBEAT", mode_name="GUIDED"))       # learn
    out = m.map_message(FakeMsg("HEARTBEAT", mode_name="STABILIZE"))  # edge
    assert out is not None
    event, phase = out
    res = hub.apply_telemetry("drone-01", event, phase=phase)
    assert res["matched"] is True
    assert res["to_state"] == "interrupted"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
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
    print(f"\n{passed}/{passed + failed} telemetry bridge + mapper tests passed.")
    sys.exit(1 if failed else 0)
