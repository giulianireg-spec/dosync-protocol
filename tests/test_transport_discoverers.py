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
    from dosync.discoverers_mdns import META_QUERY, SERVICE_TYPES
    assert META_QUERY == "_services._dns-sd._udp.local.", \
        "the scan no longer asks the network what else it offers"
    for service in SERVICE_TYPES:
        assert service.startswith("_") and service.endswith(".local."), service


def test_the_meta_query_is_browsed_and_not_merely_listed():
    """Asking the network to name its service types buys nothing on its own.

    The first real scan returned only the hard-coded types: the meta-query was
    in the browse list, its answers are TYPES rather than devices, and nothing
    opened a browser for them. The docstring promised that unknown types would
    still surface, and they did not.
    """
    source = (REPO / "dosync" / "discoverers_mdns.py").read_text(encoding="utf-8")
    assert "if service_type == META_QUERY:" in source, \
        "the handler does not distinguish a service TYPE from a device"
    assert source.count("AsyncServiceBrowser(") >= 2, \
        "no browser is ever opened for a type the network named"


def test_the_hub_does_not_report_finding_itself():
    """Loopback announcements tell an operator nothing."""
    source = (REPO / "dosync" / "discoverers_mdns.py").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in source and "::1" in source, \
        "loopback findings are no longer filtered — the hub reports itself"


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


# ── SSDP: written against a capture from a real device ───────────────────────

#: Verbatim from a Bambu Lab A1 mini on a home network, 2026-08-15, with its
#: serial and address redacted. Kept as the fixture because a parser written
#: from a specification and one written from what hardware actually sends are
#: different parsers, and this project prefers the second.
BAMBU_ANNOUNCEMENT = (
    b"NOTIFY * HTTP/1.1\r\n"
    b"HOST: 239.255.255.250:1900\r\n"
    b"Server: UPnP/1.0\r\n"
    b"Location: 192.0.2.91\r\n"
    b"NT: urn:bambulab-com:device:3dprinter:1\r\n"
    b"USN: SERIAL-REDACTED\r\n"
    b"Cache-Control: max-age=1800\r\n"
    b"DevModel.bambu.com: N1\r\n"
    b"DevVersion.bambu.com: 01.08.01.00\r\n"
    b"DevConnect.bambu.com: cloud\r\n"
)


def test_a_real_announcement_parses():
    from dosync.discoverers_ssdp import _parse
    headers = _parse(BAMBU_ANNOUNCEMENT)
    assert headers["location"] == "192.0.2.91"
    assert headers["usn"] == "SERIAL-REDACTED"
    assert headers["devmodel.bambu.com"] == "N1"


def test_the_device_type_survives_a_vendor_namespace():
    """`urn:bambulab-com:device:3dprinter:1` is not a standard URN and still
    says what the thing is. Dropping the vendor keeps this from becoming the
    first line of a product catalogue."""
    from dosync.discoverers_ssdp import _device_type, _parse
    assert _device_type(_parse(BAMBU_ANNOUNCEMENT)) == "3dprinter"


def test_a_urn_without_a_device_segment_is_reported_as_is():
    from dosync.discoverers_ssdp import _device_type
    assert _device_type({"nt": "upnp:rootdevice"}) == "upnp:rootdevice"
    assert _device_type({}) == ""


def test_the_non_standard_port_is_listened_on():
    """The capture arrived on 2021, not 1900. A discoverer that assumed the
    default would have reported "nothing found" about a device announcing every
    few seconds — the false negative this reporting design exists to prevent,
    arriving through a different door."""
    from dosync.discoverers_ssdp import PORTS
    assert 1900 in PORTS and 2021 in PORTS


def test_ssdp_needs_no_optional_dependency():
    from dosync.discoverers_ssdp import SSDPDiscoverer
    assert SSDPDiscoverer().can_discover() is True


def test_the_server_registers_the_ssdp_discoverer():
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "SSDPDiscoverer" in source, "SSDP is implemented and never registered"


# ── Adoption of a device nothing has declared yet ────────────────────────────

def test_adoption_accepts_a_finding_with_no_adapter():
    """The scan would show someone their printer and refuse to let them keep it.

    `/v1/discovery/adopt` required an adapter — written when the only discoverer
    was WiZ, where a finding always carried one. Generalising discovery exposed
    the assumption: mDNS and SSDP report an address and a service type, and
    nothing has declared what the device can do.

    Adopting it as inventory is honest only because the hub now says what it
    cannot do: an adapter-less device is reported as unexecutable at every
    start, and any action on it comes back `simulated`.
    """
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    adopt = source[source.index('async def adopt_device'):]
    adopt = adopt[:adopt.index("\n@app.")] if "\n@app." in adopt else adopt
    assert 'detail="device_id is required"' in adopt, \
        "adopt still demands an adapter, so a discovered device cannot be kept"
    assert "elif not adapter:" in adopt, \
        "there is no path for a finding that declares no adapter"


def test_an_adopted_inventory_device_keeps_what_it_announced():
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert '"discovered_as": service' in source, \
        "the service type a device announced is discarded on adoption"


def test_the_dashboard_sends_the_service_type_and_explains_the_limit():
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    assert "service_type: d.service_type" in dashboard, \
        "the dashboard adopts without passing what the device announced"
    assert "Nothing has declared what this device can do yet" in dashboard, \
        "the dashboard lets someone adopt an inert device without saying so"
    assert "WiZ today" not in dashboard, \
        "the empty-scan message still claims only WiZ can discover"


def test_location_parses_in_both_shapes_devices_actually_send():
    """Two devices on one network sent two different shapes.

    A 3D printer sent a bare address (`Location: 192.0.2.91`); a TV sent a full
    URL (`Location: http://192.0.2.105:9110/ip_control`). The first parser was
    written against the printer and split on "/", so every device using the
    second form was reported at the address `http:` — visible in the dashboard,
    which offered to adopt something "@ http:".
    """
    from dosync.discoverers_ssdp import _address
    assert _address("192.0.2.91", "fallback") == "192.0.2.91"
    assert _address("http://192.0.2.105:9110/ip_control", "fb") == "192.0.2.105"
    assert _address("https://192.0.2.7/desc.xml", "fb") == "192.0.2.7"
    assert _address("", "192.0.2.50") == "192.0.2.50"


def test_a_device_is_named_by_what_it_called_itself():
    """Not by its Location URL, which is where a name does not belong."""
    from dosync.discoverers_ssdp import _name
    # Vendor headers carry the vendor domain — DevName.bambu.com — so a suffix
    # test on the whole key matches nothing.
    assert _name({"devname.bambu.com": "printer-A"}, "3dprinter", "192.0.2.91") \
        == "printer-A"
    assert _name({"friendlyname": "Living TV"}, "MediaRenderer", "192.0.2.9") \
        == "Living TV"
    # With nothing to go on, the type is more use than an address.
    assert "IPControlServer" in _name({"server": "UPnP/1.0"}, "IPControlServer",
                                      "192.0.2.105")


def test_one_device_announcing_many_times_is_one_finding():
    """A television turned into eight rows.

    SSDP devices announce repeatedly — once as `upnp:rootdevice`, once per
    service, once bare — and each announcement carries a different USN of the
    form `uuid:XXX::urn:YYY`. Keying on the whole USN made a network of two
    devices report twelve, and the dashboard asked about each one in a separate
    dialog.

    Identity is the uuid before `::`.
    """
    usns = [
        "uuid:d58697e1-2986-4ac8-bfb5-fdcf92ec938b::upnp:rootdevice",
        "uuid:d58697e1-2986-4ac8-bfb5-fdcf92ec938b",
        "uuid:d58697e1-2986-4ac8-bfb5-fdcf92ec938b::urn:samsung.com:service:IPControlService:1",
        "uuid:d58697e1-2986-4ac8-bfb5-fdcf92ec938b::urn:samsung.com:device:IPControlServer:1",
    ]
    identities = {u.split("::", 1)[0] for u in usns}
    assert len(identities) == 1, "the same device would be reported four times"

    source = (REPO / "dosync" / "discoverers_ssdp.py").read_text(encoding="utf-8")
    assert 'usn.split("::", 1)[0]' in source, \
        "findings are still keyed on the full USN, so one device reports many"


def test_the_hub_does_not_discover_its_own_search():
    """Multicast returns to the sender.

    Without a guard the hub receives its own M-SEARCH, parses it as an
    announcement, and reports itself as a device offering `ssdp:all` — once per
    port, on every scan. It did exactly that on the reference deployment.
    """
    source = (REPO / "dosync" / "discoverers_ssdp.py").read_text(encoding="utf-8")
    assert 'b"M-SEARCH"' in source, "the hub still processes its own searches"
    assert 'headers.get("nt") or headers.get("st")' in source, \
        "a packet announcing nothing is still treated as a finding"


def test_the_description_document_supplies_a_name_worth_showing():
    """Headers give a type; the document gives a name.

    A television announced `IPControlServer` in its SSDP headers and
    `75" QLED` by Samsung in the document at its Location. Only one of those is
    worth showing a person.
    """
    from dosync.discoverers_ssdp import _name
    assert _name({"friendlyname": '75" QLED'}, "IPControlServer",
                 "192.0.2.105") == '75" QLED'


def test_discovery_does_not_depend_on_the_description_being_reachable():
    """A device that does not serve its document is still a finding."""
    import asyncio as _asyncio
    from dosync.discoverers_ssdp import _describe
    assert _asyncio.run(_describe("")) == {}
    assert _asyncio.run(_describe("not-a-url")) == {}
