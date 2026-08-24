"""Deployment files must only set variables the code actually reads.

Found 2026-07-22 by installing the package and watching where the database went:
the shipped Dockerfile and docker-compose.yml set DOSYNC_DB_PATH, the hub reads
DOSYNC_DB. Nothing failed, nothing warned — the container simply wrote its
database inside the image instead of the mounted volume, so every `docker
compose down` destroyed the audit chain. A silent contract mismatch between
deployment config and code, which is exactly the class of failure this project
refuses to leave undetected.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _declared_env_vars(path: Path) -> set[str]:
    """DOSYNC_* variables a deployment file SETS."""
    if not path.exists():
        return set()
    text = path.read_text()
    found = set()
    # Dockerfile:  ENV DOSYNC_X=...     compose:  - DOSYNC_X=...
    for m in re.finditer(r"^\s*(?:ENV\s+|-\s+)(DOSYNC_[A-Z0-9_]+)\s*=", text, re.M):
        found.add(m.group(1))
    return found


def _vars_read_by_code() -> set[str]:
    """DOSYNC_* variables the package READS, anywhere."""
    found = set()
    for py in (REPO / "dosync").rglob("*.py"):
        found |= set(re.findall(r"DOSYNC_[A-Z0-9_]+", py.read_text()))
    return found


def test_every_env_var_set_by_deployment_files_is_read_by_the_code():
    read = _vars_read_by_code()
    # Variables consumed by tooling other than the hub process itself.
    tooling = {"DOSYNC_DEMO_TOKEN", "DOSYNC_CA_CERT", "DOSYNC_HUB_URL"}

    for fname in ("Dockerfile", "docker-compose.yml"):
        declared = _declared_env_vars(REPO / fname)
        orphans = declared - read - tooling
        assert not orphans, (
            f"{fname} sets {sorted(orphans)}, which no code in dosync/ reads. "
            "A deployment variable nobody reads is silently ignored — this is how "
            "DOSYNC_DB_PATH sent the database outside its volume."
        )


def test_database_path_variable_is_the_one_the_hub_reads():
    """The specific regression: the compose files must name DOSYNC_DB."""
    compose = (REPO / "docker-compose.yml").read_text()
    dockerfile = (REPO / "Dockerfile").read_text()
    assert "DOSYNC_DB=" in compose, "compose must set DOSYNC_DB"
    assert "DOSYNC_DB=" in dockerfile, "Dockerfile must set DOSYNC_DB"


def test_deprecated_alias_still_works_but_warns(monkeypatch, caplog):
    """A deployment still carrying the old variable must keep its data, with a
    warning — not silently fall back to the default path and lose it."""
    import logging

    import dosync.server as srv

    monkeypatch.delenv("DOSYNC_DB", raising=False)
    monkeypatch.setenv("DOSYNC_DB_PATH", "/tmp/legacy-name.db")
    with caplog.at_level(logging.WARNING):
        resolved = srv._resolve_db_path()
    assert resolved == "/tmp/legacy-name.db"
    assert any("deprecated alias" in str(r.msg) for r in caplog.records)


def test_dosync_db_wins_over_the_alias(monkeypatch):
    import dosync.server as srv
    monkeypatch.setenv("DOSYNC_DB", "/tmp/correct.db")
    monkeypatch.setenv("DOSYNC_DB_PATH", "/tmp/legacy.db")
    assert srv._resolve_db_path() == "/tmp/correct.db"


def test_startup_log_does_not_hardcode_a_port():
    """The startup line announced port 47200 regardless of where the hub was
    listening. A log that lies about the basics erodes trust in the ones that
    matter."""
    src = (REPO / "dosync" / "server.py").read_text()
    assert 'started on port 47200' not in src


# ── Version must have exactly one source ─────────────────────────────────────

def test_version_is_declared_in_exactly_one_place():
    """Until 2026-07-22 the version lived in three places that disagreed:
    dosync/__init__.py said 0.1.0, server.py hardcoded 0.4.0 four times, and
    pyproject.toml carried its own copy. `import dosync; dosync.__version__`
    reported a number three releases stale."""
    server_src = (REPO / "dosync" / "server.py").read_text()
    assert not re.search(r'"\d+\.\d+\.\d+"', server_src.replace('"0.4"', "")), \
        "server.py hardcodes a version literal; import dosync.__version__ instead"

    pyproject = (REPO / "pyproject.toml").read_text()
    assert 'attr = "dosync.__version__"' in pyproject, \
        "pyproject must read the version from the package, not restate it"


def test_reported_version_matches_the_package():
    """What /v1/status reports must be what the installed package says."""
    import dosync
    from fastapi.testclient import TestClient

    import dosync.server as srv
    client = TestClient(srv.app)
    body = client.get("/v1/status").json()
    assert body["version"] == dosync.__version__
    assert body["protocol_version"] == dosync.__protocol_version__


# ── The one non-developer entry point (2026-07-26) ──────────────────────────

def test_dashboard_ships_inside_the_package():
    """H6 in the horizon list — "everything is curl and tokens" — was worse than
    recorded: a browser dashboard existed, but it sat at the repository root, so
    `pip install dosync` never carried it, and after the packaging move the
    handler looked for it beside server.py where it was not. The single entry
    point that needs no terminal was missing from the package and broken in a
    clone at the same time."""
    from pathlib import Path

    import dosync
    shipped = Path(dosync.__file__).parent / "dashboard.html"
    assert shipped.exists(), \
        "dashboard.html must live inside the package to survive an install"

    pyproject = (REPO / "pyproject.toml").read_text()
    # The declaration itself, not any mention of the filename — the comment
    # above that line names it too, so a substring search passes even after the
    # declaration is deleted. Sixth instance of "assert the mechanism".
    import re
    decl = re.search(r"^dosync\s*=\s*\[(.+)\]", pyproject, re.M)
    assert decl and "dashboard.html" in decl.group(1), \
        "dashboard.html must be declared as package-data, or the wheel omits it"


def test_dashboard_is_served():
    from fastapi.testclient import TestClient

    import dosync.server as srv
    r = TestClient(srv.app).get("/")
    assert r.status_code == 200
    assert "<html" in r.text[:300].lower()


def test_a_missing_dashboard_answers_instead_of_crashing(monkeypatch):
    """The fallback used to be `FileResponse.__new__(FileResponse)` — an
    uninitialised object that raises AttributeError inside the framework. The
    one person who arrived without a terminal got a stack trace."""
    from pathlib import Path

    from fastapi.testclient import TestClient

    import dosync.server as srv

    real = Path.exists
    monkeypatch.setattr(
        Path, "exists",
        lambda self: False if self.name == "dashboard.html" else real(self))

    r = TestClient(srv.app).get("/")
    assert r.status_code == 200, "a missing file is not a server error"
    assert "/docs" in r.text, "it must point somewhere useful"


def test_dashboard_does_not_hardcode_a_version():
    """It displayed "Hub v0.1" for three releases — a fourth hardcoded version
    source, and the only one a visitor ever sees. Checks the RENDERED markup,
    not the file, so an explanatory comment cannot satisfy it."""
    import re

    from fastapi.testclient import TestClient

    import dosync.server as srv
    page = TestClient(srv.app).get("/").text
    body = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    assert not re.search(r"Hub v\d+\.\d+", body), \
        "the version must come from /v1/status, not the markup"


def test_dashboard_intents_come_from_the_deployment():
    """The launcher held eight hardcoded home scenarios — Good Morning, Bedtime,
    blinds, coffee — which made the project's only visual artifact contradict
    its central claim of domain agnosticism. Anyone evaluating DoSync for a
    plant or a care facility opened this page and saw a house.

    Comments are stripped before checking: the explanation above the grid names
    those scenarios on purpose, and a substring search would pass on it — the
    recurring 'assert the mechanism' trap.
    """
    import re

    from fastapi.testclient import TestClient

    import dosync.server as srv
    page = TestClient(srv.app).get("/").text
    body = re.sub(r"<!--.*?-->", "", page, flags=re.S)

    for scenario in ("Good Morning", "Bedtime", "Blinds up", "Coffee on",
                     "Away Mode", "Remind Chore"):
        assert scenario not in body, \
            f"'{scenario}' is a home scenario baked into a domain-agnostic protocol"

    assert 'id="intentGrid"' in body, "the launcher must have a container to fill"
    assert "/v1/intent-classes" in body, \
        "and must populate it from what this deployment registered"


def test_intent_classes_endpoint_gives_the_dashboard_what_it_needs():
    """The rendering depends on these fields; a change to the endpoint that
    dropped one would leave the launcher blank with no test failing."""
    from fastapi.testclient import TestClient

    import dosync.server as srv
    data = TestClient(srv.app).get("/v1/intent-classes").json()
    assert data["intent_classes"], "a hub must expose its intent classes"
    for c in data["intent_classes"]:
        assert "name" in c and "urgency" in c, \
            "the launcher needs a label and an urgency to sort and colour by"


def test_dashboard_explains_how_to_get_in():
    """The project's author opened the dashboard, saw "API token…", and could
    not work out where to obtain one. Then, once told, the token did not work —
    because the page hardcoded http:// and could not talk to an HTTPS hub. Two
    separate walls at the first screen.

    Keys are hashed and shown once, so the honest guidance is not "look it up".
    All three real options must be named: choose one, generate one, or turn
    authentication off for a hub nothing outside can reach.
    """
    from fastapi.testclient import TestClient

    import dosync.server as srv
    page = TestClient(srv.app).get("/").text

    assert "keys create --token" in page, "choosing your own must be offered"
    assert "DOSYNC_AUTH=false" in page, "so must running without one"
    assert "showTokenHelp" in page


def test_dashboard_follows_the_scheme_it_was_loaded_over():
    """Hardcoded http:// and ws:// meant the dashboard could not work on any TLS
    deployment: a browser blocks an HTTPS page from fetching http://, so the
    request never left the tab and the UI sat at "disconnected" with no error.
    The hub warns at every startup that TLS is unconfigured — and the moment an
    operator complied, its own interface stopped working."""
    import re

    from fastapi.testclient import TestClient

    import dosync.server as srv
    page = TestClient(srv.app).get("/").text
    body = re.sub(r"<!--.*?-->", "", page, flags=re.S)

    assert not re.search(r"const HUB\s*=\s*`http://", body), \
        "the scheme must come from window.location, not be assumed"
    assert "window.location.protocol" in body
    assert not re.search(r":47200`", body), \
        "the port must come from window.location.host too"


def test_the_browser_warning_is_explained_somewhere():
    """The hub tells operators to run setup_pki.sh. About ninety seconds later
    their browser says "Not secure" and strikes through https — and nothing in
    the project explained why, or how to finish. An instruction whose predictable
    outcome is an alarming warning is an incomplete instruction."""
    readme = (REPO / "README.md").read_text()
    assert "Not secure" in readme, "the warning operators will see must be named"
    assert "add-trusted-cert" in readme, "and the way to resolve it, per platform"
    assert "update-ca-certificates" in readme
    assert "does **not** mean the connection is unencrypted" in readme, \
        "and what it does NOT mean, since that is the part people get wrong"


# ── Positioning (2026-07-26) ────────────────────────────────────────────────

def test_readme_answers_the_comparison_an_evaluator_will_make():
    """The integral audit found the closest competitor is W3C Web of Things —
    Thing Description is a finished W3C Recommendation backed by Oracle,
    Siemens, Intel and others — and that the README never mentioned it. An
    informed evaluator makes that comparison anyway; the only question is
    whether they make it with our answer or without it."""
    readme = (REPO / "README.md").read_text()
    assert "Web of Things" in readme, "the nearest standard must be named"
    assert "Thing Description" in readme
    assert "MCP" in readme and "distribution channel" in readme, \
        "and MCP framed as a channel rather than a rival"


def test_readme_does_not_make_absolute_security_claims():
    """Oracle's "unbreakable" was broken within days of the campaign and became
    a case study. For a protocol whose value proposition IS honesty — states
    like `unverifiable` and `indeterminate` exist precisely to avoid claiming
    what cannot be known — an absolute security claim would be self-refuting.

    The disclaimer sentence is removed before checking, because it necessarily
    contains the word it disclaims. The first version of this test failed on the
    project's own denial — an assertion arguing with itself.
    """
    import re

    readme = (REPO / "README.md").read_text()
    assert "None of these is claimed to be unbreakable" in readme, \
        "the limit should be stated outright, not merely avoided"

    # Strip the sentence that names the word in order to reject it.
    checked = re.sub(r"None of these is claimed[^.]*\.", "", readme).lower()
    for absolute in ("unbreakable", "100% secure", "impossible to tamper",
                     "cannot be hacked", "fully secure", "completely secure"):
        assert absolute not in checked, \
            f"'{absolute}' is not a claim this protocol can support"


def test_readme_links_resolve():
    """A citation that does not resolve is worse than no citation in front of an
    evaluator — and one of them did not: the claim state machine was cited as
    living in the protocol spec when it is in the consistency model."""
    import re

    readme = (REPO / "README.md").read_text()
    links = re.findall(r"\]\((?!http)([^)#]+)", readme)
    missing = [l for l in sorted(set(links)) if not (REPO / l.strip()).exists()]
    assert not missing, f"broken local links in README: {missing}"


def test_install_instructions_cover_the_target_platform():
    """`pip install dosync` fails on Raspberry Pi OS, Debian 12+ and Ubuntu
    23.04+ with a wall of text about externally-managed-environment (PEP 668) —
    and the Raspberry Pi is the machine most likely to be running a hub. The
    project's own author hit it. An install instruction that fails on the target
    platform is the first wall in front of exactly the user H6 is about."""
    readme = (REPO / "README.md").read_text()
    assert "externally-managed-environment" in readme, \
        "the error users will actually see must be named, so a search finds it"
    assert "pipx install dosync" in readme, \
        "and the correct tool offered — DoSync is an application, not a library"
    assert "break-system-packages" in readme, \
        "including the option we do not recommend, and why"


def test_pipx_inject_is_documented_as_a_general_pattern_not_a_vendor_case():
    """A Windows reinstall confirmed a vendor library installed with plain
    `pip install` after `pipx install dosync` lands in the wrong environment —
    `pipx` isolates the hub in its own venv, and the failure is silent, because
    the hub cannot distinguish "not installed" from "installed somewhere else".

    The fix documented is `pipx inject dosync <package>`. It must be framed as
    the general pattern for ANY optional dependency installed after the hub —
    not tied to WiZ or any other single vendor's product, since the protocol
    presumes no domain and the README should not either. This project has
    caught itself doing exactly that before: a reference adapter once nagged
    every hub to install `pywizlight` on every start, regardless of whether the
    operator owned anything from that vendor.
    """
    readme = (REPO / "README.md").read_text()
    assert "pipx inject dosync" in readme, \
        "the fix for the isolated-environment problem is not documented"
    assert "not specific to any one vendor" in readme, \
        "pipx inject must be framed as a general pattern, not a per-vendor fix"

    # The instruction itself must not single out a domestic-appliance vendor
    # as the worked example — that is exactly the framing this project has
    # already had to walk back once.
    section_start = readme.index("pipx inject dosync")
    section = readme[max(0, section_start - 400):section_start + 600]
    for domestic_vendor in ("wiz ", "philips hue", "lifx", "nanoleaf"):
        assert domestic_vendor not in section.lower(), (
            f"the generic pipx inject explanation names '{domestic_vendor.strip()}' "
            "as its worked example, re-introducing the home-appliance framing "
            "this project already removed from the reference adapter's warning"
        )


# ── Dependencies must not live in two places (2026-07-31) ───────────────────

def test_requirements_and_pyproject_agree():
    """CI went red for three commits because they had drifted.

    The workflow installed `-r requirements.txt` while the package declared its
    dependencies in `pyproject.toml`. Adding bleak, pyyaml, aiohttp and paho-mqtt
    to the package installed them for every user and for nobody in CI, so four
    tests failed there and passed everywhere else — the most confusing shape a
    failure can take.

    `requirements.txt` is kept because a CI job derives MINIMUM versions from it
    to test against the declared floor. It is not kept as a second opinion about
    what the package needs.
    """
    import re

    pyproject = (REPO / "pyproject.toml").read_text()
    core = re.search(r"^dependencies = \[(.*?)^\]", pyproject, re.M | re.S)
    assert core, "core dependencies must be declared in pyproject.toml"

    def _name(spec):
        return re.split(r"[><=!\[]", spec, 1)[0].strip().lower()

    declared = {_name(d) for d in re.findall(r'"([^"]+)"', core.group(1))}
    listed = {_name(l) for l in (REPO / "requirements.txt").read_text().splitlines()
              if l.strip() and not l.strip().startswith("#")}

    missing = declared - listed
    assert not missing, (
        f"in pyproject.toml but not requirements.txt: {sorted(missing)} — CI "
        f"installs the package now, but the floor job reads this file, and a "
        f"dependency it does not know about is never tested at its minimum")

    extra = listed - declared
    assert not extra, (
        f"in requirements.txt but not pyproject.toml: {sorted(extra)} — a user "
        f"installing the package would not get these")


def test_ci_installs_the_package_itself():
    """What CI exercises must be what `pip install dosync` produces. Installing
    a parallel list is how the two came apart in the first place."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "pip install -e ." in ci, \
        "CI must install the package, not a hand-maintained mirror of its deps"


def test_declared_floors_are_versions_that_exist_and_build():
    """A minimum version nobody can install is not a minimum, it is a wish.

    Two of the four dependencies added this week declared floors that cannot be
    installed on the Python CI runs: `pyyaml==6.0` fails to build (the Cython
    `cython_sources` error, fixed in 6.0.1) and `aiohttp==3.8.0` has no Python
    3.12 support. The floor job caught both — which is what it is for — but only
    after they had been merged, so this records the specific versions rather
    than re-deriving the lesson next time.

    Checked statically: actually installing them is the floor job's work, and
    duplicating a CI job inside the suite would make every local run pay for it.
    """
    import re

    reqs = (REPO / "requirements.txt").read_text()

    def floor(pkg):
        m = re.search(rf"^{pkg}>=([0-9.]+)", reqs, re.M)
        return m.group(1) if m else None

    assert floor("pyyaml") != "6.0", \
        "pyyaml 6.0 cannot build on modern Python; 6.0.1 is the real floor"
    assert floor("aiohttp") not in ("3.8.0", "3.8"), \
        "aiohttp 3.8.x has no Python 3.12 support; 3.9.0 is the real floor"


def test_extras_do_not_contradict_core_floors():
    """`dosync[ha]` declared aiohttp>=3.8.0 while core declared >=3.9.0. pip
    resolves the intersection so nothing breaks, but two numbers for one fact is
    how the previous four divergences started."""
    import re

    pyproject = (REPO / "pyproject.toml").read_text()
    core = re.search(r"^dependencies = \[(.*?)^\]", pyproject, re.M | re.S).group(1)

    core_floors = {}
    for spec in re.findall(r'"([^"]+)"', core):
        m = re.match(r"([A-Za-z0-9_.-]+)>=([0-9.]+)", spec)
        if m:
            core_floors[m.group(1).lower()] = m.group(2)

    extras = pyproject.split("[project.optional-dependencies]", 1)
    if len(extras) < 2:
        return
    for spec in re.findall(r'"([^"]+)"', extras[1].split("[project.urls]")[0]):
        m = re.match(r"([A-Za-z0-9_.-]+)>=([0-9.]+)", spec)
        if m and m.group(1).lower() in core_floors:
            assert m.group(2) == core_floors[m.group(1).lower()], (
                f"{m.group(1)}: extras say >={m.group(2)}, core says "
                f">={core_floors[m.group(1).lower()]} — one fact, one number")


def test_the_configuration_reference_is_current():
    """H8. The hub reads 48 `DOSYNC_*` settings, each documented where it was
    introduced and nowhere together. The backlog entry that ASKED for this
    document mistyped one while being written (`DOSYNC_AUTH_MAX_LIVE` for
    `DOSYNC_AUDIT_MAX_LIVE`) — if the author gets one wrong summarising his own
    work, an operator reading prose has no chance.

    So the reference is generated, and this fails when it drifts. A
    hand-maintained table would be the fifth thing here to hold one fact in two
    places.
    """
    from dosync.config_reference import render, scan

    target = REPO / "docs" / "CONFIGURATION.md"
    assert target.exists(), "run: python3 -m dosync.config_reference --write"
    assert target.read_text() == render(scan()), \
        "docs/CONFIGURATION.md is stale — run python3 -m dosync.config_reference --write"


def test_the_reference_does_not_invent_settings():
    """The generator scanned its own docstring, which shows the pattern it looks
    for, and reported `DOSYNC_X` as a real setting. A generator that
    hallucinates is worse than a hand-written table."""
    from dosync.config_reference import scan

    found = scan()
    assert "DOSYNC_X" not in found
    assert "DOSYNC_ASSURANCE" in found and "DOSYNC_AUDIT_MAX_LIVE" in found


# ── The specification must keep up with the implementation (2026-08-01) ─────

def test_every_audit_event_type_is_in_the_spec():
    """A session audit found 32 event types in the code and a specification with
    NO table of event types — not seven rows missing, the table absent.

    This matters more than tidiness: the audit chain's value is that somebody
    who did not write the hub can read it. An operator whose deployment records
    `device_quarantined` has to be able to look it up, and a second
    implementation has to know what to emit for the same situation. A chain of
    names only its author understands is a log, not evidence.
    """
    from dosync.spec_coverage import documented_event_types, emitted_event_types

    missing = emitted_event_types() - documented_event_types()
    assert not missing, (
        f"emitted but not in spec §7.8: {sorted(missing)} — a hub that records "
        f"an event nobody can look up is keeping a private log")


def test_every_endpoint_is_in_the_spec():
    """27 of 41 endpoints were outside the specification. The gap was not
    created by any one session — it accumulated — but a protocol that grows
    faster than its specification stops being a protocol and becomes a popular
    program (Benítez)."""
    from dosync.spec_coverage import report

    _, missing = report()
    assert not missing, f"exposed but not in spec §7.9: {sorted(missing)}"


def test_the_changelog_covers_the_release():
    """The 0.4.2 changelog listed six discovery entries and omitted five
    features, two with security implications: an endpoint accepting unencrypted
    messages, and a loader for third-party code. An operator has a right to know
    what enters their hub."""
    changelog = (REPO / "CHANGELOG.md").read_text()
    section = changelog.split("## [0.4.2]")[1].split("## [0.4.1]")[0].lower()

    for topic in ("declarative", "quarantin", "entry point", "heartbeat",
                  "discovery", "auth"):
        assert topic in section, f"0.4.2 ships {topic} and does not mention it"


def test_no_shipped_work_sits_under_unreleased():
    """Two `[Unreleased]` sections were found at publication time, one holding
    0.4.1's contents nine days after it shipped and one holding 0.4.2's. A
    reader saw published functionality marked as not released — and one of the
    entries was a BEHAVIOUR CHANGE to `audit-verify` that could break somebody's
    cron.

    An `[Unreleased]` section is legitimate; one describing a version that
    already exists is not.
    """
    changelog = (REPO / "CHANGELOG.md").read_text()
    import re

    versions = re.findall(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", changelog, re.M)
    assert versions, "the changelog must name released versions"

    import dosync
    assert dosync.__version__ in versions, (
        f"version {dosync.__version__} is about to ship and has no changelog "
        f"section — found {versions}")

    # An Unreleased section is fine; one sitting ABOVE the current version means
    # shipped work is filed under it.
    unreleased = changelog.find("## [Unreleased]")
    current = changelog.find(f"## [{dosync.__version__}]")
    if unreleased != -1:
        assert unreleased < current, \
            "an [Unreleased] section below the current version holds shipped work"


def test_the_declarative_examples_ship_with_the_package():
    """The panel called the examples the deliverable, not the appendix — the
    format is learned by finding one that resembles your device and changing the
    address. They lived only at the repository root, so anyone who installed
    from PyPI and never cloned had none."""
    from pathlib import Path

    import dosync
    shipped = Path(dosync.__file__).parent / "examples" / "declarative"
    assert shipped.is_dir(), "examples must live inside the package to be installed"
    assert len(list(shipped.glob("*.yaml"))) + len(list(shipped.glob("*.json"))) >= 5

    pyproject = (REPO / "pyproject.toml").read_text()
    import re
    decl = re.search(r"^dosync\s*=\s*\[(.+)\]", pyproject, re.M)
    assert decl and "examples" in decl.group(1), \
        "and be declared as package-data, or the wheel omits them"


def test_the_ci_workflow_does_not_hard_code_the_version():
    """CI was red on `main` for weeks because its own configuration asserted
    `"v0.3" in output` — a string written when the workflow was created and
    never updated as the project reached 0.4.x. The code was fine; the check
    was stale.

    Fifth instance in this project of one fact living in two places: the version
    in four files, DOSYNC_DB vs DOSYNC_DB_PATH, requirements against pyproject,
    the site's numbers, and now the workflow. The pattern is reliable enough to
    be worth a test each time it appears.
    """
    import re

    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()

    hardcoded = re.findall(r'assert\s+"v\d+\.\d+', ci)
    assert not hardcoded, (
        f"CI asserts a literal version {hardcoded} — derive it from "
        f"dosync.__version__ instead, or it goes stale on the next release")
