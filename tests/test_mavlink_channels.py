"""
Tests for MAVLinkAdapter._split_channels — separating the command and telemetry
channels so they never bind the same UDP port (the cause of 'Address already in use').

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


# ── udp: command is rewritten to udpout (does not bind) ───────────────────────

def test_udp_command_becomes_udpout():
    cmd, tel = MAVLinkAdapter._split_channels({"connection": "udp:127.0.0.1:14551"})
    check("udp command rewritten to udpout (outbound, no bind)",
          cmd == "udpout:127.0.0.1:14551")


def test_udp_telemetry_keeps_binding_form():
    cmd, tel = MAVLinkAdapter._split_channels({"connection": "udp:127.0.0.1:14551"})
    check("telemetry keeps the binding form (listens)",
          tel == "udp:127.0.0.1:14551")


def test_command_and_telemetry_do_not_bind_same_port():
    # The whole point: with a single udp connection string, the two derived channels
    # must not both bind — command is outbound, telemetry binds.
    cmd, tel = MAVLinkAdapter._split_channels({"connection": "udp:127.0.0.1:14551"})
    check("command is outbound so it does not contend for the bind",
          cmd.startswith("udpout:") and tel.startswith("udp:"))


# ── explicit telemetry_connection ─────────────────────────────────────────────

def test_explicit_telemetry_connection_used():
    cmd, tel = MAVLinkAdapter._split_channels({
        "connection": "udp:127.0.0.1:14550",
        "telemetry_connection": "udp:127.0.0.1:14551"})
    check("command derived from connection", cmd == "udpout:127.0.0.1:14550")
    check("telemetry uses the explicit telemetry_connection",
          tel == "udp:127.0.0.1:14551")


# ── serial (hardware) is left untouched ───────────────────────────────────────

def test_serial_connection_not_rewritten():
    cmd, tel = MAVLinkAdapter._split_channels({"connection": "serial:/dev/ttyUSB0:57600"})
    check("serial command left as-is", cmd == "serial:/dev/ttyUSB0:57600")
    check("serial telemetry defaults to the same link (single bidirectional link)",
          tel == "serial:/dev/ttyUSB0:57600")


# ── defensive validation ──────────────────────────────────────────────────────

def test_same_bind_port_raises():
    # Both channels binding the same udp port (command not rewritten to udpout
    # because it's already udpin) must fail clearly, not silently 'Address in use'.
    raised = False
    try:
        MAVLinkAdapter._split_channels({
            "connection": "udpin:127.0.0.1:14551",
            "telemetry_connection": "udp:127.0.0.1:14551"})
    except ValueError as e:
        raised = "same UDP port" in str(e) or "bind" in str(e)
    check("two channels binding the same udp port raise a clear error", raised)


def test_outbound_command_with_binding_telemetry_ok():
    # The normal, correct case: outbound command + binding telemetry on the same
    # port number is fine (only one binds).
    ok = True
    try:
        MAVLinkAdapter._split_channels({
            "connection": "udp:127.0.0.1:14551",
            "telemetry_connection": "udpin:127.0.0.1:14551"})
    except ValueError:
        ok = False
    check("outbound command + binding telemetry on same port is allowed", ok)


def test_missing_connection_yields_none():
    cmd, tel = MAVLinkAdapter._split_channels({})
    check("no connection yields (None, None)", cmd is None and tel is None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} channel-separation tests passed.")
    if _FAIL:
        raise SystemExit(1)
