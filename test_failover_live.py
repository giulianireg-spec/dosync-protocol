#!/usr/bin/env python3
"""DoSync Multi-Hub Phase A — Real Failover Test Harness (observer only)."""
import argparse, socket, sys, time
import httpx
from dosync.hub_monitor import HubMonitor, HeartbeatObservation, MonitorState


def probe_primary(client, primary_url):
    try:
        r = client.get(f"{primary_url}/v1/hub/heartbeat", timeout=3.0)
        if r.status_code == 200:
            return True, r.json().get("devices")
        return False, None
    except Exception:
        return False, None


def probe_gateway(gateway_ip, port=80, timeout=2.0):
    try:
        sock = socket.create_connection((gateway_ip, port), timeout=timeout)
        sock.close()
        return True
    except ConnectionRefusedError:
        return True
    except (socket.timeout, OSError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", required=True)
    ap.add_argument("--ca-cert", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--gateway", required=True)
    ap.add_argument("--local-devices", type=int, default=0)
    ap.add_argument("--threshold", type=int, default=3)
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    verify = args.ca_cert if args.ca_cert else False
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    client = httpx.Client(verify=verify, headers=headers)
    monitor = HubMonitor(failure_threshold=args.threshold, local_device_count=args.local_devices)

    print("=" * 70)
    print("DoSync Multi-Hub Phase A — Live Failover Observer")
    print("=" * 70)
    print(f"  Watching primary : {args.primary}")
    print(f"  Gateway probe    : {args.gateway}")
    print(f"  Local devices    : {args.local_devices}")
    print(f"  Failure threshold: {args.threshold} misses")
    print()
    print("  SCENARIOS:")
    print("   1. Steady     — Pi running. Expect WATCHING.")
    print("   2. Primary down — on Pi: sudo systemctl stop dosync")
    print("                     Expect 3 misses -> PRIMARY_DOWN -> proposes (DESTRUCTIVE).")
    print("                     Restart: sudo systemctl start dosync")
    print("   3. Partition  — disconnect Mac network while Pi stays up.")
    print("                     Expect UNCERTAIN, does NOT propose.")
    print("=" * 70)
    print()

    last_state = None
    try:
        while True:
            reachable, devices = probe_primary(client, args.primary)
            net_ok = probe_gateway(args.gateway)
            monitor.observe(HeartbeatObservation(primary_reachable=reachable, network_reachable=net_ok, primary_devices=devices))
            snap = monitor.snapshot()
            proposal = monitor.promotion_proposal()
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] primary={'UP' if reachable else 'DOWN':<4} gateway={'OK' if net_ok else 'FAIL':<4} misses={snap['consecutive_misses']} state={snap['monitor_state']:<12} propose={proposal.proposed} destructive={proposal.destructive}")
            if snap["monitor_state"] != last_state:
                if snap["monitor_state"] == "PRIMARY_DOWN":
                    print(f"    >>> PRIMARY_DOWN. {proposal.reason}")
                    if proposal.destructive:
                        print(f"    >>> SAFEGUARD ACTIVE: promotion would be destructive. NOT promoting (observer only).")
                elif snap["monitor_state"] == "UNCERTAIN":
                    print(f"    >>> UNCERTAIN — possible partition. Correctly NOT proposing promotion (no split-brain).")
                elif snap["monitor_state"] == "WATCHING" and last_state is not None:
                    print(f"    >>> Recovered to WATCHING.")
                last_state = snap["monitor_state"]
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nObserver stopped. No promotion was ever performed (observer-only).")
        sys.exit(0)


if __name__ == "__main__":
    main()
