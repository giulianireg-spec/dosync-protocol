"""A declarative file says where its device is, and is the source of truth for it.

A declarative file is written by the operator, so the location it declares is
the operator's statement -- as authoritative as PATCH. It goes in the location
field (a path), not in tags. Every start re-registers each device from its file,
so the rules are: a file that declares a location wins, and a move it causes is
audited; a file that declares none keeps the location the operator set by PATCH;
and PATCH refuses to change a location the file will re-apply, naming the file.
The bundled examples are also the template the adapter-drafting tool shows a
model, so they must teach this form.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dosync.declarative import DeclarativeError, build_manifest, load_directory, register_declared

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "declarative"


def _data(**device):
    return {
        "device": {"id": "decl-loc-1", "name": "D", "tags": ["light"], **device},
        "transport": {"kind": "http", "base_url": "http://x"},
        "actions": {"turn_on": {"type": "turn_on",
                                "request": {"method": "POST", "path": "/on"}}},
    }


def test_a_location_path_goes_in_the_field():
    m = build_manifest(_data(location="plant-1/line-3/cell-2"), source="cell.yaml")
    assert m.location == "plant-1/line-3/cell-2"
    assert m.adapter_config["location_from_file"] == "cell.yaml"


def test_no_location_means_the_file_does_not_own_one():
    assert "location_from_file" not in build_manifest(_data()).adapter_config


def test_room_and_location_that_disagree_are_refused():
    with pytest.raises(DeclarativeError, match="same field"):
        build_manifest(_data(location="kitchen", room="hallway"))


def test_a_malformed_location_is_refused_with_its_reason():
    with pytest.raises(DeclarativeError, match="device.location"):
        build_manifest(_data(location="/plant-1"))


def _hub():
    import dosync.server as srv
    return srv


def test_reloading_a_file_with_no_location_keeps_the_one_the_operator_set():
    srv = _hub()
    register_declared(srv.hub, [(build_manifest(_data(id="decl-keep")), {})])
    srv.hub.registry.get("decl-keep").location = "main-bedroom"
    register_declared(srv.hub, [(build_manifest(_data(id="decl-keep")), {})])
    assert srv.hub.registry.get("decl-keep").location == "main-bedroom", \
        "a restart moved the device back to nowhere"


def test_a_file_that_moves_a_device_is_audited():
    srv = _hub()
    register_declared(srv.hub, [(build_manifest(_data(id="decl-move", location="lab-1"),
                                                source="probe.yaml"), {})])
    register_declared(srv.hub, [(build_manifest(_data(id="decl-move", location="lab-2"),
                                                source="probe.yaml"), {})])
    moves = [e for e in srv.hub.audit_log.entries()
             if e.get("type") == "device_relocated" and e.get("device_id") == "decl-move"]
    assert moves and moves[-1]["location"] == "lab-2" and moves[-1]["source"] == "probe.yaml"


def test_patch_refuses_a_location_the_file_owns_and_names_the_file():
    srv = _hub()
    register_declared(srv.hub, [(build_manifest(_data(id="decl-owned", location="lab-1"),
                                                source="owned.yaml"), {})])
    client = TestClient(srv.app)
    r = client.patch("/v1/devices/decl-owned", json={"location": "lab-9"})
    assert r.status_code == 409 and "owned.yaml" in r.json()["detail"]
    assert client.patch("/v1/devices/decl-owned", json={"device_name": "Renamed"}).status_code == 200, \
        "renaming was refused along with the location"


def test_the_bundled_examples_teach_the_location_field():
    for path in sorted(EXAMPLES.iterdir()):
        text = path.read_text(encoding="utf-8")
        assert "\n  room:" not in text and '"room"' not in text, \
            f"{path.name} teaches `room`, the old form the drafting tool shows a model"
    for manifest, _ in load_directory(str(EXAMPLES)):
        if manifest.location:
            assert manifest.location not in manifest.tags, \
                f"{manifest.device_id} states its location twice: as the field and as a tag"
