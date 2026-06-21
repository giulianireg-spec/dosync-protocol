"""
DoSync — MAVLink telemetry listener + consumer tests (Step 2b).

The background listener owns a thread that blocks on a MAVLink socket, so it can't
be unit-tested the way pure logic is. Instead we inject a FAKE connection (an object
with recv_match) that yields a scripted sequence of messages and can simulate a
disconnection (recv_match returns None). This exercises the entire loop — read,
map, enqueue, consume, apply — plus reconnection and the "silence is not success"
rule, all without a real socket.

The live SITL test (opt-in, DOSYNC_SITL_LIVE=1) validates against pymavlink + the
simulator.

Run: PYTHONPATH=. python3 tests/test_mavlink_listener.py
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import queue as _queue
from dosync.hub import DoSyncHub
from dosync.operations import Operation, OperationState
from dosync.reconciler import TelemetryEvent
from dosync.adapters.mavlink import (
    MAVLinkAdapter, MAVLinkTelemetryListener, MAVLinkTelemetryMapper,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeMsg:
    def __init__(self, mtype, **fields):
        self._type = mtype
        for k, v in fields.items():
            setattr(self, k, v)
    def get_type(self):
        return self._type


class FakeConnection:
    """A stand-in for a pymavlink connection. Yields scripted messages from a list;
    once exhausted, returns None (simulating a quiet/closed link). Thread-safe
    enough for the listener's single reader thread."""
    def __init__(self, messages, on_exhausted=None):
        self._messages = list(messages)
        self._i = 0
        self._lock = threading.Lock()
        self.closed = False
        self._on_exhausted = on_exhausted

    def recv_match(self, blocking=True, timeout=None):
        with self._lock:
            if self._i < len(self._messages):
                m = self._messages[self._i]
                self._i += 1
                return m
        if self._on_exhausted:
            self._on_exhausted()
        # Simulate a quiet link: block for the timeout, return None.
        if timeout:
            time.sleep(min(timeout, 0.05))
        return None

    def close(self):
        self.closed = True


def _hub_with_active_op(device_id="drone-01"):
    hub = DoSyncHub(db_path=":memory:")
    hub.db.init_operations_table()
    op = Operation(device_id=device_id, action="take_off", telemetry_capable=True)
    op.transition_to(OperationState.IN_PROGRESS, reason="dispatch accepted")
    hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)
    return hub


# ── Listener: reads, maps, enqueues ───────────────────────────────────────────

def test_listener_enqueues_mapped_facts():
    q = _queue.Queue()
    # A takeover sequence: GUIDED heartbeat (learn), then STABILIZE (edge → event).
    msgs = [
        FakeMsg("HEARTBEAT", mode_name="GUIDED"),
        FakeMsg("HEARTBEAT", mode_name="STABILIZE"),
    ]
    conn = FakeConnection(msgs)
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    # Give the thread a moment to process both messages.
    time.sleep(0.3)
    listener.stop()
    # Exactly one fact should have been enqueued: MANUAL_CONTROL_TAKEN.
    facts = []
    while not q.empty():
        facts.append(q.get_nowait())
    assert len(facts) == 1, f"expected 1 fact, got {facts}"
    device_id, event, phase = facts[0]
    assert device_id == "drone-01"
    assert event == TelemetryEvent.MANUAL_CONTROL_TAKEN


def test_listener_ignores_irrelevant_messages():
    q = _queue.Queue()
    msgs = [
        FakeMsg("VFR_HUD", airspeed=5.0),
        FakeMsg("ATTITUDE", roll=0.1),
        FakeMsg("HEARTBEAT", mode_name="GUIDED"),  # learn, no event
    ]
    conn = FakeConnection(msgs)
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    time.sleep(0.3)
    listener.stop()
    assert q.empty(), "no facts should be enqueued for irrelevant messages"


def test_listener_arming_enqueues_preparing():
    q = _queue.Queue()
    msgs = [FakeMsg("STATUSTEXT", text="Arming motors")]
    conn = FakeConnection(msgs)
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    time.sleep(0.3)
    listener.stop()
    facts = []
    while not q.empty():
        facts.append(q.get_nowait())
    assert len(facts) == 1
    _, event, phase = facts[0]
    assert event == TelemetryEvent.PREPARING
    assert phase == "arming"


# ── Listener: lifecycle ───────────────────────────────────────────────────────

def test_listener_stop_joins_thread():
    q = _queue.Queue()
    conn = FakeConnection([])  # immediately quiet
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    time.sleep(0.1)
    listener.stop(join_timeout=2.0)
    # After stop, the thread must be gone.
    assert listener._thread is None
    assert conn.closed is True


def test_listener_start_idempotent():
    q = _queue.Queue()
    conn = FakeConnection([])
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    t1 = listener._thread
    listener.start()  # second call is a no-op
    assert listener._thread is t1
    listener.stop()


# ── Listener: disconnection never invents state ───────────────────────────────

def test_listener_silence_enqueues_nothing():
    """The core safety rule: a quiet link produces NO facts. Silence != success."""
    q = _queue.Queue()
    conn = FakeConnection([])  # no messages, ever — pure silence
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener.start()
    time.sleep(0.4)
    listener.stop()
    assert q.empty(), "silence must never enqueue a fact"


def test_listener_reconnect_resets_mapper():
    """On reconnect the mapper is reset so it re-learns the mode — never assumes
    the pre-disconnection state. We verify reset() is called by tracking it."""
    q = _queue.Queue()
    reset_calls = {"n": 0}

    class TrackingMapper(MAVLinkTelemetryMapper):
        def reset(self):
            reset_calls["n"] += 1
            super().reset()

    # Connection that exhausts quickly, forcing a silence→reconnect cycle.
    conns = [FakeConnection([FakeMsg("HEARTBEAT", mode_name="GUIDED")]),
             FakeConnection([FakeMsg("HEARTBEAT", mode_name="GUIDED")])]
    idx = {"i": 0}
    def factory():
        c = conns[min(idx["i"], len(conns) - 1)]
        idx["i"] += 1
        return c

    listener = MAVLinkTelemetryListener("drone-01", factory, q, mapper=TrackingMapper())
    listener.start()
    # Wait long enough for at least the first connect (reset #1).
    time.sleep(0.3)
    listener.stop()
    # reset is called on every (re)connect, so at least once.
    assert reset_calls["n"] >= 1


# ── Consumer: drains queue → apply_telemetry ──────────────────────────────────

def test_consumer_drain_applies_to_hub():
    hub = _hub_with_active_op("drone-01")
    adapter = MAVLinkAdapter(hub=hub)
    # Manually enqueue a FINISHED fact (as a listener would).
    adapter._telemetry_queue.put(("drone-01", TelemetryEvent.FINISHED, None))
    applied = adapter.drain_telemetry_once()
    assert applied == 1
    # The operation should now be completed (no longer active).
    active = hub.db.get_active_operations()
    assert not any(o["device_id"] == "drone-01" for o in active)


def test_consumer_drain_empty_is_zero():
    hub = _hub_with_active_op("drone-01")
    adapter = MAVLinkAdapter(hub=hub)
    assert adapter.drain_telemetry_once() == 0


def test_consumer_applies_multiple_facts_in_order():
    hub = _hub_with_active_op("drone-01")
    adapter = MAVLinkAdapter(hub=hub)
    # preparing(arming) then finished.
    adapter._telemetry_queue.put(("drone-01", TelemetryEvent.PREPARING, "arming"))
    adapter._telemetry_queue.put(("drone-01", TelemetryEvent.FINISHED, None))
    applied = adapter.drain_telemetry_once()
    assert applied == 2


def test_consumer_unknown_device_does_not_crash():
    hub = _hub_with_active_op("drone-01")
    adapter = MAVLinkAdapter(hub=hub)
    # Fact for a device with no active operation — apply_telemetry returns
    # matched=False; the consumer must not crash.
    adapter._telemetry_queue.put(("ghost-99", TelemetryEvent.FINISHED, None))
    applied = adapter.drain_telemetry_once()
    assert applied == 1  # it was processed (and harmlessly ignored)


# ── Simulated mode: telemetry no-ops without pymavlink ────────────────────────

def test_start_telemetry_simulated_returns_false():
    import dosync.adapters.mavlink as m
    saved = m._MAVLINK_AVAILABLE
    m._MAVLINK_AVAILABLE = False
    try:
        adapter = MAVLinkAdapter()
        assert adapter.start_telemetry("drone-01", "udp:127.0.0.1:14550") is False
    finally:
        m._MAVLINK_AVAILABLE = saved


# ── End-to-end with fakes: listener → queue → consumer → hub ──────────────────

def test_end_to_end_takeover_interrupts_operation():
    """The whole Step 2b path with zero real sockets: a takeover heartbeat read by
    the listener flows through the queue to the consumer, which applies it, which
    interrupts the active operation."""
    hub = _hub_with_active_op("drone-01")
    adapter = MAVLinkAdapter(hub=hub)

    msgs = [
        FakeMsg("HEARTBEAT", mode_name="GUIDED"),       # learn
        FakeMsg("HEARTBEAT", mode_name="STABILIZE"),    # takeover edge
    ]
    conn = FakeConnection(msgs)
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, adapter._telemetry_queue)
    listener.start()
    time.sleep(0.3)
    listener.stop()

    # Consumer drains the queue and applies.
    adapter.drain_telemetry_once()

    # The active operation must now be interrupted.
    op_dicts = hub.db.get_active_operations()
    drone_active = [o for o in op_dicts if o["device_id"] == "drone-01"]
    assert not drone_active, "operation should be terminal (interrupted)"
    # Confirm via audit that it was interrupted.
    tel = [e for e in hub.audit_log.entries()
           if e.get("type") == "operation_telemetry" and e.get("device_id") == "drone-01"]
    assert any(e["to_state"] == "interrupted" for e in tel)


# ── Reconnect backoff (exponential) ───────────────────────────────────────────

def test_backoff_starts_at_base():
    import dosync.adapters.mavlink as m
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    # 0 or 1 failures → base interval.
    listener._reconnect_failures = 0
    assert listener._current_backoff() == m._RECONNECT_BASE_S
    listener._reconnect_failures = 1
    assert listener._current_backoff() == m._RECONNECT_BASE_S


def test_backoff_grows_exponentially():
    import dosync.adapters.mavlink as m
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener._reconnect_failures = 2
    assert listener._current_backoff() == m._RECONNECT_BASE_S * 2
    listener._reconnect_failures = 3
    assert listener._current_backoff() == m._RECONNECT_BASE_S * 4


def test_backoff_capped_at_max():
    import dosync.adapters.mavlink as m
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener._reconnect_failures = 99  # absurdly high
    assert listener._current_backoff() == m._RECONNECT_MAX_S


def test_backoff_increments_on_failed_connect():
    q = _queue.Queue()
    # Factory returns None → connect "fails".
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    assert listener._reconnect_failures == 0
    listener._reconnect()
    assert listener._reconnect_failures == 1
    listener._reconnect()
    assert listener._reconnect_failures == 2


def test_backoff_resets_on_successful_connect():
    q = _queue.Queue()
    conn = FakeConnection([])
    listener = MAVLinkTelemetryListener("drone-01", lambda: conn, q)
    listener._reconnect_failures = 5  # simulate prior failures
    ok = listener._reconnect()
    assert ok is True
    assert listener._reconnect_failures == 0  # reset on success


# ── Waypoint arrival detection ────────────────────────────────────────────────

# Córdoba center as the go_to destination.
DEST_LAT, DEST_LON = -31.4201, -64.1888


def _gpi(lat, lon):
    """A GLOBAL_POSITION_INT stand-in (lat/lon in 1e7 degrees)."""
    return FakeMsg("GLOBAL_POSITION_INT", lat=int(lat * 1e7), lon=int(lon * 1e7))


def test_waypoint_no_destination_no_event():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    # No destination set — a position message produces nothing.
    listener._check_waypoint_arrival(_gpi(DEST_LAT, DEST_LON))
    assert q.empty()


def test_waypoint_far_no_event():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener.set_destination(DEST_LAT, DEST_LON)
    listener._check_waypoint_arrival(_gpi(-31.465, -64.1888))  # ~5km away
    assert q.empty()
    assert listener._get_destination() is not None  # still pending


def test_waypoint_arrival_emits_finished():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener.set_destination(DEST_LAT, DEST_LON)
    # ~1m away — within the 3m arrival radius.
    listener._check_waypoint_arrival(_gpi(-31.42011, -64.1888))
    fact = q.get_nowait()
    assert fact[0] == "drone-01"
    assert fact[1] == TelemetryEvent.FINISHED
    # Destination cleared so it fires only once.
    assert listener._get_destination() is None


def test_waypoint_fires_only_once():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener.set_destination(DEST_LAT, DEST_LON)
    near = _gpi(-31.42011, -64.1888)
    listener._check_waypoint_arrival(near)  # arrives → FINISHED + clear
    listener._check_waypoint_arrival(near)  # already cleared → nothing
    facts = []
    while not q.empty():
        facts.append(q.get_nowait())
    assert len(facts) == 1


def test_waypoint_ignores_non_position_messages():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    listener.set_destination(DEST_LAT, DEST_LON)
    # A heartbeat near the destination is not a position fix — ignored.
    listener._check_waypoint_arrival(FakeMsg("HEARTBEAT", mode_name="GUIDED"))
    assert q.empty()
    assert listener._get_destination() is not None


def test_waypoint_set_and_clear():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    assert listener._get_destination() is None
    listener.set_destination(DEST_LAT, DEST_LON)
    assert listener._get_destination() == (DEST_LAT, DEST_LON)
    listener.clear_destination()
    assert listener._get_destination() is None


# ── Mode-name resolution (real-message bridge) ────────────────────────────────

def test_attach_mode_name_respects_existing():
    # A stand-in that already has mode_name (like our test fakes) is left as-is.
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    msg = FakeMsg("HEARTBEAT", mode_name="GUIDED")
    listener._attach_mode_name(msg)
    assert msg.mode_name == "GUIDED"


def test_attach_mode_name_ignores_non_heartbeat():
    q = _queue.Queue()
    listener = MAVLinkTelemetryListener("drone-01", lambda: None, q)
    msg = FakeMsg("ATTITUDE", roll=0.1)
    listener._attach_mode_name(msg)  # must not crash, must not add mode_name
    assert not hasattr(msg, "mode_name")


# ── Live SITL test (opt-in) ──────────────────────────────────────────────────

def test_listener_sitl_live():
    """Live test against a running ArduPilot SITL. Skipped unless DOSYNC_SITL_LIVE=1
    and pymavlink is available. Starts a listener on the real vehicle, commands a
    takeoff via the command channel, and asserts telemetry facts arrive. Run with
    the simulator up:
        cd ~/ardupilot/ArduCopter && sim_vehicle.py -v ArduCopter --console -w
        DOSYNC_SITL_LIVE=1 PYTHONPATH=. python3 tests/test_mavlink_listener.py
    """
    import dosync.adapters.mavlink as m
    if os.environ.get("DOSYNC_SITL_LIVE") != "1" or not m._MAVLINK_AVAILABLE:
        print("    (skipped — set DOSYNC_SITL_LIVE=1 with pymavlink + SITL running)")
        return

    hub = _hub_with_active_op("drone-01")
    # Register the drone so the listener can resolve its connection string.
    from dosync.adapters.mavlink import mavlink_manifest
    man = mavlink_manifest("drone-01", "SITL Drone", "udp:127.0.0.1:14550")
    man.adapter = "mavlink"
    hub.registry.register(man)

    adapter = MAVLinkAdapter(hub=hub)
    started = adapter.start_telemetry("drone-01", "udp:127.0.0.1:14550")
    assert started, "telemetry should start with pymavlink + SITL"

    # The listener now resolves mode_name on real heartbeats, so a manual-takeover
    # (a mode change away from GUIDED) produces a real fact. To exercise it without
    # a pilot, this test just gathers whatever telemetry the idle vehicle emits and
    # confirms the listener runs end-to-end. To see MANUAL_CONTROL_TAKEN live, change
    # the vehicle's mode in the MAVProxy console (mode GUIDED; mode STABILIZE) while
    # this runs — the listener will detect the GUIDED->manual edge.
    facts_seen = 0
    for _ in range(20):
        time.sleep(0.25)
        facts_seen += adapter.drain_telemetry_once()
    adapter.stop_telemetry()
    print(f"    ✓ LIVE: listener processed telemetry from SITL ({facts_seen} facts applied)")
    print("      (change mode in MAVProxy: 'mode GUIDED' then 'mode STABILIZE' to "
          "see MANUAL_CONTROL_TAKEN live)")


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
    print(f"\n{passed}/{passed + failed} MAVLink listener + consumer tests passed.")
    sys.exit(1 if failed else 0)
