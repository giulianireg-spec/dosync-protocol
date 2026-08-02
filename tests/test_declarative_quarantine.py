"""A device whose declarative file disappeared (2026-07-27).

Panel blocker B2, and the one that made the feature dishonest: editing a file
UPDATED the device, deleting it did NOT remove the device. Torres: "the worst of
both worlds — the user learns from the first half that the file is the source of
truth and discovers in the second that it was not."

The obvious fix is dangerous, and Benítez named it before it was written: a
directory that failed to mount looks exactly like a directory whose files were
removed, and a hub that reacts to the first by deregistering a building is worse
than one that asks. So a device whose file is gone is QUARANTINED — out of
intents, still in the inventory, recorded in the chain, removed only when an
operator says so.
"""
import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent


def _device(device_id):
    return {
        "device": {"id": device_id, "name": device_id, "tags": ["light", "emergency"],
                   "emergency_capable": True},
        "transport": {"kind": "http", "base_url": "http://x"},
        "actions": {"turn_on": {"type": "turn_on",
                                "request": {"method": "POST", "path": "/on"}}},
    }


@pytest.fixture
def hub_with_files(tmp_path):
    """A hub reading declarative files, restored afterwards.

    Reloads `dosync.server` and puts the environment back — the same teardown
    the auth and rename fixtures needed after each polluted the suite by
    dropping variables instead of restoring them.
    """
    files = tmp_path / "files"
    files.mkdir()
    previous = {k: os.environ.get(k)
                for k in ("DOSYNC_DB", "DOSYNC_DECLARATIVE_DIR", "DOSYNC_AUTH")}
    os.environ["DOSYNC_DB"] = str(tmp_path / "a.db")
    os.environ["DOSYNC_DECLARATIVE_DIR"] = str(files)
    # Without this the API returns 401 and the assertions fail on a KeyError
    # that says nothing about authentication — which is how the first run of
    # this file looked like a quarantine bug.
    os.environ["DOSYNC_AUTH"] = "false"

    import dosync.server as srv

    def start():
        importlib.reload(srv)
        return srv, TestClient(srv.app)

    yield files, start

    for k, v in previous.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    importlib.reload(srv)


def test_a_deleted_file_quarantines_its_device(hub_with_files):
    from dosync.hub import is_quarantined

    files, start = hub_with_files
    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    (files / "printer.json").write_text(json.dumps(_device("printer")))

    srv, c = start()
    with c:
        assert {m.device_id for m in srv.hub.registry.active()} == {"lamp", "printer"}

    (files / "printer.json").unlink()
    srv, c = start()
    with c:
        assert {m.device_id for m in srv.hub.registry.active()} == {"lamp"}, \
            "a device the operator deleted must not be planned into intents"
        assert "printer" in {m.device_id for m in srv.hub.registry.all()}, \
            "but it must remain visible — hiding it is how it gets forgotten"
        assert is_quarantined(srv.hub.registry.get("printer"))


def test_quarantine_is_recorded_and_reversible(hub_with_files):
    from dosync.hub import is_quarantined

    files, start = hub_with_files
    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    (files / "printer.json").write_text(json.dumps(_device("printer")))
    srv, c = start()
    with c:
        pass

    (files / "printer.json").unlink()
    srv, c = start()
    with c:
        types = [e["type"] for e in srv.hub.audit_log.entries()]
        assert "device_quarantined" in types

    (files / "printer.json").write_text(json.dumps(_device("printer")))
    srv, c = start()
    with c:
        assert not is_quarantined(srv.hub.registry.get("printer")), \
            "restoring the file must put the device back in service"
        assert "device_unquarantined" in [e["type"] for e in srv.hub.audit_log.entries()]


def test_deleting_the_last_file_still_quarantines_its_device(hub_with_files):
    """Found on the reference deployment, not by these tests.

    The original guard only ran the quarantine pass if at least one file had
    loaded — protecting against a failed mount, per Benítez. But the operator
    deleted their ONLY declarative file and the device stayed active, silently:
    the same guard that protects a lost directory also blocked the legitimate
    case of removing the last device.

    Resolved by remembering how many files were seen last time. Going from some
    to none is a change the hub WITNESSED, and quarantine is the safe response
    because quarantine is not deletion — the device stays in the inventory and
    returns the moment the file does.
    """
    from dosync.hub import is_quarantined

    files, start = hub_with_files
    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    srv, c = start()
    with c:
        assert not is_quarantined(srv.hub.registry.get("lamp"))

    (files / "lamp.json").unlink()          # the only one
    srv, c = start()
    with c:
        assert is_quarantined(srv.hub.registry.get("lamp")), \
            "deleting the last file must still take its device out of intents"
        assert srv.hub.registry.get("lamp") is not None, \
            "and must not delete it — quarantine is reversible, deletion is not"


def test_a_directory_that_was_never_populated_changes_nothing(hub_with_files):
    """The other half. A first start that finds no files, or a directory that
    was already empty, has witnessed nothing and must not act."""
    from dosync.hub import is_quarantined

    files, start = hub_with_files
    srv, c = start()                        # no files at all, ever
    with c:
        assert srv.hub.db.get_setting("declarative_file_count") == 0

    srv, c = start()                        # and again
    with c:
        assert not [d for d in srv.hub.registry.all() if is_quarantined(d)]


def test_a_device_returns_when_its_file_does(hub_with_files):
    """Which is what makes quarantining a vanished directory acceptable: if the
    disk comes back, so does the device, without anyone intervening."""
    from dosync.hub import is_quarantined

    files, start = hub_with_files
    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    srv, c = start()
    with c:
        pass
    (files / "lamp.json").unlink()
    srv, c = start()
    with c:
        assert is_quarantined(srv.hub.registry.get("lamp"))

    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    srv, c = start()
    with c:
        assert not is_quarantined(srv.hub.registry.get("lamp"))


def test_the_api_reports_quarantined_devices(hub_with_files):
    files, start = hub_with_files
    (files / "lamp.json").write_text(json.dumps(_device("lamp")))
    (files / "printer.json").write_text(json.dumps(_device("printer")))
    srv, c = start()
    with c:
        pass
    (files / "printer.json").unlink()
    srv, c = start()
    with c:
        body = c.get("/v1/devices").json()
        assert body["quarantined"] == 1 and body["active"] == 1
        printer = [d for d in body["devices"] if d["device_id"] == "printer"][0]
        assert printer["quarantined"] is True and printer["quarantine_reason"]
