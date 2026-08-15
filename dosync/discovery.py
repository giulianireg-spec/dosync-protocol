"""
DoSync — Discovery Module
=========================
Discovers devices on the local network automatically.

Soporta:
    - WiZ: broadcast UDP (pywizlight)
    - DoSync native: broadcast UDP en puerto 47201
    - Extensible: cualquier adapter puede registrar su propio discoverer

Basic usage:
    from dosync.discovery import Discovery

    discovery = Discovery(hub, executor)
    found = await discovery.run()
    print(f"Encontrados: {found} dispositivos")

Usage with auto-registration at hub startup:
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
    #: What the device announced itself AS — an mDNS service type
    #: (`_octoprint._tcp`), an SSDP device type, a BLE service UUID. An address
    #: says where something is; this says what it claims to be, and it is the
    #: only part a person can act on. A scan that returns forty addresses and no
    #: types is worse than one that returns nothing, because the reader has to
    #: go find out what each one is.
    service_type: str = ""
    #: Rough, honest ranking for presentation: a device announcing a control API
    #: is more likely to be worth adopting than a phone announcing AirPlay. Not
    #: a claim about compatibility — DoSync does not keep a catalogue of
    #: products, and this never decides anything, it only orders a list.
    likely_actionable: bool = False


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
    Discover devices on the local network and register them with the hub.

    Devices already registered are not duplicated — they are updated
    when their configuration changed (an address reassigned by DHCP).
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
        Returns how many new devices were registered.
        """
        log.info("Starting device discovery...")
        found_all: list[DiscoveredDevice] = []

        # WiZ
        wiz_devices = await discover_wiz(self.wiz_timeout)
        found_all.extend(wiz_devices)

        # Future discoverers are added here:
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
        Returns 1 when new, 0 when already present.
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

            # If an executor is present, make sure the WiZAdapter is registered
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
        Runs discovery periodically in the background.
        Useful for catching devices that appear or change address.

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
        Useful for diagnostics.
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