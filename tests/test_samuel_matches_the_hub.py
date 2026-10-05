"""SAMUEL, the governance specification, says what the reference hub does.

spec/SAMUEL-SPEC-v0.1.md was extracted from the protocol on 2026-10-05 so that a
hub can be governed whatever describes its devices. A document nobody checks
drifts -- the JSON Schemas once rejected what the hub produced -- so this binds
it to the implementation: every refusal the hub can return, every universal class
it seeds, every certification check and protocol rule it names.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = (REPO / "spec" / "SAMUEL-SPEC-v0.1.md").read_text(encoding="utf-8")


def _codes(section_title: str) -> set[str]:
    part = DOC.split(section_title, 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", part, re.M))


def test_every_intent_refusal_is_specified():
    import dosync.server as srv
    assert _codes("### 5.1") == set(srv._REJECTION_REASONS)


def test_every_proposal_refusal_is_specified():
    from dosync.resolvers import CapabilityMatchingResolver
    assert _codes("### 5.2") == set(CapabilityMatchingResolver.PROPOSAL_REFUSALS)


def test_every_direct_action_refusal_is_specified():
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert _codes("### 5.3") == set(re.findall(r'_refuse\(\d+, "([a-z_]+)"', server))


def test_the_universal_classes_are_the_ones_a_hub_seeds(tmp_path):
    from dosync.db import DoSyncDB
    db = DoSyncDB(str(tmp_path / "g.db"))
    db.init()
    seeded = {c["name"]: c["location_role"] for c in db.list_intent_classes() if c.get("domain") == "universal"}
    part = DOC.split("### 2.1", 1)[1].split("\n## ", 1)[0]
    documented = dict(re.findall(r"^\| `([a-z_]+)` \| [^|]+ \| (restricts|informs) \|", part, re.M))
    assert documented == seeded


def test_every_certification_check_named_exists():
    certify = (REPO / "dosync" / "certify.py").read_text(encoding="utf-8")
    existing = set(re.findall(r'"(C\d{2})  ', certify))
    named = set(re.findall(r"\bC\d{2}\b", DOC.split("## 8. Conformance", 1)[1]))
    assert named and named <= existing, sorted(named - existing)


def test_every_protocol_rule_named_exists():
    spec = (REPO / "spec" / "DoSync-SPEC-v0.1.md").read_text(encoding="utf-8")
    rules_section = spec.split("### 6.8 Governance rules", 1)[1].split("\n## 7.", 1)[0]
    rules = set(re.findall(r"\n([1-8])\. \*\*", rules_section))
    named = {n for cell in re.findall(r"§6\.8 rules? ([\d, ]+)", DOC) for n in re.findall(r"\d", cell)}
    assert named and named <= rules, sorted(named - rules)
    for section in ("5.9", "7.8"):
        assert f"### {section}" in spec or f"## {section}" in spec, section
