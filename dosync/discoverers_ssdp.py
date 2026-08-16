"""Find devices that announce themselves over SSDP.

SSDP is the other half of local announcement. mDNS covers most things with a
local API; SSDP covers UPnP devices, media renderers, routers, and a long tail
of hardware that publishes on the multicast group but never registers an mDNS
service — including printers whose vendors picked their own port.

Written against a real capture rather than a specification reading. A Bambu Lab
A1 mini on a home network announced:

    NOTIFY * HTTP/1.1
    HOST: 239.255.255.250:1900
    Location: 192.168.1.x
    NT: urn:bambulab-com:device:3dprinter:1
    USN: <serial>
    DevModel.bambu.com: N1
    DevVersion.bambu.com: 01.08.01.00

Two things that capture taught, and neither was obvious:

1. **The port is not always 1900.** That announcement arrived on **2021**. A
   discoverer that only joins the standard port misses a device that is
   otherwise announcing loudly and continuously.
2. **The device type is the useful field, even namespaced by a vendor.**
   `urn:bambulab-com:device:3dprinter:1` is not a standard URN, and it still
   says *3dprinter* — which is what a person needs in order to decide whether
   to adopt it.

**What this does not do**, as with mDNS: it reports what a device announced. It
does not decide what the device can do, and it emphatically does not mean the
device is reachable — that same printer was announcing while bound to its
vendor's cloud, where a local hub cannot command it at all. Finding is not
controlling.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct

from dosync.discovery import DiscoveredDevice

log = logging.getLogger("dosync.discoverers.ssdp")

MULTICAST_GROUP = "239.255.255.250"

#: 1900 is the standard. 2021 is here because a real device used it and nothing
#: in the standard forbids it; a discoverer that assumed the default would have
#: reported "nothing found" about a printer announcing every few seconds.
PORTS = (1900, 2021)

M_SEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: {group}:{port}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
)


def _parse(payload: bytes) -> dict:
    """SSDP is HTTP-shaped: a start line, then case-insensitive headers."""
    headers = {}
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:                            # pragma: no cover
        return headers
    for line in text.splitlines()[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return headers


def _address(location: str, fallback: str) -> str:
    """The host out of a `Location` header, which comes in two shapes.

    A 3D printer sent a bare address (`Location: 192.0.2.91`); a TV on the same
    network sent a full URL (`Location: http://192.0.2.105:9110/ip_control`).
    The first version split on "/" and produced `http:` as the address of every
    device that used the second form — written against one capture, broken by
    the next one on the same network.
    """
    location = (location or "").strip()
    if not location:
        return fallback
    if "://" in location:
        host = location.split("://", 1)[1].split("/", 1)[0]
        return host.rsplit(":", 1)[0] if ":" in host else host
    return location.split("/", 1)[0]


def _name(headers: dict, device_type: str, address: str) -> str:
    """A name a person recognises, preferring what the device called itself.

    Vendors publish one under their own header (`DevName.bambu.com`), and there
    is no standard for it — so anything ending in `name` is accepted. Falling
    back to the Location URL, as the first version did, showed people a URL
    where a name belongs.
    """
    for key, value in headers.items():
        # Vendor headers carry the vendor domain: `DevName.bambu.com`. Testing
        # the whole key for a "name" suffix matched nothing, because it ends in
        # the domain — so the field before the first dot is what to look at.
        if key.split(".", 1)[0].endswith("name") and value:
            return value
    if headers.get("server") and device_type:
        return f"{device_type} ({headers['server']})"
    return device_type or address


def _device_type(headers: dict) -> str:
    """The `NT`/`ST` URN, shortened to the part a reader can use.

    `urn:bambulab-com:device:3dprinter:1` → `3dprinter`. The vendor namespace is
    dropped on purpose: what matters is what the thing says it IS, and keeping
    the vendor would turn this into the beginning of a product catalogue.
    """
    urn = headers.get("nt") or headers.get("st") or ""
    if ":device:" in urn:
        tail = urn.split(":device:", 1)[1]
        return tail.split(":", 1)[0] or urn
    return urn


class SSDPDiscoverer:
    """Listens for SSDP announcements and solicits responses with M-SEARCH."""

    name = "ssdp"
    transport = "SSDP / UPnP (local network)"

    def can_discover(self) -> bool:
        # No optional dependency: SSDP is UDP and a socket. It can still fail on
        # a host with no multicast route, which surfaces as an empty scan rather
        # than a claim of having searched.
        return True

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredDevice]:
        found: dict[str, DiscoveredDevice] = {}
        await asyncio.gather(
            *(self._listen(port, timeout, found) for port in PORTS),
            return_exceptions=True,
        )
        results = [d for d in found.values() if d.ip not in ("127.0.0.1", "::1")]
        results.sort(key=lambda d: (not d.likely_actionable, d.service_type,
                                    d.device_name))
        log.info("SSDP scan: %d device(s) announced across ports %s",
                 len(results), ", ".join(str(p) for p in PORTS))
        return results

    async def _listen(self, port: int, timeout: float, found: dict) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", port))
            mreq = struct.pack("4sl", socket.inet_aton(MULTICAST_GROUP),
                               socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setblocking(False)
            # Solicit as well as listen: a device announces on its own schedule
            # (the printer said max-age 1800), and a scan that only waits is at
            # the mercy of that interval.
            sock.sendto(M_SEARCH.format(group=MULTICAST_GROUP, port=port).encode(),
                        (MULTICAST_GROUP, port))
        except OSError as exc:
            # A port already held by another service, or no multicast route.
            log.debug("SSDP port %s unavailable: %s", port, exc)
            sock.close()
            return

        deadline = loop.time() + timeout
        try:
            while loop.time() < deadline:
                try:
                    payload, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 4096),
                        timeout=max(0.1, deadline - loop.time()))
                except (asyncio.TimeoutError, OSError):
                    continue
                headers = _parse(payload)
                if not headers:
                    continue
                device_type = _device_type(headers)
                # An identity that survives a changing address: the USN is what
                # SSDP has for that, and it is usually a serial number.
                usn = headers.get("usn", "") or f"{addr[0]}:{port}"
                if usn in found:
                    continue
                # Vendor headers (`DevModel.bambu.com: N1`) are kept verbatim in
                # extra: not interpreted, because interpreting them is where a
                # product catalogue starts, and useful because they are what a
                # person recognises their own device by.
                vendor = {k: v for k, v in headers.items()
                          if k not in ("host", "cache-control", "nt", "nts",
                                       "usn", "location", "server", "st", "ext")}
                address = _address(headers.get("location", ""), addr[0])
                found[usn] = DiscoveredDevice(
                    adapter="",                  # nothing declared yet
                    device_id=usn,
                    device_name=_name(headers, device_type, address),
                    ip=address,
                    extra={"port": port, "headers": vendor, "transport": "ssdp",
                           "server": headers.get("server", "")},
                    service_type=device_type,
                    # Anything announcing itself as a `:device:` is announcing
                    # that it exists to be interacted with. Generic and
                    # vendor-neutral: it orders a list, it decides nothing.
                    likely_actionable=":device:" in (headers.get("nt")
                                                     or headers.get("st") or ""),
                )
        finally:
            sock.close()
