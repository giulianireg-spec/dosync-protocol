"""SAMUEL, the governance specification, says what the reference hub does.

spec/SAMUEL-SPEC-v0.2.md is normative on its own (v0.1, extracted from the
protocol on 2026-10-05, deferred to the protocol's §6.8). A document nobody checks
drifts -- the JSON Schemas once rejected what the hub produced -- so this binds it
to the implementation: every refusal the hub can return, every universal class
it seeds, every conformance check it names, and the kernel's own lists.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = (REPO / "spec" / "SAMUEL-SPEC-v0.2.md").read_text(encoding="utf-8")


def _codes(section_title: str) -> set[str]:
    part = DOC.split(section_title, 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)` \|", part, re.M))


def test_every_intent_refusal_is_specified():
    import dosync.server as srv
    from dosync.samuel import INTENT_REFUSALS
    assert _codes("### 7.1") == set(srv._REJECTION_REASONS)
    assert set(INTENT_REFUSALS) <= _codes("### 7.1")


def test_every_proposal_refusal_is_specified():
    from dosync.samuel import PROPOSAL_REFUSALS
    from dosync.resolvers import CapabilityMatchingResolver
    assert _codes("### 7.2") == set(PROPOSAL_REFUSALS) == set(CapabilityMatchingResolver.PROPOSAL_REFUSALS)


def test_every_direct_action_refusal_is_specified():
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert _codes("### 7.3") == set(re.findall(r'_refuse\(\d+, "([a-z_]+)"', server))


def test_the_universal_classes_are_the_ones_a_hub_seeds(tmp_path):
    from dosync.db import DoSyncDB
    db = DoSyncDB(str(tmp_path / "g.db"))
    db.init()
    seeded = {c["name"]: c["location_role"] for c in db.list_intent_classes() if c.get("domain") == "universal"}
    part = DOC.split("### 3.1", 1)[1].split("\n## ", 1)[0]
    documented = dict(re.findall(r"^\| `([a-z_]+)` \| [^|]+ \| (restricts|informs) \|", part, re.M))
    assert documented == seeded


def test_every_conformance_check_named_exists_and_every_check_is_named():
    suite = (REPO / "dosync" / "samuel_certify.py").read_text(encoding="utf-8")
    existing = set(re.findall(r'r\.add\("(S\d{2})"', suite))
    named = set(re.findall(r"\bS\d{2}\b", DOC.split("## 12. Conformance", 1)[1]))
    assert named == existing, (sorted(named - existing), sorted(existing - named))


def test_every_rule_a_guarantee_names_exists():
    rules = set(re.findall(r"^\*\*(R\d+) —", DOC, re.M))
    table = DOC.split("## 6. Guarantees", 1)[1].split("\n## ", 1)[0]
    named = set(re.findall(r"\bR\d+\b", table))
    assert rules == {f"R{i}" for i in range(1, 13)}
    assert named and named <= rules, sorted(named - rules)


def test_the_protocol_defers_to_samuel():
    spec = (REPO / "spec" / "DoSync-SPEC-v0.1.md").read_text(encoding="utf-8")
    rules_section = spec.split("### 6.8 Governance rules", 1)[1].split("\n## 7.", 1)[0]
    assert "SAMUEL-SPEC-v0.2.md" in rules_section and "normative" in rules_section


def test_the_binding_answers_what_the_spec_lists():
    from dosync.samuel_http import router
    def shape(path):                      # parameter names are the binding's own
        return re.sub(r"\{[^}]*\}", "{}", path)
    listed = {(m, shape(p)) for m, p in re.findall(r"^\| `(GET|PUT|POST) (/samuel/v0[^`?]*)", DOC, re.M)}
    served = {(m, shape(r.path)) for r in router.routes for m in r.methods}
    assert listed == served, (sorted(listed - served), sorted(served - listed))
