"""
Tests for MAVLinkAdapter._single_connection — deriving the ONE bidirectional
connection the listener owns and the command writes on (single-connection,
single-reader design).

The connection must be a receiving form (udp:/udpin:/tcp:/serial:) — it is what
learns target_system from the heartbeat. An outbound-only 'udpout:' (which cannot
receive the heartbeat, producing target_system=0) is normalized back to 'udp:'.

Pure logic, no pymavlink, no socket.
"""

from dosync.adapters.mavlink import MAVLinkAdapter

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


def test_udp_connection_kept():
    conn = MAVLinkAdapter._single_connection({"connection": "udp:127.0.0.1:14550"})
    check("a receiving udp: connection is used as the single connection",
          conn == "udp:127.0.0.1:14550")


def test_udpin_connection_kept():
    conn = MAVLinkAdapter._single_connection({"connection": "udpin:127.0.0.1:14550"})
    check("a udpin: connection is kept (it receives)", conn == "udpin:127.0.0.1:14550")


def test_udpout_normalized_to_receiving():
    conn = MAVLinkAdapter._single_connection({"connection": "udpout:127.0.0.1:14550"})
    check("a legacy udpout: is normalized to udp: so it can receive the heartbeat",
          conn == "udp:127.0.0.1:14550")


def test_telemetry_connection_used_when_no_main():
    conn = MAVLinkAdapter._single_connection({"telemetry_connection": "udp:127.0.0.1:14551"})
    check("telemetry_connection is used as the single connection when present",
          conn == "udp:127.0.0.1:14551")


def test_main_connection_preferred_over_telemetry():
    conn = MAVLinkAdapter._single_connection({
        "connection": "udp:127.0.0.1:14550",
        "telemetry_connection": "udp:127.0.0.1:14551"})
    check("the main connection is preferred when both are present",
          conn == "udp:127.0.0.1:14550")


def test_serial_connection_kept():
    conn = MAVLinkAdapter._single_connection({"connection": "serial:/dev/ttyUSB0:57600"})
    check("a serial connection is used as-is (one bidirectional link)",
          conn == "serial:/dev/ttyUSB0:57600")


def test_tcp_connection_kept():
    conn = MAVLinkAdapter._single_connection({"connection": "tcp:127.0.0.1:5760"})
    check("a tcp connection is used as-is", conn == "tcp:127.0.0.1:5760")


def test_no_connection_returns_none():
    check("no connection yields None", MAVLinkAdapter._single_connection({}) is None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} single-connection tests passed.")
    if _FAIL:
        raise SystemExit(1)
