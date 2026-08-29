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
}

#: Files kept in the root only as pointers, because published articles and
#: external links reference these paths and cannot be edited.
ROOT_POINTERS = {"TUTORIAL.md", "COMPATIBILITY.md"}


#: `ROADMAP.md` was deleted rather than moved. It said the current release was
#: v0.3 three months after v0.5.0 shipped — a document whose whole purpose is to
#: tell a reader whether the project is alive, asserting that it was not. What
#: does not age from it (scope, the guarantee of independence from FamilyOS, the
#: questions still open) is in docs/VISION.md; what happened is in CHANGELOG.md,
#: which stays accurate without anyone maintaining a second copy.
DELETED_DOCS = {"ROADMAP.md"}


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


def test_the_roadmap_did_not_come_back_without_a_way_to_keep_it_true():
    """A roadmap that nobody updates asserts the project is dead.

    `ROADMAP.md` said the latest release was v0.3 three months after v0.5.0
    shipped. It is the document a stranger opens to find out whether anything is
    happening here, and it answered wrongly. Deleting it was the honest move
    because the release history already lives in the CHANGELOG, where it cannot
    drift.

    If it returns, something has to keep it true — that is what this test is
    asking for, not that a roadmap is forbidden.
    """
    for name in DELETED_DOCS:
        assert not (REPO / name).exists(), (
            f"{name} is back. It listed releases by hand and went three months "
            "stale; if it is worth having again, it needs a mechanism that "
            "keeps it accurate, not a promise to remember")


def test_what_does_not_age_survived_the_deletion():
    vision = REPO / "docs" / "VISION.md"
    assert vision.exists(), "the vision went out with the roadmap"
    text = vision.read_text(encoding="utf-8")
    assert "independent open protocol regardless" in text, (
        "the guarantee that DoSync stays independent of FamilyOS is gone — it is "
        "what answers someone deciding whether building on this ties them to a "
        "product")
    assert "Not on the roadmap" in text, \
        "the list of what the project will not do is gone"


def test_running_the_hub_permanently_is_documented_for_both_platforms():
    """The project shipped a systemd unit for months and never said so.

    The README referred to *"your service"* as though the reader already had
    one, `dosync.service` sat in the repository root unmentioned by any user
    document, and the reference deployment ran under systemd only because its
    operator wrote the unit himself. It surfaced while testing Windows, which
    was never the special case — it was where the omission became visible.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## Keeping the hub running" in readme, \
        "nothing explains how to keep a hub running across reboots"
    section = readme[readme.index("## Keeping the hub running"):]
    section = section[:section.index("\n## ", 10)]

    assert "systemctl enable" in section, "the Linux path is not documented"
    assert "Register-ScheduledTask" in section, "the Windows path is not documented"
    assert "DOSYNC_DB" in section, (
        "the Windows recipe omits DOSYNC_DB — without it a scheduled task "
        "writes to a different database, reports devices: 0, and looks like "
        "the inventory was lost")
    assert "Verified on" in section, \
        "the section does not say what was actually tested, only what to type"


def test_the_deployment_section_keeps_secrets_out_of_the_unit():
    """A unit file is copied by everyone who reads it, so what it models is
    what gets deployed. This one carried a live token until August 2026."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Keeping the hub running"):]
    section = section[:section.index("\n## ", 10)]
    assert "drop-in" in section, \
        "nothing tells the operator where credentials should live instead"
    assert "not tracked" in section or "untracked" in section


def test_connecting_an_agent_is_documented():
    """The README said "MCP" a dozen times and never explained how.

    A badge, a section on why MCP is the channel rather than the rival, a drone
    that flew a mission through it — and no instructions. The same pattern as
    the systemd unit that shipped for months while the page referred to *"your
    service"*: both surfaced by using the system as a stranger would.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "## Connecting an agent" in readme, \
        "nothing explains how to connect an agent over MCP"
    section = readme[readme.index("## Connecting an agent"):]
    section = section[:section.index("\n## ", 10)]

    assert "python -m dosync.mcp_server" in section, \
        "the section does not say how the server is started"
    assert "DOSYNC_TOKEN" in section and "DOSYNC_HUB_URL" in section, \
        "the environment the server reads is not documented"
    assert "<2.0" in section, \
        "nothing warns that the 2.x SDK cannot run this server"


def test_the_agent_section_survives_a_different_client():
    """The panel's test for this section: if another MCP client appears
    tomorrow, how much still applies?

    What DoSync controls — the command, the environment, the SDK bound — is the
    protocol's and documented outright. Where a client keeps its file is the
    client's, and appears as a dated example. Windows' byte-order mark and
    filesystem virtualisation are the platform's: they hold for any packaged
    client and any JSON written with PowerShell, which is why they are
    described as platform behaviour rather than as one product's quirks.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Connecting an agent"):]
    section = section[:section.index("\n## ", 10)]

    assert "documented by your client, not here" in section, \
        "the section does not hand client-specific configuration back to the " \
        "client, so it will rot when that client changes"
    assert "Verified 29 August 2026" in section, \
        "the section is not dated — a client's configuration changes without " \
        "notice and this one already has"
    # Whitespace-normalised: markdown wraps lines, and a test that breaks
    # because a sentence moved across a line break is testing the wrap, not
    # the meaning.
    flat = " ".join(section.split())
    assert "not as a recommendation" in flat, \
        "naming what was tested reads as endorsing it"
    assert "Any client that speaks MCP works" in section, \
        "nothing says the protocol is not tied to one client"


def test_the_agent_section_warns_the_config_holds_a_credential():
    """The example puts the hub's token in a file in plain text. This project
    shipped a live token in a public repository for three months."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Connecting an agent"):]
    section = section[:section.index("\n## ", 10)]
    assert "holds a credential" in section
    assert "out of version control" in section, \
        "nothing tells the reader to keep the file out of version control"
