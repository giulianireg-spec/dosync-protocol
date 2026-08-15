"""Finding devices is a different job from driving them.

Until now, the only way for a component to be reached by the scan was to be an
adapter — so anything that finds without executing had to implement `execute`
in order to be seen. That deformed the model to fit the plumbing, and it kept
discovery to two transports (BLE, and WiZ through a legacy path) in a protocol
whose scan endpoint already argued, in its own docstring, that discovery must
not be an IP-only idea.

Found from the user's chair: preparing a from-scratch install with a real
device — a WiFi 3D printer — surfaced that a scan would not see it. The gap was
coverage, not design.
"""
import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

from dosync.discoverers import DiscovererRegistry, TransportDiscoverer
from dosync.discovery import DiscoveredDevice


class _Fake:
    name = "fake"
    transport = "fake transport"

    def __init__(self, ready=True, found=()):
        self._ready, self._found = ready, list(found)

    def can_discover(self):
        return self._ready

    async def discover(self, timeout: float = 5.0):
        return self._found


def test_a_discoverer_does_not_have_to_be_an_adapter():
    """The point of the whole change: no `execute` in sight."""
    assert isinstance(_Fake(), TransportDiscoverer)
    assert not hasattr(_Fake(), "execute")


def test_the_registry_separates_registered_from_ready():
    """"Nothing found" means something different when nothing was searched."""
    registry = DiscovererRegistry()
    registry.register(_Fake())
    unready = _Fake(ready=False)
    unready.name = "unready"
    registry.register(unready)

    assert len(registry) == 2
    assert [d.name for d in registry.ready()] == ["fake"]


def test_a_discoverer_that_raises_on_readiness_is_skipped_not_fatal():
    class _Broken(_Fake):
        name = "broken"

        def can_discover(self):
            raise RuntimeError("interface down")

    registry = DiscovererRegistry()
    registry.register(_Broken())
    registry.register(_Fake())
    assert [d.name for d in registry.ready()] == ["fake"]


def test_a_discoverer_must_declare_a_name():
    anonymous = _Fake()
    anonymous.name = ""
    with pytest.raises(ValueError):
        DiscovererRegistry().register(anonymous)


def test_findings_carry_what_the_device_announced_itself_as():
    """An address says where something is; the service type says what it claims
    to be, and that is the only part a person can act on."""
    d = DiscoveredDevice(adapter="", device_id="printer-1", device_name="Printer",
                         ip="192.0.2.10", extra={}, service_type="_octoprint._tcp",
                         likely_actionable=True)
    assert d.service_type == "_octoprint._tcp"
    assert d.likely_actionable is True


def test_service_type_defaults_to_empty_so_existing_discoverers_keep_working():
    d = DiscoveredDevice(adapter="wiz", device_id="x", device_name="x",
                         ip="192.0.2.11", extra={})
    assert d.service_type == "" and d.likely_actionable is False


def test_likely_actionable_orders_a_list_and_decides_nothing():
    """It is presentation, not policy: a device outside the list is still found."""
    from dosync.discoverers_mdns import LIKELY_ACTIONABLE, _short_type

    assert _short_type("_octoprint._tcp.local.") == "_octoprint._tcp"
    assert any("_octoprint".startswith(p) for p in LIKELY_ACTIONABLE)
    # A laptop announcing file sharing is reported, just not first.
    assert not any("_workstation._tcp".startswith(p) for p in LIKELY_ACTIONABLE)


def test_the_mdns_discoverer_reports_readiness_honestly():
    from dosync.discoverers_mdns import MDNSDiscoverer, ZEROCONF_AVAILABLE
    assert MDNSDiscoverer().can_discover() is ZEROCONF_AVAILABLE


def test_the_mdns_discoverer_returns_nothing_rather_than_failing_without_zeroconf(
        monkeypatch):
    import dosync.discoverers_mdns as mdns
    monkeypatch.setattr(mdns, "ZEROCONF_AVAILABLE", False)
    assert asyncio.run(mdns.MDNSDiscoverer().discover(timeout=0.1)) == []


def test_the_service_list_is_not_a_product_catalogue():
    """It decides where to listen, never what a device can do.

    The project declined to maintain a vendor catalogue when it labelled WiZ and
    Shelly as reference adapters. A discovery list that grew into one would
    reintroduce it through the back door — so it holds published, vendor-neutral
    service names, and asks the network to enumerate its own types.
    """
    from dosync.discoverers_mdns import SERVICE_TYPES
    assert "_services._dns-sd._udp.local." in SERVICE_TYPES, \
        "the scan no longer asks the network what else it offers"
    for service in SERVICE_TYPES:
        assert service.startswith("_") and service.endswith(".local."), service


def test_the_server_actually_registers_its_discoverers():
    """The unit tests all passed while nothing was wired up.

    The scan loop shipped and the registration did not: a `str.replace` whose
    anchor did not match left the file untouched and said nothing, so the
    endpoint consulted a registry no code ever filled. On the reference
    deployment the scan returned 200 with mDNS in neither `searched` nor
    `not_searchable` — absent entirely, which is the one outcome the
    searched/skipped reporting was built to make impossible.

    Every test in this file passed throughout. They exercised the pieces; none
    asserted the pieces were connected.
    """
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "DiscovererRegistry" in source, \
        "the server imports no discoverer registry — nothing will ever be searched"
    assert "hub.discoverers" in source, \
        "the server builds a registry and never gives it to the hub"
    assert "MDNSDiscoverer" in source, \
        "no discoverer is registered, so the registry is always empty"


def test_the_scan_consults_the_discoverer_registry():
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    # Anchor on the DECORATOR, not the path: the path also appears in a
    # neighbouring docstring, and slicing from there cut the body away — the
    # first version of this test failed against correct code for that reason.
    start = source.index('@app.get("/v1/discovery/scan"')
    end = source.find("\n@app.", start + 10)
    scan = source[start:end if end != -1 else len(source)]
    assert 'getattr(hub, "discoverers"' in scan or "hub.discoverers" in scan, \
        "the scan endpoint does not ask the discoverers anything"


def test_a_discoverer_reports_as_searched_or_skipped_but_never_vanishes():
    """Absence is the failure this reporting exists to prevent."""
    registry = DiscovererRegistry()
    ready = _Fake(found=[DiscoveredDevice(adapter="", device_id="d",
                                          device_name="d", ip="192.0.2.1",
                                          extra={}, service_type="_x._tcp")])
    unready = _Fake(ready=False)
    unready.name = "unready"
    registry.register(ready)
    registry.register(unready)

    names = {d.name for d in registry.all()}
    ready_names = {d.name for d in registry.ready()}
    assert names == {"fake", "unready"}
    # Everything registered is accounted for: searched or skipped, never missing.
    assert names - ready_names == {"unready"}
