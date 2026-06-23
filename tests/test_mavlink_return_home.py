"""
Tests for return_home (RTL) confirmation — the last link that closes the full
AI->drone loop.

return_home is confirmed by the DISARM after landing: the vehicle flies RTL, lands
at home, and shuts its motors down. The listener emits FINISHED on that disarm, but
only while a 'disarm' arrival target is active (a return_home is in flight). Also
covers the coupled fix: a commanded GUIDED->RTL transition must NOT be read as a
manual takeover (RTL/LAND are autonomous modes we command).

Pure logic — fake HEARTBEAT messages, no pymavlink, no socket.
"""

import queue

from dosync.adapters.mavlink import (
    MAVLinkTelemetryListener, MAVLinkTelemetryMapper, _AUTONOMOUS_MODES,
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


ARMED_BIT = 0x80


class _HB:
    """Fake HEARTBEAT. armed -> base_mode safety-armed bit; mode -> mode_name."""
    def __init__(self, armed=True, mode=None):
        self.base_mode = ARMED_BIT if armed else 0x00
        if mode is not None:
            self.mode_name = mode

    def get_type(self):
        return "HEARTBEAT"


def _listener():
    return MAVLinkTelemetryListener(
        device_id="drone-01", connection_factory=lambda: None, out_queue=queue.Queue())


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


# ── disarm target confirms return_home ────────────────────────────────────────

def test_disarm_target_finishes_on_disarm():
    lis = _listener()
    lis.set_arrival_target("disarm", None)
    lis._check_arrival(_HB(armed=False))  # landed, motors off
    events = _drain(lis._queue)
    check("disarm after RTL emits FINISHED",
          any(e[1] == TelemetryEvent.FINISHED for e in events))
    check("disarm target cleared after FINISHED", lis._get_arrival_target() is None)


def test_disarm_target_waits_while_armed():
    lis = _listener()
    lis.set_arrival_target("disarm", None)
    lis._check_arrival(_HB(armed=True))  # still flying RTL
    check("still-armed vehicle does not finish return_home", lis._queue.empty())
    check("disarm target still pending while armed",
          lis._get_arrival_target() is not None)


def test_disarm_without_target_does_nothing():
    lis = _listener()
    # No target active — a disarm must not spuriously complete anything.
    lis._check_arrival(_HB(armed=False))
    check("disarm with no active target emits nothing", lis._queue.empty())


def test_disarm_target_ignores_position_messages():
    lis = _listener()
    lis.set_arrival_target("disarm", None)

    class _GPI:
        def get_type(self): return "GLOBAL_POSITION_INT"
        lat = 0; lon = 0; relative_alt = 0
    lis._check_arrival(_GPI())  # position message, not a heartbeat
    check("disarm target ignores position messages", lis._queue.empty())


def test_disarm_finishes_only_once():
    lis = _listener()
    lis.set_arrival_target("disarm", None)
    lis._check_arrival(_HB(armed=False))
    lis._check_arrival(_HB(armed=False))  # already cleared
    events = _drain(lis._queue)
    check("disarm fires FINISHED exactly once",
          sum(1 for e in events if e[1] == TelemetryEvent.FINISHED) == 1)


# ── _is_disarmed helper ───────────────────────────────────────────────────────

def test_is_disarmed_true_when_bit_clear():
    check("_is_disarmed True when armed bit clear",
          MAVLinkTelemetryListener._is_disarmed(_HB(armed=False)) is True)


def test_is_disarmed_false_when_armed():
    check("_is_disarmed False when armed bit set",
          MAVLinkTelemetryListener._is_disarmed(_HB(armed=True)) is False)


# ── coupled fix: RTL/LAND are autonomous (no false manual takeover) ───────────

def test_rtl_land_in_autonomous_modes():
    check("RTL is an autonomous mode", "RTL" in _AUTONOMOUS_MODES)
    check("LAND is an autonomous mode", "LAND" in _AUTONOMOUS_MODES)


def test_guided_to_rtl_not_manual_takeover():
    m = MAVLinkTelemetryMapper()
    m.map_message(_HB(mode="GUIDED"))
    result = m.map_message(_HB(mode="RTL"))  # our return_home
    check("commanded GUIDED->RTL is not a manual takeover", result is None)


def test_guided_to_stabilize_still_manual_takeover():
    m = MAVLinkTelemetryMapper()
    m.map_message(_HB(mode="GUIDED"))
    result = m.map_message(_HB(mode="STABILIZE"))  # a real pilot takeover
    check("GUIDED->STABILIZE is still a manual takeover",
          result is not None and result[0] == TelemetryEvent.MANUAL_CONTROL_TAKEN)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} return_home confirmation tests passed.")
    if _FAIL:
        raise SystemExit(1)
