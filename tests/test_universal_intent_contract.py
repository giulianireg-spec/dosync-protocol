"""The universal intent resolution contract — spec §6.4.1 vs the implementation.

The tags that decide which devices a universal intent selects lived only in
`_seed_universal_intents()`. Nothing in `spec/` stated them, so a second
implementation written from the specification alone would resolve
`control_access` differently and still pass certification — and two hubs that
both conform while behaving differently is the one failure a protocol cannot
have. Measured cost of the gap: an industrial registry tagging its door
`access` + `security` (both standard vocabulary) scored F1 0.00 on
`control_access`, roughly a quarter of the multi-domain agnosticism gap.

Writing the table into the spec fixes that and creates a second copy of one
fact — which this project has watched diverge five times (the version in four
places, DOSYNC_DB vs DOSYNC_DB_PATH, the auth setting, requirements.txt vs
pyproject, the test count in README vs the site). So the copies are pinned to
each other here rather than trusted.

The test parses the spec table and compares it to what a freshly seeded
database actually contains — not to a literal restated in this file, which
would be a third copy.
"""
import json
import re
from pathlib import Path

import pytest

from dosync.db import DoSyncDB

SPEC = Path(__file__).resolve().parent.parent / "spec" / "DoSync-SPEC-v0.1.md"


def _parse_spec_contract():
    """Extract §6.4.1's table as {intent: (tags, actuators)}."""
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("#### 6.4.1 Resolution contract (normative)")
    section = text[start:]
    # Only the FIRST table of the section: scoping by character count would
    # swallow neighbouring tables and make the test assert about the wrong rows.
    rows, in_table = [], False
    for line in section.splitlines():
        if line.startswith("|"):
            in_table = True
            rows.append(line)
        elif in_table:
            break
    contract = {}
    for line in rows:
        m = re.match(r"^\|\s*`(\w+)`\s*\|(.+?)\|(.+?)\|\s*$", line)
        if not m:
            continue
        name, tags_cell, act_cell = m.group(1), m.group(2), m.group(3)

        def cell(c):
            c = c.strip()
            if c in ("*(none)*", "*(ninguno)*", ""):
                return []
            return re.findall(r"`([^`]+)`", c)

        contract[name] = (cell(tags_cell), cell(act_cell))
    return contract


def _seeded_contract(tmp_path):
    """What a freshly initialized hub database actually holds."""
    db = DoSyncDB(str(tmp_path / "contract.db"))
    db.init()
    rows = db._conn.execute(
        "SELECT name, resolution_tags, resolution_actuators "
        "FROM intent_classes WHERE is_universal = 1"
    ).fetchall()
    db.close()
    return {r["name"]: (json.loads(r["resolution_tags"]),
                        json.loads(r["resolution_actuators"])) for r in rows}


def test_spec_section_exists():
    """The contract must be IN the specification, which is the whole point."""
    text = SPEC.read_text(encoding="utf-8")
    assert "#### 6.4.1 Resolution contract (normative)" in text, \
        "spec §6.4.1 is missing — the resolution contract is unspecified again"


def test_spec_lists_all_five_universals():
    contract = _parse_spec_contract()
    assert set(contract) == {"ensure_safety", "alert_anomaly", "control_access",
                             "report_status", "notify"}, \
        f"§6.4.1 does not list exactly the five universals: {sorted(contract)}"


def test_spec_matches_implementation(tmp_path):
    """The normative table and the seed are one fact; they must not drift."""
    spec = _parse_spec_contract()
    seeded = _seeded_contract(tmp_path)
    assert set(spec) == set(seeded), (
        f"intents differ — spec: {sorted(spec)}, implementation: {sorted(seeded)}")
    for name in sorted(spec):
        spec_tags, spec_acts = spec[name]
        seed_tags, seed_acts = seeded[name]
        assert sorted(spec_tags) == sorted(seed_tags), (
            f"{name}: resolution_tags disagree — "
            f"spec §6.4.1 {sorted(spec_tags)} vs seed {sorted(seed_tags)}")
        assert sorted(spec_acts) == sorted(seed_acts), (
            f"{name}: resolution_actuators disagree — "
            f"spec §6.4.1 {sorted(spec_acts)} vs seed {sorted(seed_acts)}")


def test_report_status_declares_nothing(tmp_path):
    """Empty is deliberate, not an omission: it means read-only over all sensors.

    Pinned separately because a well-meaning future edit that 'completes' the
    empty row would silently change what a status query means.
    """
    tags, acts = _seeded_contract(tmp_path)["report_status"]
    assert tags == [] and acts == [], \
        "report_status gained a resolution — that changes what a status query means"


def test_resolution_tags_come_from_the_standard_vocabulary():
    """A resolution tag no conforming device would declare selects nothing.

    TAG-VOCABULARY.md is SHOULD for devices — an operator may tag a lock
    `front-door`. It is effectively MUST for the other direction: an intent
    whose resolution names a tag outside the vocabulary can only be matched by
    a device that guessed the same non-standard word.
    """
    vocab_file = SPEC.parent / "TAG-VOCABULARY.md"
    vocabulary = set(re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|",
                                vocab_file.read_text(encoding="utf-8"),
                                flags=re.MULTILINE))
    assert vocabulary, "could not parse TAG-VOCABULARY.md"
    for name, (tags, _acts) in _parse_spec_contract().items():
        for tag in tags:
            assert tag in vocabulary, (
                f"{name} resolves on `{tag}`, which is not in TAG-VOCABULARY.md — "
                "no device tagged from the standard vocabulary can match it")


def test_resolver_spec_example_uses_vocabulary_tags():
    """The example a reader copies must not teach tags outside the vocabulary.

    It used to read ["lighting", "climate", "access"]; `lighting` and `access`
    are not in TAG-VOCABULARY.md, so an intent class written from this example
    would never select a lock tagged `lock`.
    """
    resolver_spec = (SPEC.parent / "RESOLVER-SPEC-v0.3.md").read_text(encoding="utf-8")
    vocabulary = set(re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|",
                                (SPEC.parent / "TAG-VOCABULARY.md").read_text(encoding="utf-8"),
                                flags=re.MULTILINE))
    for block in re.findall(r'"resolution_tags":\s*\[([^\]]*)\]', resolver_spec):
        for tag in re.findall(r'"([^"]+)"', block):
            assert tag in vocabulary, (
                f"RESOLVER-SPEC example uses `{tag}`, absent from TAG-VOCABULARY.md")


def test_location_tags_are_an_open_namespace():
    """The protocol does not know or care where a device is.

    Location is a fact about one installation, not shared vocabulary: a
    deployment names its own places and the resolver compares strings. The spec
    used to list ten locations, all residential, in a normative document that an
    industrial or clinical implementer reads — which taught that the protocol
    was a house even though the code never restricted anything.

    Asserted with a location no list would ever contain, on purpose: if this
    passes, no enumeration crept in.
    """
    from dosync.hub import CapabilityMatchingResolver, CapabilityRegistry
    from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                               Intent, IntentClass, Urgency)

    registry = CapabilityRegistry()
    registry.register(CapabilityManifest(
        device_id="alarm-deathstar-01", device_name="alarm", manufacturer="t",
        model="t", firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["alarm", "emergency", "death-star"], emergency_capable=False,
        sensors=[], actuators=[ActuatorSpec(id="alarm", type="alarm", description="a")]))
    registry.register(CapabilityManifest(
        device_id="alarm-elsewhere-01", device_name="alarm", manufacturer="t",
        model="t", firmware="1", category=DeviceCategory.ACTUATOR,
        tags=["alarm", "emergency", "sector-7g"], emergency_capable=False,
        sensors=[], actuators=[ActuatorSpec(id="alarm", type="alarm", description="a")]))

    resolver = CapabilityMatchingResolver(registry)
    resolution = {"tags": ["emergency", "alarm"], "actuators": ["alarm"]}
    resolver._get_resolution = lambda intent: dict(resolution)
    intent = Intent(intent=IntentClass("ensure_safety"), urgency=Urgency.ALERT,
                    context={"location": "death-star"})

    scored = {d["device_id"]: d["score"]
              for d in resolver.explain(intent).get("included", [])}
    assert "alarm-deathstar-01" in scored, \
        "a device was rejected for declaring a location outside the spec's examples"
    assert scored["alarm-deathstar-01"] > scored["alarm-elsewhere-01"], \
        "the location bonus did not apply to an operator-defined location"


def test_the_spec_says_location_is_open():
    """The property above must be discoverable by reading, not only by testing."""
    text = (SPEC.parent / "TAG-VOCABULARY.md").read_text(encoding="utf-8")
    assert "open namespace" in text and "MUST NOT reject a location tag" in text, \
        "TAG-VOCABULARY no longer states that locations are deployment-defined"
