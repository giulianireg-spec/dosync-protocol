"""Scan, then adopt — approval and naming as one step (2026-07-26).

Panel finding on H6: the discovery machinery already existed (207 lines, two
endpoints, `run_periodic`) and the dashboard called none of it, so a hub with no
devices was a dead end whose only exit was a hand-written JSON manifest.

Two design decisions came out of that session and are pinned here:

  * **Scanning must not register.** `POST /v1/discovery/run` finds and registers
    in one step, which is fine for a scripted setup but wrong as the only path:
    in a protocol whose argument is accountability, devices appearing because
    they answered a broadcast — approved by nobody — contradicts the premise.
  * **The operator names them.** `wiz-a4c138` is what the bulb calls itself;
    "Kitchen light" is what makes every later screen readable.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import dosync.server as srv
    return srv, TestClient(srv.app)


def test_scanning_registers_nothing(client):
    """The endpoint reports candidates and their registration state; it must not
    change that state."""
    srv, c = client
    before = len(srv.hub.registry.all())
    r = c.get("/v1/discovery/scan")
    assert r.status_code == 200
    assert "found" in r.json()
    assert len(srv.hub.registry.all()) == before, "a scan must have no side effects"


def test_adopting_uses_the_name_the_operator_chose(client):
    srv, c = client
    r = c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-test-adopt", "ip": "192.168.1.50",
        "device_name": "Kitchen light", "room": "kitchen"})
    assert r.status_code == 200 and r.json()["adopted"] is True

    dev = srv.hub.registry.get("wiz-test-adopt")
    assert dev is not None
    assert dev.device_name == "Kitchen light", \
        "the chosen name is the one that makes later screens readable"


def test_adoption_is_recorded_as_operator_approved(client):
    """'How did this device get here' is the same class of question as 'who
    turned authentication off'."""
    srv, c = client
    c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-test-audit", "ip": "1.2.3.4",
        "device_name": "Audited lamp"})

    entries = [e for e in srv.hub.audit_log.entries()
               if e.get("type") == "device_adopted"
               and e.get("device_id") == "wiz-test-audit"]
    assert entries, "adoption must appear in the chain"
    assert entries[-1]["approved_by_operator"] is True
    assert entries[-1]["device_name"] == "Audited lamp"


def test_adopting_twice_is_not_an_error_and_not_a_duplicate(client):
    srv, c = client
    body = {"adapter": "wiz", "device_id": "wiz-test-twice", "ip": "1.2.3.4",
            "device_name": "Once"}
    assert c.post("/v1/discovery/adopt", json=body).json()["adopted"] is True
    second = c.post("/v1/discovery/adopt", json=body).json()
    assert second["adopted"] is False and "already" in second["reason"]


def test_an_adapter_that_cannot_build_a_manifest_says_so(client):
    """Discovery is an adapter capability, not a protocol promise — a drone does
    not answer a UDP broadcast and a clinical device sits on a proprietary bus.
    The honest response is to name manual registration as the normal path, not
    to fail obscurely."""
    srv, c = client
    r = c.post("/v1/discovery/adopt", json={"adapter": "mqtt", "device_id": "x"})
    assert r.status_code == 422
    assert "/v1/devices/register" in r.json()["detail"], \
        "and must point at the path that does work"


def test_automatic_adoption_is_recorded_too(client):
    """`run` registering without a human choosing is defensible — invoking it IS
    the approval — but it must not be invisible."""
    import inspect

    import dosync.server as srv
    src = inspect.getsource(srv.run_discovery)
    assert "devices_auto_adopted" in src, \
        "auto-registration must append to the chain"
    assert "approved_by_operator" in src, \
        "and distinguish itself from an operator's deliberate choice"


def test_dashboard_offers_the_scan(client):
    """The machinery existed and nothing in the UI called it — the same defect
    as the dashboard itself sitting outside the package."""
    srv, c = client
    page = c.get("/").text
    assert "scanDevices()" in page
    assert "/v1/discovery/scan" in page and "/v1/discovery/adopt" in page


# ── Managing devices without a terminal (2026-07-26) ────────────────────────

def test_renaming_persists_across_a_restart(tmp_path):
    """Renaming had no endpoint: fixing a name meant re-registering the whole
    manifest, reconstructing every capability to change one string. A device
    adopted from a scan arrives called `wiz-a4c138`, so getting it wrong was
    expensive.

    Persistence is the part worth pinning — a rename that silently un-renames
    itself on restart would be worse than not offering the feature.
    """
    import importlib
    import os

    from dosync.hub import DoSyncHub

    import dosync.server as srv

    # Save the ORIGINAL value, not merely the fact that one existed. The first
    # version popped the variable on teardown, so later tests fell back to the
    # default on-disk database instead of the suite's :memory: — the same
    # pollution the auth fixture had, arriving by a slightly different door.
    previous_db = os.environ.get("DOSYNC_DB")
    dbp = tmp_path / "rename.db"
    os.environ["DOSYNC_DB"] = str(dbp)
    importlib.reload(srv)
    c = TestClient(srv.app)

    c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-rn", "ip": "1.2.3.4",
        "device_name": "wiz-rn"})
    r = c.patch("/v1/devices/wiz-rn", json={"device_name": "Dining room light"})
    assert r.status_code == 200

    reloaded = DoSyncHub(db_path=str(dbp))
    assert reloaded.registry.get("wiz-rn").device_name == "Dining room light"

    if previous_db is None:
        os.environ.pop("DOSYNC_DB", None)
    else:
        os.environ["DOSYNC_DB"] = previous_db
    importlib.reload(srv)


def test_renaming_is_recorded_with_the_previous_name(client):
    srv, c = client
    c.post("/v1/discovery/adopt", json={
        "adapter": "wiz", "device_id": "wiz-audit-rn", "ip": "1.2.3.4",
        "device_name": "Before"})
    c.patch("/v1/devices/wiz-audit-rn", json={"device_name": "After"})

    entries = [e for e in srv.hub.audit_log.entries()
               if e.get("type") == "device_renamed"
               and e.get("device_id") == "wiz-audit-rn"]
    assert entries and entries[-1]["previous_name"] == "Before"


def test_renaming_a_missing_device_is_a_404(client):
    srv, c = client
    assert c.patch("/v1/devices/nope", json={"device_name": "x"}).status_code == 404


def test_the_dashboard_can_rename_and_remove(client):
    """Claiming the bar is lower while leaving a terminal for someone's first
    mistake would not have been true."""
    srv, c = client
    page = c.get("/").text
    assert "renameDevice(" in page and "removeDevice(" in page


def test_an_empty_hub_says_what_to_do(client):
    """Ferreyra, on the panel: "if I open this and it is empty, I do not know
    whether the program is broken, my wifi is wrong, or I have to do
    something"."""
    srv, c = client
    page = c.get("/").text
    assert "No devices yet" in page and "Scan" in page


# ── Discovery is not an IP-only idea (2026-07-26) ───────────────────────────

def test_discovery_is_an_adapter_capability():
    """It used to be a central module with `if adapter == "wiz"`, which made
    every new transport edit shared code to become findable — and quietly made
    discovery mean "UDP broadcast" in a protocol that claims no such limit."""
    from dosync.adapters import DoSyncAdapter

    assert hasattr(DoSyncAdapter, "discover")
    assert hasattr(DoSyncAdapter, "can_discover")


def test_an_adapter_without_discovery_says_so_rather_than_failing():
    """Most adapters cannot discover and that is correct, not broken: a drone
    does not announce itself, a clinical device sits on a proprietary bus."""
    from dosync.adapters.notifications import NotificationAdapter

    a = NotificationAdapter()
    assert a.can_discover() is False


def test_ble_discovers_over_radio_not_ip():
    """The proof that the model was never IP-bound — Bluetooth devices announce
    themselves on a radio channel, with no broadcast address involved."""
    from dosync.adapters.ble import BLEAdapter

    a = BLEAdapter()
    assert a.can_discover() is True, "BLE must advertise itself as discoverable"


def test_scan_reports_which_transports_it_searched(client):
    """"Nothing found" means something different when Bluetooth was never
    scanned. The response must not let a user confuse the two."""
    srv, c = client
    body = c.get("/v1/discovery/scan").json()
    assert "searched" in body and "not_searchable" in body


def test_every_adapter_inherits_the_base_class():
    """NotificationAdapter duck-typed the interface instead of inheriting it —
    matching `adapter_name` and `execute` was enough to work, right up until the
    base class gained `discover`/`can_discover` and this adapter silently lacked
    both. Duck-typing an interface means every later addition to that interface
    skips you without a word."""
    import importlib
    import inspect
    import pkgutil

    import dosync.adapters as pkg
    from dosync.adapters import DoSyncAdapter

    offenders = []
    for mod in pkgutil.iter_modules(pkg.__path__):
        m = importlib.import_module(f"dosync.adapters.{mod.name}")
        for name, obj in inspect.getmembers(m, inspect.isclass):
            if obj.__module__ != m.__name__:
                continue
            if name.endswith(("Adapter", "Bridge")) and not issubclass(obj, DoSyncAdapter):
                offenders.append(f"{mod.name}.{name}")
    assert not offenders, f"adapters not inheriting DoSyncAdapter: {offenders}"
