"""The shipped adapters describe devices in the protocol's terms.

Found reviewing what reaches PyPI before 0.7.0:
- Action descriptions an agent reads in a device's manifest were Spanish --
  "Encender relay", "Ajustar brillo 0-100%", "Temperatura", "Cerrar"/"Abrir".
  A word detector would not have caught "Brillo": the labels here are held to a
  closed English list, as the MCP's are.
- The Home Assistant bridge gave every thermostat and blind the deprecated tag
  `climate`.
- wiz_manifest, shelly_manifest and matter_manifest took `room=` and appended it
  to the tags -- a place stored as a category, the form protocol 0.5 retired.
  They take `location=` now, into the location field; `room=` is its
  deprecated alias.
"""
import logging
import re
from pathlib import Path

import pytest

from dosync.adapters.homeassistant import HA_DOMAIN_MAP
from dosync.adapters.matter import matter_manifest
from dosync.adapters.shelly import shelly_manifest
from dosync.adapters.wiz import wiz_manifest

VOCAB = Path(__file__).resolve().parent.parent / "spec" / "TAG-VOCABULARY.md"

DESCRIPTION_WORDS = {
    "brightness", "close", "color", "colour", "dimmer", "lock", "off", "on", "open",
    "plug", "position", "predefined", "relay", "rgb", "scene", "target",
    "temperature", "the", "turn", "unlock", "wiz",
    "a", "alarm", "an", "arm", "device", "effect", "hvac", "message", "recording",
    "set", "show", "start", "streaming", "supports", "trigger",
}


def _manifests():
    yield wiz_manifest("w", "W", "10.0.0.1")
    for t in ("relay", "dimmer", "plug", "rgbw"):
        yield shelly_manifest("s", "S", "10.0.0.2", device_type=t)
    for t in ("light", "switch", "cover", "lock", "climate"):
        yield matter_manifest("m", "M", "light.x", device_type=t)


def _descriptions():
    for m in _manifests():
        for a in m.actuators:
            yield a.description
    for domain, spec in HA_DOMAIN_MAP.items():
        for a in spec.get("actuators", []):
            yield a.description


def test_every_action_description_uses_the_english_word_list():
    unknown = sorted({(w, d) for d in _descriptions()
                      for w in re.findall(r"[A-Za-zÀ-ÿ]+", d) if w.lower() not in DESCRIPTION_WORDS})
    assert not unknown, ("action descriptions use words outside the English list "
                         f"(translate, or add an English word on purpose): {unknown}")


def _vocabulary():
    text = VOCAB.read_text(encoding="utf-8")
    tags = set(re.findall(r"^\| `([a-z0-9-]+)` \|", text.split("## Intent-to-tag mapping")[0], re.M))
    deprecated = set(re.findall(r"^\| `([a-z0-9_-]+)` \|", text.split("## Deprecated tags")[1].split("\n## ")[0], re.M))
    return tags, deprecated


def test_the_home_assistant_bridge_tags_from_the_vocabulary():
    tags, deprecated = _vocabulary()
    bad = sorted({(domain, t) for domain, spec in HA_DOMAIN_MAP.items()
                  for t in spec.get("tags", []) if t not in tags or t in deprecated})
    assert not bad, f"HA domains carry tags outside the vocabulary or deprecated: {bad}"


@pytest.mark.parametrize("build", [
    lambda **kw: wiz_manifest("w", "W", "10.0.0.1", **kw),
    lambda **kw: shelly_manifest("s", "S", "10.0.0.2", **kw),
    lambda **kw: matter_manifest("m", "M", "light.x", **kw),
])
def test_a_helper_puts_the_place_in_the_location_field(build, caplog):
    m = build(location="plant-1/line-3")
    assert m.location == "plant-1/line-3" and "plant-1/line-3" not in m.tags
    with caplog.at_level(logging.WARNING, logger="dosync.adapters"):
        old = build(room="lab")
    assert old.location == "lab" and "lab" not in old.tags, "room= still becomes a tag"
    assert any("deprecated" in r.message for r in caplog.records)
    with pytest.raises(ValueError):
        build(location="/plant")


def test_the_default_emergency_call_names_the_place_not_a_home():
    import dosync.resolvers as r
    src = Path(r.__file__).read_text(encoding="utf-8")
    assert 'get("message", "Emergency at home")' not in src, "the household default is back"
    assert "f\"Emergency at {intent.context['location']}\"" in src


def test_presence_reports_members_present_and_keeps_the_deprecated_name():
    from fastapi.testclient import TestClient
    import dosync.server as srv
    body = TestClient(srv.app).get("/v1/presence").json()
    assert "members_present" in body and body["members_present"] == body["members_home"]
