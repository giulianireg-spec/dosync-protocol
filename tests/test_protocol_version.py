"""The protocol version is one number, said the same way everywhere, and never
moves without the specification saying what changed.

Protocol 0.5 changed behavior -- a location in an intent's context now
restricts where it acts -- and the number had two sources: dosync/__init__.py
and a separate literal in server.py that fed the X-DoSync-Protocol-Version
header. Bumping one would have made /v1/status and the header disagree. The
specification must also carry the version and a "Changes in" section for it:
another implementation reads the spec, not this package's CHANGELOG, and a
number that changes behavior without saying how is not a version.
"""
import logging
import re
from pathlib import Path

from fastapi.testclient import TestClient

import dosync

SPEC = Path(__file__).resolve().parent.parent / "spec" / "DoSync-SPEC-v0.1.md"


def test_the_header_the_status_and_the_package_agree():
    import dosync.server as srv
    r = TestClient(srv.app).get("/v1/status")
    assert r.headers["X-DoSync-Protocol-Version"] == dosync.__protocol_version__
    assert r.json()["protocol_version"] == dosync.__protocol_version__


def test_the_spec_states_the_version_and_what_changed_in_it():
    spec = SPEC.read_text(encoding="utf-8")
    v = dosync.__protocol_version__
    row = re.search(r"\| \*\*Protocol version\*\* \| `([0-9.]+)` \|", spec)
    assert row and row.group(1) == v, "the spec's §10 table states another protocol version"
    assert re.search(rf"^### 10\.\d+ Changes in {re.escape(v)} ", spec, re.M), \
        f"protocol {v} has no 'Changes in {v}' section in the spec"


def _patch(client, body):
    return client.patch("/v1/devices/proto-dep-1", json=body)


def test_room_carries_the_deprecation_headers_and_location_does_not():
    import dosync.server as srv
    client = TestClient(srv.app)
    assert client.post("/v1/devices/register", json={
        "device_id": "proto-dep-1", "device_name": "d", "manufacturer": "t", "model": "t",
        "firmware": "1", "category": "actuator", "tags": ["light"],
        "actuators": [{"id": "turn_on", "type": "turn_on", "description": ""}]}).status_code == 200
    old = _patch(client, {"room": "kitchen"})
    assert old.status_code == 200 and "Deprecation" in old.headers and "Sunset" in old.headers
    new = _patch(client, {"location": "kitchen"})
    assert "Deprecation" not in new.headers and "Sunset" not in new.headers
    adopted = client.post("/v1/discovery/adopt", json={"device_id": "proto-dep-2", "room": "lab"})
    assert "Deprecation" in adopted.headers


def test_a_declarative_room_is_warned_about(caplog):
    from dosync.declarative import build_manifest
    data = {"device": {"id": "proto-dep-3", "name": "D", "tags": ["light"], "room": "lab"},
            "transport": {"kind": "http", "base_url": "http://x"},
            "actions": {"turn_on": {"type": "turn_on",
                                    "request": {"method": "POST", "path": "/on"}}}}
    with caplog.at_level(logging.WARNING, logger="dosync.declarative"):
        build_manifest(data, source="old.yaml")
    assert any("room:" in r.message and "deprecated" in r.message for r in caplog.records)
