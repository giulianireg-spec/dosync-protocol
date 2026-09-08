"""
DoSync — Discovery CLI
Scans the local network and lists or registers the devices it finds.

Usage:
    # Solo escanear (no registra nada):
    PYTHONPATH=. python3 discover.py

    # Scan and register in the local database:
    PYTHONPATH=. python3 discover.py --register

    # Cambiar timeout (default 5s):
    PYTHONPATH=. python3 discover.py --timeout 10
"""

import argparse
import asyncio
import logging

logging.basicConfig(level=logging.WARNING)


async def main():
    parser = argparse.ArgumentParser(
        description="DoSync Device Discovery — scans the local network",
    )
    parser.add_argument("--register", action="store_true",
                        help="Register the devices found in the database")
    parser.add_argument("--timeout",  type=float, default=5.0,
                        help="Scan timeout in seconds (default: 5)")
    args = parser.parse_args()

    from dosync.discovery import Discovery, discover_wiz
    from dosync.hub import DoSyncHub
    from dosync.adapters import AdapterExecutor
    from dosync.adapters.wiz import WiZAdapter

    hub      = DoSyncHub(db_path="dosync.db")
    executor = AdapterExecutor(hub, fallback_to_simulated=True)
    executor.register(WiZAdapter(hub=hub))

    disc = Discovery(hub, executor, wiz_timeout=args.timeout)

    if args.register:
        print(f"\n  Scanning and registering (timeout: {args.timeout}s)...\n")
        new = await disc.run()
        print(f"  Newly registered devices: {new}")
        print(f"  Total in registry: {len(hub.registry.all())}")
        print()
        print("  Registered devices:")
        for d in hub.registry.all():
            adapter = getattr(d, 'adapter', 'simulated') or 'simulated'
            config  = getattr(d, 'adapter_config', {})
            ip      = config.get('ip', '') if config else ''
            print(f"  · {d.device_id:<35} {adapter:<12} {ip}")
    else:
        await disc.scan_and_print()
        print("  (use --register to add them to the database)")


if __name__ == "__main__":
    asyncio.run(main())
