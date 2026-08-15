"""Finding devices is not the same job as driving them.

An adapter executes: it takes an action and makes a device do it. A discoverer
only listens to a transport and reports what announced itself. Until now the
only way for the scan to reach a transport was to be an adapter, which meant a
component that executes nothing had to implement `execute` in order to be seen —
deforming the model to fit the plumbing.

The distinction is not tidiness. The two have different lifecycles (an adapter
is registered when its library AND its credentials are present; a discoverer
needs only its library), different failure modes (an adapter failing means a
device did not move; a discoverer failing means you do not know what you have),
and different trust properties: **a discoverer enumerates someone's network**,
which is the first thing an attacker would want and belongs in the audit chain
next to "who turned authentication off".

A hub therefore registers discoverers alongside adapters, and the scan asks
both.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:                                # pragma: no cover
    from dosync.discovery import DiscoveredDevice

log = logging.getLogger("dosync.discoverers")


@runtime_checkable
class TransportDiscoverer(Protocol):
    """What the hub needs from anything that can find devices.

    A Protocol rather than a base class: a discoverer is a small thing, often a
    wrapper around one library, and third parties should be able to supply one
    without importing DoSync's class hierarchy. The same reasoning the project
    applied to resolvers.
    """

    #: Stable identifier reported in scan results — `"mdns"`, `"ssdp"`.
    name: str

    #: Human-readable transport, for the "searched / skipped" report.
    transport: str

    def can_discover(self) -> bool:
        """Whether this discoverer can search RIGHT NOW.

        Implementing `discover` is necessary and not sufficient — the library
        may be missing, the interface may be down. Claiming to have searched a
        transport that was never searched produces exactly the false "nothing
        found" the scan report exists to prevent.
        """
        ...

    async def discover(self, timeout: float = 5.0) -> list["DiscoveredDevice"]:
        """Listen on the transport and report what announced itself.

        Returns candidates. Registering them is emphatically not this method's
        job: the operator adopts, by name, and the adoption is audited.
        """
        ...


class DiscovererRegistry:
    """The discoverers a hub can use, and which of them can run."""

    def __init__(self) -> None:
        self._discoverers: dict[str, TransportDiscoverer] = {}

    def register(self, discoverer: TransportDiscoverer) -> None:
        name = getattr(discoverer, "name", None)
        if not name:
            raise ValueError("a discoverer must declare a name")
        self._discoverers[name] = discoverer
        log.info("Discoverer registered: %s (%s)", name,
                 getattr(discoverer, "transport", "unknown transport"))

    def get(self, name: str) -> TransportDiscoverer | None:
        return self._discoverers.get(name)

    def all(self) -> list[TransportDiscoverer]:
        return list(self._discoverers.values())

    def ready(self) -> list[TransportDiscoverer]:
        """Those that can search right now — the rest are reported as skipped."""
        out = []
        for d in self._discoverers.values():
            try:
                if d.can_discover():
                    out.append(d)
            except Exception as exc:            # a broken discoverer is skipped,
                log.info("%s could not report readiness: %s",              # never
                         getattr(d, "name", d), exc)                       # fatal
        return out

    def __len__(self) -> int:
        return len(self._discoverers)
