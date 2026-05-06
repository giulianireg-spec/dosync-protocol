"""
DoSync — Discovery CLI
Escanea la red local y muestra/registra dispositivos encontrados.

Uso:
    # Solo escanear (no registra nada):
    PYTHONPATH=. python3 discover.py

    # Escanear y registrar en la DB local:
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
        description="DoSync Device Discovery — escanea la red local",
    )
    parser.add_argument("--register", action="store_true",
                        help="Registrar dispositivos encontrados en la DB")
    parser.add_argument("--timeout",  type=float, default=5.0,
                        help="Timeout de escaneo en segundos (default: 5)")
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
        print(f"\n  Escaneando y registrando (timeout: {args.timeout}s)...\n")
        new = await disc.run()
        print(f"  Dispositivos nuevos registrados: {new}")
        print(f"  Total en registry: {len(hub.registry.all())}")
        print()
        print("  Dispositivos registrados:")
        for d in hub.registry.all():
            adapter = getattr(d, 'adapter', 'simulated') or 'simulated'
            config  = getattr(d, 'adapter_config', {})
            ip      = config.get('ip', '') if config else ''
            print(f"  · {d.device_id:<35} {adapter:<12} {ip}")
    else:
        await disc.scan_and_print()
        print("  (Usá --register para registrarlos en la DB)")


if __name__ == "__main__":
    asyncio.run(main())
