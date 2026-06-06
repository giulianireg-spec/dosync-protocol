"""
DoSync — Home Assistant Bridge CLI
Importa dispositivos de HA al hub DoSync.

Uso con HA real:
    PYTHONPATH=. python3 ha_bridge.py \
        --ha-url http://homeassistant.local:8123 \
        --ha-token <token> \
        --register

Uso en modo simulado (sin HA instalado):
    PYTHONPATH=. python3 ha_bridge.py --simulated --register

Cómo obtener el token de HA:
    1. Abrí HA en el navegador
    2. Click en tu perfil (abajo izquierda)
    3. Scrolleá hasta "Long-Lived Access Tokens"
    4. Click en "Create Token"
    5. Copiá el token generado
"""

import argparse
import asyncio
import logging
import os

logging.basicConfig(level=logging.WARNING)


async def main():
    parser = argparse.ArgumentParser(
        description="DoSync — Home Assistant Bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Modo simulado — ver qué importaría sin HA real
  python3 ha_bridge.py --simulated

  # Modo simulado — registrar dispositivos de ejemplo en el hub
  python3 ha_bridge.py --simulated --register

  # Con HA real
  python3 ha_bridge.py --ha-url http://homeassistant.local:8123 --ha-token <token>
  python3 ha_bridge.py --ha-url http://192.168.1.10:8123 --ha-token <token> --register
        """,
    )
    parser.add_argument("--ha-url",   default=os.environ.get("HA_URL", ""),
                        help="URL de Home Assistant (o env HA_URL)")
    parser.add_argument("--ha-token", default=os.environ.get("HA_TOKEN", ""),
                        help="Long-lived access token de HA (o env HA_TOKEN)")
    parser.add_argument("--simulated", action="store_true",
                        help="Usar dispositivos de ejemplo sin HA real")
    parser.add_argument("--register",  action="store_true",
                        help="Registrar dispositivos en la DB del hub")
    parser.add_argument("--db", default="dosync.db",
                        help="Path a la DB del hub (default: dosync.db)")
    args = parser.parse_args()

    if not args.simulated and not args.ha_url:
        parser.error("Provide --ha-url or use --simulated")

    if not args.simulated and not args.ha_token:
        parser.error("Provide --ha-token or use --simulated")

    from dosync.hub import DoSyncHub
    from dosync.adapters import AdapterExecutor
    from dosync.adapters.homeassistant import HABridge

    hub      = DoSyncHub(db_path=args.db if args.register else ":memory:")
    executor = AdapterExecutor(hub, fallback_to_simulated=True)

    bridge = HABridge(
        ha_url=args.ha_url or "http://homeassistant.local:8123",
        ha_token=args.ha_token or "simulated",
        hub=hub,
        simulated=args.simulated,
    )
    executor.register(bridge)

    mode = "SIMULATED" if args.simulated else args.ha_url
    print(f"\n── DoSync — Home Assistant Bridge ────────────────────")
    print(f"  Mode:     {mode}")
    print(f"  Register: {'yes → dosync.db' if args.register else 'no (dry run)'}")
    print()

    try:
        result = await bridge.import_devices()
    except Exception as e:
        print(f"  ✗ Error: {e}")
        print()
        return

    # result is {"new": N, "updated": M, "skipped": K, "total": T}
    total = result.get("total", 0) if isinstance(result, dict) else result
    print(f"  Found {total} device(s):\n")

    for d in hub.registry.all():
        adapter = getattr(d, "adapter", "simulated") or "simulated"
        config  = getattr(d, "adapter_config", {}) or {}
        entity  = config.get("entity_id", "")
        emerg   = "🚨" if d.emergency_capable else "  "
        tags    = ", ".join(d.tags[:4])
        print(f"  {emerg} {d.device_name:<35} [{entity}]")
        print(f"       tags: {tags}")
        print()

    if args.register:
        if isinstance(result, dict):
            new = result.get("new", 0)
            updated = result.get("updated", 0)
            skipped = result.get("skipped", 0)
            print(f"  ✓ {new} new · {updated} updated · {skipped} unchanged — saved to {args.db}")
        else:
            print(f"  ✓ {result} device(s) registered in {args.db}")
        print(f"  Restart the hub (or reload via API) to apply changes.")
    else:
        print(f"  (dry run — use --register to save to DB)")

    await bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
