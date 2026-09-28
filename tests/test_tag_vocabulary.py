"""The tag vocabulary describes the classes a hub actually ships with.

Since 2026-06-03 a hub seeds five universal intent classes and each deployment
registers its own. spec/TAG-VOCABULARY.md was never updated: its mapping table,
which said it reflected "the default resolution tags configured at hub
initialization", listed thirteen classes -- eight a hub does not have
(away_mode, bedtime_routine, monitor_health, ...) -- and was wrong even for the
five real ones. Nothing compared it with the seed. It also mattered beyond the
spec: adapter_drafting.py reads this file into the prompt of the model that
drafts adapters.

Both tables are now read and compared with what a hub seeds.
"""
import re
from pathlib import Path

from dosync.db import DoSyncDB

VOCAB = Path(__file__).resolve().parent.parent / "spec" / "TAG-VOCABULARY.md"


def _seed():
    db = DoSyncDB(":memory:")
    db.init()
    return {c["name"]: c for c in db.list_intent_classes()}


def _codes(cell: str) -> set:
    return set(re.findall(r"`([^`]+)`", cell))


def _table(after_heading: str) -> list[list[str]]:
    text = VOCAB.read_text(encoding="utf-8")
    section = text[text.index(after_heading):]
    rows = []
    for line in section.splitlines()[1:]:
        if line.startswith("## ") or line.startswith("### "):
            break
        if line.startswith("| `"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def test_the_mapping_table_is_exactly_what_a_hub_seeds():
    seed = _seed()
    rows = {_codes(r[0]).pop(): r for r in _table("## Intent-to-tag mapping")}
    assert set(rows) == set(seed), f"table lists {sorted(rows)}, a hub seeds {sorted(seed)}"
    for name, (_, urgency, role, tags, actuators, sensors) in rows.items():
        c = seed[name]
        assert urgency == c["urgency"], f"{name}: urgency"
        assert role == c["location_role"], f"{name}: location role"
        assert _codes(tags) == set(c["resolution_tags"]), f"{name}: resolution tags"
        assert _codes(actuators) == set(c["resolution_actuators"]), f"{name}: actuators"
        assert _codes(sensors) == set(c.get("resolution_sensors") or []), f"{name}: sensors"


def test_the_semantic_role_table_names_only_classes_that_list_the_tag():
    seed = _seed()
    for row in _table("### 4. Semantic role"):
        tag = _codes(row[0]).pop()
        listed = _codes(row[-1])
        actual = {n for n, c in seed.items() if tag in c["resolution_tags"]}
        assert listed == actual, f"`{tag}` says it is listed by {sorted(listed)}; the seed says {sorted(actual)}"


def test_the_package_carries_the_same_vocabulary_as_the_spec():
    """The drafting prompt reads the package's copy -- the one a `pip install`
    has. The spec is edited in spec/; a copy that drifted would teach a model
    yesterday's vocabulary."""
    package_copy = VOCAB.parent.parent / "dosync" / "spec" / "TAG-VOCABULARY.md"
    assert package_copy.read_bytes() == VOCAB.read_bytes(), \
        "dosync/spec/TAG-VOCABULARY.md differs from spec/TAG-VOCABULARY.md: copy it"
