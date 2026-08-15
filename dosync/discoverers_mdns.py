"""Find devices that announce themselves on the local network.

mDNS (and the SSDP devices that also answer it) is how most things with a local
API make themselves known: printers, NAS boxes, cameras, Shelly and Tasmota and
ESPHome nodes, Prusa and Bambu printers, media players. One discoverer covers
that whole class, which is the alternative to writing an adapter per vendor —
the path the project already declined when it labelled WiZ and Shelly as
reference adapters rather than a catalogue.

**What this does not do.** It reports what a device announced: a service type,
an address, a port, whatever it published in TXT records. It does not decide
what the device can do. A `_octoprint._tcp` on the network is not yet a DoSync
device, because DoSync resolves over declared capabilities and nobody has
declared any. Turning a discovered service into a capability manifest is a
separate question with its own open design — deliberately not answered here.

`zeroconf` is treated the way `bleak` is: shipped in the core and used when
present. Requiring an install before you can find out what you have is the
circle the README already describes — nobody installs a discovery library
before knowing they own something to discover.
"""
from __future__ import annotations

import asyncio
import logging

from dosync.discovery import DiscoveredDevice

log = logging.getLogger("dosync.discoverers.mdns")

try:
    from zeroconf import ServiceStateChange, Zeroconf
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    ZEROCONF_AVAILABLE = True
except ImportError:                              # pragma: no cover
    ZEROCONF_AVAILABLE = False
    log.debug("zeroconf not installed — mDNS discovery is inactive")

#: Service types worth listening for. Deliberately NOT a product catalogue: each
#: entry is a published, vendor-neutral service name, and the list decides only
#: WHERE TO LISTEN, never what a device can do. `_services._dns-sd._udp` asks the
#: network to enumerate its own service types, so unknown ones still surface.
#: Asks the network to name every service type it offers. Browsed separately
#: from the list below, because its answers are TYPES, not devices.
META_QUERY = "_services._dns-sd._udp.local."

SERVICE_TYPES = (
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_octoprint._tcp.local.",
    "_prusalink._tcp.local.",
    "_bambulab._tcp.local.",
    "_ipp._tcp.local.",                # printers
    "_printer._tcp.local.",
    "_mqtt._tcp.local.",
    "_esphomelib._tcp.local.",
    "_shelly._tcp.local.",
    "_hap._tcp.local.",                # HomeKit accessories
    "_matter._tcp.local.",
    "_matterc._udp.local.",            # Matter commissionable
    "_workstation._tcp.local.",
)

#: Announcing one of these suggests a device that exists to be controlled, as
#: opposed to a laptop advertising file sharing. Used ONLY to order a list so a
#: person sees plausible candidates first — it grants nothing and blocks
#: nothing, and a device outside it is still reported.
LIKELY_ACTIONABLE = (
    "_octoprint", "_prusalink", "_bambulab", "_ipp", "_printer", "_mqtt",
    "_esphomelib", "_shelly", "_hap", "_matter", "_matterc",
)


def _short_type(service_type: str) -> str:
    """`_octoprint._tcp.local.` → `_octoprint._tcp`, which is what a reader wants."""
    return service_type.removesuffix(".local.").removesuffix(".")


class MDNSDiscoverer:
    """Listens for mDNS/DNS-SD announcements on the local network."""

    name = "mdns"
    transport = "mDNS / DNS-SD (local network)"

    def can_discover(self) -> bool:
        return ZEROCONF_AVAILABLE

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredDevice]:
        if not ZEROCONF_AVAILABLE:
            return []

        found: dict[str, DiscoveredDevice] = {}
        seen_types: set[str] = set()
        azc = AsyncZeroconf()

        browsers: list = []

        def _on_change(zeroconf: Zeroconf, service_type: str, name: str,
                       state_change: "ServiceStateChange") -> None:
            if state_change is not ServiceStateChange.Added:
                return
            # The meta-query does not return devices: it returns the SERVICE
            # TYPES this network offers. Each one has to be browsed in turn or
            # the query buys nothing — which is what happened on its first real
            # run, where the scan reported only the types hard-coded below and
            # the docstring's promise that "unknown types still surface" was
            # simply false.
            if service_type == META_QUERY:
                discovered_type = f"{name}."
                if discovered_type in SERVICE_TYPES or discovered_type in seen_types:
                    return
                seen_types.add(discovered_type)
                try:
                    browsers.append(AsyncServiceBrowser(
                        zeroconf, [discovered_type], handlers=[_on_change]))
                except Exception as exc:
                    log.debug("could not browse %s: %s", discovered_type, exc)
                return
            asyncio.ensure_future(_resolve(zeroconf, service_type, name))

        async def _resolve(zeroconf, service_type: str, name: str) -> None:
            try:
                from zeroconf.asyncio import AsyncServiceInfo
                info = AsyncServiceInfo(service_type, name)
                if not await info.async_request(zeroconf, int(timeout * 1000)):
                    return
                addresses = info.parsed_scoped_addresses() or []
                ip = addresses[0] if addresses else ""
                short = _short_type(service_type)
                properties = {}
                for key, value in (info.properties or {}).items():
                    try:
                        properties[key.decode()] = (
                            value.decode() if isinstance(value, bytes) else value)
                    except (UnicodeDecodeError, AttributeError):
                        continue
                device_id = name.removesuffix("." + service_type).strip(".")
                # One host announcing on loopback, LAN and a docker bridge is
                # one finding, not three. Keyed on identity, not on address;
                # the first real scan returned the hub itself three times.
                key = f"{device_id}|{short}"
                if key in found and found[key].ip:
                    return
                found[key] = DiscoveredDevice(
                    adapter="",                 # unknown: nothing declared yet
                    device_id=device_id,
                    device_name=properties.get("fn") or device_id,
                    ip=ip,
                    extra={"port": info.port, "properties": properties,
                           "transport": "mdns"},
                    service_type=short,
                    likely_actionable=any(short.startswith(p)
                                          for p in LIKELY_ACTIONABLE),
                )
            except Exception as exc:            # one bad record must not end a scan
                log.debug("could not resolve %s: %s", name, exc)

        try:
            browsers.append(AsyncServiceBrowser(
                azc.zeroconf, [META_QUERY, *SERVICE_TYPES], handlers=[_on_change]))
            # Two windows: the first lets the network name its service types,
            # the second lets the browsers opened for those types answer.
            await asyncio.sleep(timeout / 2)
            await asyncio.sleep(timeout / 2)
        except Exception as exc:
            log.info("mDNS scan did not complete: %s", exc)
        finally:
            for browser in browsers:
                try:
                    await browser.async_cancel()
                except Exception:
                    pass
            await azc.async_close()

        # Loopback is the hub finding itself, which tells the operator nothing.
        results = [d for d in found.values() if d.ip not in ("127.0.0.1", "::1")]
        results.sort(key=lambda d: (not d.likely_actionable, d.service_type,
                                    d.device_name))
        log.info("mDNS scan: %d service(s) announced, %d likely actionable",
                 len(results), sum(1 for d in results if d.likely_actionable))
        return results
