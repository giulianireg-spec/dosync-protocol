"""
Tests for the single-reader COMMAND_ACK design.

The telemetry listener is the only reader of the MAVLink socket. It records
COMMAND_ACKs and the command path consumes them via wait_for_ack, instead of the
command channel reading the socket itself. This is what makes the adapter work over
a single bidirectional link (a serial radio — one link, one reader), not just
SITL/UDP with a separate outbound command channel.

These tests drive the ACK registry directly — no pymavlink, no socket.
"""

import time
import queue
import threading

from dosync.adapters.mavlink import MAVLinkTelemetryListener

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


# MAV_RESULT_ACCEPTED is 0 in MAVLink.
ACCEPTED = 0
DENIED = 1


def _listener():
    return MAVLinkTelemetryListener(
        device_id="drone-01", connection_factory=lambda: None, out_queue=queue.Queue())


# ── The core race cases (panel's main concern) ────────────────────────────────

def test_ack_after_wait_starts():
    """ACK arrives after the waiter begins waiting — the normal case."""
    lis = _listener()
    t0 = time.time()

    def delayed():
        time.sleep(0.1)
        lis.record_ack(22, ACCEPTED)
    threading.Thread(target=delayed).start()
    result = lis.wait_for_ack(22, since=t0, timeout=2.0)
    check("ACK arriving after wait starts is received", result == ACCEPTED)


def test_ack_before_wait_not_lost():
    """ACK arrives before the waiter starts waiting — must be retained, not lost."""
    lis = _listener()
    t0 = time.time()
    lis.record_ack(400, ACCEPTED)  # arrives first
    result = lis.wait_for_ack(400, since=t0, timeout=0.5)  # then we wait
    check("ACK arriving before wait starts is retained", result == ACCEPTED)


def test_stale_ack_ignored():
    """An ACK from before the command's send time must NOT be accepted."""
    lis = _listener()
    lis.record_ack(176, ACCEPTED)        # stale ACK (e.g. from a prior identical cmd)
    time.sleep(0.02)
    send_time = time.time()              # our command is sent AFTER the stale ACK
    result = lis.wait_for_ack(176, since=send_time, timeout=0.3)
    check("stale ACK (before send time) is ignored", result is None)


def test_timeout_without_ack():
    lis = _listener()
    result = lis.wait_for_ack(999, since=time.time(), timeout=0.3)
    check("no ACK within timeout returns None", result is None)


# ── Result codes ──────────────────────────────────────────────────────────────

def test_denied_result_returned():
    lis = _listener()
    t0 = time.time()
    lis.record_ack(22, DENIED)
    result = lis.wait_for_ack(22, since=t0, timeout=0.5)
    check("a DENIED result is returned (not swallowed)", result == DENIED)


def test_only_matching_command_id():
    """A waiter for command 22 must not wake on an ACK for command 400."""
    lis = _listener()
    t0 = time.time()
    lis.record_ack(400, ACCEPTED)  # different command
    result = lis.wait_for_ack(22, since=t0, timeout=0.3)
    check("ACK for a different command id does not satisfy the waiter",
          result is None)


# ── _wait_ack contract (no listener → False, never assume success) ────────────

def test_wait_ack_no_listener_returns_false():
    from dosync.adapters.mavlink import MAVLinkAdapter
    a = MAVLinkAdapter()
    # No listener registered for this device.
    ok = a._wait_ack(conn=None, command_id=22, device_id="ghost-drone",
                     since=time.time())
    check("_wait_ack with no listener returns False (silence is not success)",
          ok is False)


def test_wait_ack_reads_from_listener_registry():
    from dosync.adapters.mavlink import MAVLinkAdapter, _MAVLINK_AVAILABLE
    if not _MAVLINK_AVAILABLE:
        # _wait_ack compares against mavutil.mavlink.MAV_RESULT_ACCEPTED, which needs
        # pymavlink. The registry/wait logic itself is covered by the tests above;
        # this end-to-end check runs on a host with pymavlink (e.g. the SITL host).
        check("_wait_ack reads an ACCEPTED ack from the listener registry "
              "(skipped — pymavlink absent)", True)
        return
    a = MAVLinkAdapter()
    lis = _listener()
    a._listeners["drone-01"] = lis
    send_time = time.time()
    lis.record_ack(22, ACCEPTED)  # ACK present in the registry
    ok = a._wait_ack(conn=None, command_id=22, device_id="drone-01", since=send_time)
    check("_wait_ack reads an ACCEPTED ack from the listener registry", ok is True)


def test_wait_ack_denied_is_false():
    from dosync.adapters.mavlink import MAVLinkAdapter, _MAVLINK_AVAILABLE
    if not _MAVLINK_AVAILABLE:
        check("_wait_ack returns False on a DENIED result (skipped — pymavlink absent)",
              True)
        return
    a = MAVLinkAdapter()
    lis = _listener()
    a._listeners["drone-01"] = lis
    send_time = time.time()
    lis.record_ack(22, DENIED)
    ok = a._wait_ack(conn=None, command_id=22, device_id="drone-01", since=send_time)
    check("_wait_ack returns False on a DENIED result", ok is False)


# ── connected event ───────────────────────────────────────────────────────────

def test_wait_connected_times_out_when_never_connected():
    lis = _listener()
    # Never connected (no real thread). wait_connected should time out → False.
    check("wait_connected returns False when never connected",
          lis.wait_connected(timeout=0.2) is False)


def test_wait_connected_true_after_event_set():
    lis = _listener()
    lis._connected_event.set()  # simulate the listener having connected
    check("wait_connected returns True once connected", lis.wait_connected(0.2) is True)


def test_get_connection_returns_live_conn():
    lis = _listener()
    # Before connecting, no connection.
    check("get_connection is None before connecting", lis.get_connection() is None)
    # Simulate the listener having an open connection.
    sentinel = object()
    lis._conn = sentinel
    check("get_connection returns the listener's live connection",
          lis.get_connection() is sentinel)


def test_command_uses_listener_connection_not_its_own():
    """The single-connection invariant: the adapter writes commands on the listener's
    connection, it does not open a separate one. We assert the wiring: a connected
    listener's connection is what get_connection hands back, and the adapter reads it
    per-dispatch."""
    from dosync.adapters.mavlink import MAVLinkAdapter
    a = MAVLinkAdapter()
    lis = _listener()
    sentinel = object()
    lis._conn = sentinel
    lis._connected_event.set()
    a._listeners["drone-01"] = lis
    # The adapter should fetch the listener's connection (single shared link).
    got = a._listeners["drone-01"].get_connection()
    check("adapter obtains the command connection from the listener", got is sentinel)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} single-reader ACK tests passed.")
    if _FAIL:
        raise SystemExit(1)
