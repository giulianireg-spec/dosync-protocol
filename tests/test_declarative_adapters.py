"""Devices described in a file instead of in code (2026-07-27).

Panel decision: most of what a hub needs to reach a device is not interesting
code — "send this request, read this field" — and requiring Python for it made
"domain-agnostic" mean "agnostic across the domains we already wrote".

The design constraint that decides whether this is useful, from Torres: **the
file must produce a capability MANIFEST, not a command table.** A file that only
said "POST /on turns it on" would let DoSync switch the device and leave it
invisible to everything that matters — no intent could select it, no policy
could name it, an emergency would pass it by.
"""
import json
from pathlib import Path

import pytest

from dosync.declarative import DeclarativeError, build_manifest, load_directory

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples" / "declarative"


def _minimal():
    return {
        "device": {"id": "d1", "name": "D1", "tags": ["light"]},
        "transport": {"kind": "http", "base_url": "http://x"},
        "actions": {"turn_on": {"type": "turn_on",
                                "request": {"method": "POST", "path": "/on"}}},
    }


# ── The capability requirement ──────────────────────────────────────────────

def test_an_action_without_a_type_is_refused():
    """The whole point. `type` is what the action MEANS to DoSync; without it
    the hub can perform the action and no intent can ever decide to."""
    data = _minimal()
    data["actions"]["turn_on"].pop("type")
    with pytest.raises(DeclarativeError) as e:
        build_manifest(data)
    assert "MEANS" in str(e.value), "the error must explain why type is needed"


def test_the_manifest_carries_tags_and_emergency_capability():
    data = _minimal()
    data["device"]["tags"] = ["light", "emergency"]
    data["device"]["emergency_capable"] = True
    m = build_manifest(data)
    assert "light" in m.tags and m.emergency_capable is True
    assert [a.type for a in m.actuators] == ["turn_on"]


def test_room_is_folded_into_tags():
    """Accepted for readability — `room: kitchen` is what someone reaches for —
    and stored as a tag, because that is how the resolver matches location."""
    data = _minimal()
    data["device"]["room"] = "kitchen"
    assert "kitchen" in build_manifest(data).tags


def test_a_device_with_no_tags_is_loaded_but_warned_about(caplog):
    """Not fatal — it is reachable by direct action — but it will never be
    selected by an intent, which is almost never what the author wanted."""
    import logging
    data = _minimal()
    data["device"]["tags"] = []
    with caplog.at_level(logging.WARNING):
        build_manifest(data)
    assert any("no tags" in str(r.msg) for r in caplog.records)


def test_the_transport_definition_travels_with_the_manifest():
    m = build_manifest(_minimal())
    assert m.adapter == "declarative"
    assert m.adapter_config["transport"]["base_url"] == "http://x"
    assert "turn_on" in m.adapter_config["actions"]


# ── Errors an operator can act on ───────────────────────────────────────────

def test_errors_name_the_file_and_the_fix():
    """The audience is someone editing YAML who has never read the spec.
    `KeyError: 'type'` tells them nothing about what to write."""
    with pytest.raises(DeclarativeError) as e:
        build_manifest({"actions": {}}, source="my-lamp.yaml")
    assert "my-lamp.yaml" in str(e.value) and "device:" in str(e.value)


def test_a_device_with_no_actions_is_refused():
    data = _minimal()
    data["actions"] = {}
    with pytest.raises(DeclarativeError):
        build_manifest(data)


def test_an_unparseable_file_is_skipped_not_fatal(tmp_path):
    """One malformed device description must not stop a house from starting —
    and the operator needs the hub running to fix it."""
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "good.json").write_text(json.dumps(_minimal()))
    loaded = load_directory(str(tmp_path))
    assert len(loaded) == 1 and loaded[0][0].device_id == "d1"


# ── The shipped examples ────────────────────────────────────────────────────

def test_every_example_loads():
    """Ferreyra, on the panel: "if they give me five examples and one looks like
    what I have, I copy it and change the IP". That makes the examples the
    deliverable, so a broken one is a broken feature."""
    loaded = load_directory(str(EXAMPLES))
    assert len(loaded) >= 5, "the panel asked for a range someone can recognise"
    ids = {m.device_id for m, _ in loaded}
    assert len(ids) == len(loaded), "example device ids must not collide"


def test_the_examples_are_not_all_household():
    """A format that only shows houses teaches that DoSync is for houses. The
    3D printer and the building lighting controller are there on purpose."""
    loaded = load_directory(str(EXAMPLES))
    tags = {t for m, _ in loaded for t in m.tags}
    assert {"workshop", "commercial"} & tags, \
        "at least one example must be outside the home"


def test_both_yaml_and_json_examples_exist():
    """They are interchangeable, and a building management system is more likely
    to emit JSON than YAML."""
    suffixes = {f.suffix for f in EXAMPLES.iterdir() if not f.name.startswith(".")}
    assert ".yaml" in suffixes and ".json" in suffixes


def test_examples_declare_emergency_capability_deliberately():
    """Both answers must appear, because the interesting one is `false`: a TV
    CAN display a warning, and in a care facility it should not. That is a
    deployment decision the file records."""
    loaded = load_directory(str(EXAMPLES))
    caps = {m.emergency_capable for m, _ in loaded}
    assert caps == {True, False}


# ── Panel review findings (2026-07-27) ──────────────────────────────────────
# Submitted before applying; the panel refused it with three blockers. All were
# behaviours that appear on the first day of real use.

def test_http_is_reachable_without_installing_an_extra():
    """B1. aiohttp sat in the `ha` extra, so a declarative device — whose only
    transport is HTTP — failed at EXECUTION, during an intent, rather than at
    load. Third appearance of this circle in two days."""
    import re

    pyproject = (REPO / "pyproject.toml").read_text()
    core = re.search(r"^dependencies = \[(.*?)^\]", pyproject, re.M | re.S)
    assert core and "aiohttp" in core.group(1), \
        "the only transport declarative adapters speak cannot need an extra"


def test_an_unsupported_transport_is_refused_at_load():
    """B3. A file naming a transport the hub cannot use loaded silently and
    failed when an intent reached it. The project already separates "searched"
    from "not searchable" so that "found nothing" cannot be mistaken for "did
    not look"; this is the same confusion with worse timing."""
    data = _minimal()
    data["transport"] = {"kind": "mqtt", "broker": "tcp://x"}
    with pytest.raises(DeclarativeError) as e:
        build_manifest(data, source="thing.yaml")
    assert "not supported" in str(e.value) and "code adapter" in str(e.value)


def test_http_without_a_base_url_is_refused():
    data = _minimal()
    data["transport"] = {"kind": "http"}
    with pytest.raises(DeclarativeError):
        build_manifest(data)


def test_duplicate_device_ids_are_reported(tmp_path, caplog):
    """R1. The later file won by alphabetical accident, so an operator could
    edit the losing file forever with no effect."""
    import logging

    a = _minimal(); a["device"]["id"] = "same"
    b = _minimal(); b["device"]["id"] = "same"; b["device"]["name"] = "Other"
    (tmp_path / "a.json").write_text(json.dumps(a))
    (tmp_path / "b.json").write_text(json.dumps(b))

    with caplog.at_level(logging.ERROR):
        loaded = load_directory(str(tmp_path))
    assert len(loaded) == 1, "one device, not a silent overwrite"
    assert any("already declared" in str(r.msg) for r in caplog.records)


def test_unedited_example_values_are_flagged(tmp_path, caplog):
    """R2. Copying an example and forgetting the token is the most likely first
    mistake; the request would carry the literal placeholder."""
    import logging

    data = _minimal()
    data["transport"]["headers"] = {"Authorization": "REPLACE_WITH_YOUR_TOKEN"}
    with caplog.at_level(logging.WARNING):
        build_manifest(data, source="copied.yaml")
    assert any("unedited example values" in str(r.msg) for r in caplog.records)
