"""A reader should not have to guess which file to open first.

Nine markdown files sat in the repository root with no hierarchy, and the one
called `TUTORIAL.md` — the name a stranger opens first — was not the shortest
path in. It builds a device that speaks the protocol and asks for Docker in
step 1. Meanwhile the README mentioned it once, on line 135, after the list of
people the project is *not* for.

And the principles of the project existed twice. `DESIGN-PRINCIPLES.md` and
`docs/DESIGN-PRINCIPLES.md` were both live for three months and drifted 265
lines apart, neither a stale copy of the other: the root held the founding
principle and the newest safety decision, `docs/` held the rules on adapters,
optional dependencies and how to write a test — one of which was consulted this
week to decide that a discovery library belongs in the core install. The code
referenced the one without the safety decision.

A project whose reason for existing is that a system must not say two different
things about itself cannot keep two versions of its own principles.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: What GitHub surfaces on a repository page, plus one editorial exception.
#: Everything else in the root is a claim that a visitor should read it.
ROOT_ALLOWED = {
    "README.md", "LICENSE", "CONTRIBUTING.md", "CHANGELOG.md", "GOVERNANCE.md",
    "SECURITY.md",
    # Editorial: it explains WHY the project is shaped this way, which is what
    # someone evaluating it reads — and they are not browsing docs/ yet.
    "DESIGN-PRINCIPLES.md",
    # Editorial: whether a project is alive is among the first things anyone
    # checks, and they check it in the root.
    "ROADMAP.md",
}

#: Files kept in the root only as pointers, because published articles and
#: external links reference these paths and cannot be edited.
ROOT_POINTERS = {"TUTORIAL.md", "COMPATIBILITY.md"}


def _root_markdown():
    return {p.name for p in REPO.glob("*.md")}


def test_the_root_holds_only_what_a_visitor_should_open_first():
    unexpected = _root_markdown() - ROOT_ALLOWED - ROOT_POINTERS
    assert not unexpected, (
        "files in the repository root that nothing says a visitor should read: "
        f"{sorted(unexpected)} — a file in the root is a claim that it is worth "
        "opening first")


def test_pointers_point_somewhere_that_exists():
    """Moving a file breaks links this project does not control."""
    for name in ROOT_POINTERS:
        path = REPO / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        targets = re.findall(r"\]\(([^)]+\.md)\)", text)
        assert targets, f"{name} is a pointer that points nowhere"
        for target in targets:
            assert (REPO / target).exists(), \
                f"{name} points at {target}, which does not exist"


def test_every_root_document_is_reachable_from_the_readme():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    unreferenced = []
    for name in _root_markdown():
        if name in ("README.md", "CHANGELOG.md") or name in ROOT_POINTERS:
            continue                      # the page itself, and reference material
        if name not in readme:
            unreferenced.append(name)
    assert not unreferenced, (
        f"root documents nothing links to: {unreferenced} — "
        "MULTIHUB-PHASE-A-DESIGN.md sat there for months with zero inbound links")


def test_the_principles_exist_once():
    """Two live copies drifted 265 lines apart, and the code read the older one."""
    copies = [p for p in REPO.rglob("DESIGN-PRINCIPLES.md")
              if ".git" not in p.parts and "build" not in p.parts]
    full = [p for p in copies if len(p.read_text(encoding="utf-8").splitlines()) > 40]
    assert len(full) == 1, (
        f"the project's principles exist in {len(full)} places: "
        f"{[str(p.relative_to(REPO)) for p in full]}")


def test_the_code_references_the_surviving_principles_file():
    source = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "docs/DESIGN-PRINCIPLES.md" not in source, \
        "the code still points at the copy that was merged away"


def test_the_readme_says_where_to_start():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## Where to start" in readme, \
        "the README has no map, so a reader guesses which file is the entry point"
    guide = readme[readme.index("## Where to start"):readme.index("## Quick start")]
    for destination in ("DEVICE-INTEGRATION.md", "spec/", "DESIGN-PRINCIPLES.md"):
        assert destination in guide, f"the map does not mention {destination}"


def test_the_device_tutorial_states_its_requirements_before_its_first_step():
    """Docker in step 1 is fine; Docker as a surprise in step 1 is not."""
    path = REPO / "docs" / "DEVICE-INTEGRATION.md"
    assert path.exists(), "the device tutorial is missing"
    text = path.read_text(encoding="utf-8")
    header = text[:text.index("## ")] if "## " in text else text
    assert "Docker" in header, \
        "the requirements are not stated before the first section"
    assert "README" in header, \
        "a reader who wanted to install a hub is not redirected"
