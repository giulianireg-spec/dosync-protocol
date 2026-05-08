"""
DoSync — Discovery Module
=========================
Descubre dispositivos en la red local automáticamente.

Soporta:
    - WiZ: broadcast UDP (pywizlight)
    - DoSync native: broadcast UDP en puerto 47201
    - Extensible: cualquier adapter puede registrar su propio discoverer

Uso básico:
    from dosync.discovery import Discovery

    discovery = Discovery(hub, executor)
    found = await discovery.run()
    print(f"Encontrados: {found} dispositivos")

Uso con auto-registro al iniciar el hub:
    discovery = Discovery(hub, executor)
    asyncio.create_task(discovery.run_periodic(interval_seconds=300))
"""

from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .hub import DoSyncHub
    from .adapters import AdapterExecutor

log = logging.getLogger("dosync.discovery")


# ── Resultado de discovery ────────────────────────────────────────────────────

@dataclass
class DiscoveredDevice:
    adapter:      str
    device_id:    str
    device_name:  str
    ip:           str
    extra:        dict


# ── Discoverers individuales ──────────────────────────────────────────────────

async def discover_wiz(timeout: float = 5.0) -> list[DiscoveredDevice]:
    """
    Descubre lamparitas WiZ en la red local via broadcast UDP.
    Requiere: pip install pywizlight
    """
    try:
        from pywizlight import discovery as wiz_discovery
    except ImportError:
        log.warning("pywizlight not installed — skipping WiZ discovery")
        return []

    try:
        log.info("Scanning for WiZ bulbs (timeout: %.1fs)...", timeout)
        bulbs = await wiz_discovery.find_wizlights(wait_time=timeout)

        devices = []
        for i, bulb in enumerate(bulbs):
            ip = getattr(bulb, 'ip_address', None) or getattr(bulb, 'ip', None)
            if not ip:
                continue
            devices.append(DiscoveredDevice(
                adapter="wiz",
                device_id=f"wiz-auto-{ip.replace('.', '-')}",
                device_name=f"WiZ Bulb {ip}",
                ip=ip,
                extra={},
            ))
            log.info("Found WiZ bulb: %s", ip)

        log.info("WiZ discovery complete: %d bulb(s) found", len(devices))
        return devices

    except Exception as e:
        log.error("WiZ discovery failed: %s", e)
        return []


# ── Discovery principal ───────────────────────────────────────────────────────

class Discovery:
    """
    Descubre dispositivos en la red local y los registra en el hub.

    Los dispositivos ya registrados no se duplican — se actualizan
    si su configuración cambió (ej: IP cambió por DHCP).
    """

    def __init__(
        self,
        hub: "DoSyncHub",
        executor: Optional["AdapterExecutor"] = None,
        wiz_timeout: float = 5.0,
    ):
        self.hub         = hub
        self.executor    = executor
        self.wiz_timeout = wiz_timeout
        self._running    = False

    async def run(self) -> int:
        """
        Ejecuta un ciclo de discovery completo.
        Retorna el número de dispositivos nuevos registrados.
        """
        log.info("Starting device discovery...")
        found_all: list[DiscoveredDevice] = []

        # WiZ
        wiz_devices = await discover_wiz(self.wiz_timeout)
        found_all.extend(wiz_devices)

        # Futuros discoverers se agregan aquí:
        # shelly_devices = await discover_shelly()
        # found_all.extend(shelly_devices)

        # Registrar en el hub
        new_count = 0
        for device in found_all:
            new_count += self._register(device)

        log.info(
            "Discovery complete: %d device(s) found, %d new",
            len(found_all), new_count,
        )
        return new_count

    def _register(self, discovered: DiscoveredDevice) -> int:
        """
        Registra un dispositivo descubierto en el hub.
        Retorna 1 si es nuevo, 0 si ya existía.
        """
        existing = self.hub.registry.get(discovered.device_id)
        is_new   = existing is None

        if discovered.adapter == "wiz":
            from .adapters.wiz import wiz_manifest
            manifest = wiz_manifest(
                device_id=discovered.device_id,
                device_name=discovered.device_name,
                ip=discovered.ip,
            )
            self.hub.register_device(manifest)

            # Si hay executor, asegurarse que el WiZAdapter esté registrado
            if self.executor and "wiz" not in self.executor.registered_adapters():
                from .adapters.wiz import WiZAdapter
                self.executor.register(WiZAdapter(hub=self.hub))

        if is_new:
            log.info(
                "New device registered: %s (%s @ %s)",
                discovered.device_id, discovered.adapter, discovered.ip,
            )
        else:
            log.debug(
                "Device already registered: %s — updated",
                discovered.device_id,
            )

        return 1 if is_new else 0

    async def run_periodic(self, interval_seconds: int = 300) -> None:
        """
        Corre discovery periódicamente en background.
        Ideal para detectar lamparitas que se agregan o cambian de IP.

        Uso:
            asyncio.create_task(discovery.run_periodic(interval_seconds=300))
        """
        self._running = True
        log.info(
            "Periodic discovery started (every %ds)", interval_seconds
        )
        while self._running:
            await self.run()
            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        self._running = False
        log.info("Periodic discovery stopped")

    async def scan_and_print(self) -> None:
        """
        Escanea y muestra los dispositivos encontrados sin registrarlos.
        Útil para diagnóstico.
        """
        print("\n── DoSync Device Discovery ───────────────────────")
        print(f"  Scanning for WiZ bulbs (timeout: {self.wiz_timeout}s)...")

        wiz = await discover_wiz(self.wiz_timeout)

        if not wiz:
            print("  No devices found.")
        else:
            print(f"\n  Found {len(wiz)} WiZ bulb(s):\n")
            for d in wiz:
                already = self.hub.registry.get(d.device_id)
                status  = "already registered" if already else "new"
                print(f"  · {d.ip:<18} {d.device_name:<25} [{status}]")

        print()