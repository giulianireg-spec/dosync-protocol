"""A discovery id and an inventory id are not the same thing.

The transport fabricates the first — `wiz-auto-192-168-100-33` — and the
operator chooses the second, which this project defends elsewhere as the right
way round: `wiz-a4c138` is what a bulb calls itself, not what its owner calls
it. The scan compared only `device_id`, so a bulb registered for months as
a name of their own came back as a new device, was offered for adoption, and was
adopted. The reference deployment ended with eleven WiZ entries for ten lamps.

The address that would have revealed it sat in `adapter_config` from the
original registration. The hub had the datum and never looked at it.

What this file pins is that the hub now *reports* the match and still does not
decide. Addresses move with DHCP; a hub concluding identity from one would
eventually fuse two different devices, which is worse than duplicating — a
duplicate is visible, a fusion is not.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_scan_reports_what_a_finding_matches():
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "def _already_here" in server, \
        "nothing correlates a finding against the registry"
    assert '"possibly_known"' in server, \
        "the scan result does not carry what a finding matched"


def test_the_match_is_reported_and_never_acted_on():
    """No merging, no hiding, no silent skipping."""
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    fn = server[server.index("def _already_here"):server.index('@app.get("/v1/discovery/scan"')]
    assert "device_name" in fn, \
        "the match does not carry the name the operator gave the device — which "\
        "is the part that lets them recognise it"
    for verb in ("registry.unregister", "save_device", "merge"):
        assert verb not in fn, f"the correlation performs {verb}; it must only report"


def test_the_dialog_shows_the_name_the_operator_chose():
    """The hub knew, and the person was never told.

    They recognise the name they picked; nothing else settles it for them.
    """
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    assert "possibly_known" in dashboard, \
        "the dashboard discards the correlation the hub computed"
    assert "You may already have this one" in dashboard
    assert "two entries for one device" in dashboard, \
        "the consequence of adopting anyway is not stated"


def test_a_commissionable_device_is_declared_as_such():
    """`_matterc._udp` announces that nobody has paired the device. On a shared
    network it may not be the operator's — four were adopted on the reference
    deployment before anyone asked what they were."""
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert '"commissionable"' in server, "commissionable findings are not flagged"
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    assert "has not been paired" in dashboard, \
        "the dialog treats an unpaired device like any other finding"
    assert "may belong to someone else" in dashboard


def test_wiz_discovery_prefers_a_stable_identity():
    """An address is the one datum guaranteed to change."""
    discovery = (REPO / "dosync" / "discovery.py").read_text(encoding="utf-8")
    assert 'f"wiz-{stable}"' in discovery, \
        "discovery still builds identity from the address alone"
    assert 'f"wiz-auto-{ip.replace' in discovery, \
        "the fallback for a bulb that offers no MAC is gone"


def test_existing_identifiers_are_not_rewritten():
    """Nobody wakes up to ten new ids because the scheme improved.

    The change names devices discovered from here on; a registry full of
    a name of their own stays exactly as it is.
    """
    discovery = (REPO / "dosync" / "discovery.py").read_text(encoding="utf-8")
    for rewriting in ("save_device", "registry.register", "update_device"):
        assert rewriting not in discovery, \
            f"discovery calls {rewriting}; it must only report what it found"


def test_the_same_device_over_two_transports_is_reported_as_one_match():
    """The general case, of which the WiZ duplicate is one example.

    A single lamp can answer WiZ broadcast, mDNS and Matter, arriving with three
    different identifiers. Without correlation an operator collects three copies
    of one lamp, each reporting that it cannot execute anything.
    """
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    fn = server[server.index("def _already_here"):server.index('@app.get("/v1/discovery/scan"')]
    # Correlation is on the address, which every transport reports — not on the
    # adapter, which is exactly what differs between them. (Searching for the
    # bare word "adapter" was the first attempt and it matched adapter_config,
    # the field the address is stored in: a test that fails on the mechanism it
    # depends on.)
    body = fn.split('"""')[2]
    for scoping in ("discovered.adapter", "d.adapter ==", "adapter ==",
                    "adapter !="):
        assert scoping not in body, (
            "the correlation is scoped by adapter, so the same device found "
            "over a different transport would not match")
    assert 'config.get("ip") or config.get("address")' in fn, \
        "the correlation does not read both spellings of the stored address"


def test_no_discoverer_builds_identity_from_an_address():
    """The rule every transport but one was already following, unwritten.

    BLE keys on the device MAC, SSDP on the serial in a `USN`, mDNS on the
    announced service name — all stable across a move. WiZ built its id from the
    IP, and was the only one, which is why the duplicate appeared there. A rule
    that lives only in the head of whoever applied it is what this project turns
    into a test every other time.

    Checked by source rather than by running a scan: every transport needs real
    hardware answering, and the property is about how an id is constructed.
    """
    offenders = []
    for name, path in (
            ("wiz", REPO / "dosync" / "discovery.py"),
            ("ble", REPO / "dosync" / "adapters" / "ble.py"),
            ("mdns", REPO / "dosync" / "discoverers_mdns.py"),
            ("ssdp", REPO / "dosync" / "discoverers_ssdp.py")):
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith("device_id="):
                continue
            # An address in the id is the failure. A fallback is allowed only
            # where the transport offers nothing stable, and must be visibly a
            # fallback rather than the primary.
            if "ip." in stripped or "ip.replace" in stripped:
                if "if stable" not in stripped and "else" not in stripped:
                    offenders.append(f"{name}: {stripped[:70]}")
    assert not offenders, (
        "a discoverer builds device identity from a network address, so the "
        f"same device returns as a new one after a DHCP lease: {offenders}")


def test_the_rule_is_written_where_a_third_party_would_read_it():
    """Someone writing their own discoverer implements the Protocol, and that
    is where the constraint has to be — not in the adapter that broke it."""
    contract = (REPO / "dosync" / "discoverers.py").read_text(encoding="utf-8")
    assert "must not be derived from a network address" in contract, \
        "the discoverer contract does not state the identity rule"
    assert "the transport's business" in contract, \
        "the contract does not say where a stable identity comes from"
    assert "survive the device moving" in contract, \
        "the contract states a prohibition without stating the property"
