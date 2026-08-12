"""Where a deployment keeps its configuration and its state (2026-08-08).

Found while preparing to reflash the reference hub: its configuration lived in
nine places, four of which the author did not remember existed, and three of
those were inside a git clone. A `git clean -fdx` would have destroyed a
42,000-entry audit chain and the CA's private key.

Worth naming as the contradiction it was: this protocol argues that its evidence
survives someone with root access, and it did not survive someone tidying a
repository. The sophisticated threat was covered and the trivial one was not.

The rules below are the panel's, and each exists because getting it wrong has a
specific cost.
"""
import os
from pathlib import Path

import pytest

from dosync import paths

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """A deployment rooted entirely under tmp_path, in user mode."""
    monkeypatch.setenv("DOSYNC_INSTALL_MODE", "user")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for v in ("DOSYNC_DB", "DOSYNC_CERTS_DIR", "DOSYNC_POLICIES",
              "DOSYNC_CHECKPOINT_DIR", "DOSYNC_ARCHIVE_DIR",
              "DOSYNC_DECLARATIVE_DIR"):
        monkeypatch.delenv(v, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path, work


# ── The two modes ───────────────────────────────────────────────────────────

def test_user_and_system_modes_resolve_differently(monkeypatch):
    """`pipx` installs as an ordinary user who cannot write to /etc or
    /var/lib; a systemd unit runs as root and should use them. Picking one
    layout would have broken the other."""
    monkeypatch.setenv("DOSYNC_INSTALL_MODE", "system")
    assert paths.state_dir() == Path("/var/lib/dosync")
    assert Path("/etc/dosync") in paths.config_dirs()

    monkeypatch.setenv("DOSYNC_INSTALL_MODE", "user")
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state")
    assert paths.state_dir() == Path("/tmp/xdg-state/dosync")


def test_config_cascades_from_user_to_system(clean_env, monkeypatch):
    """User location first, system second — so a per-user install finds its own
    file, and falls back to a system-wide default when there is none."""
    tmp, _ = clean_env
    dirs = paths.config_dirs()
    assert dirs[0] == tmp / "cfg" / "dosync"
    assert dirs[-1] == Path("/etc/dosync")


# ── Compatibility: the rule that protects the existing deployment ───────────

def test_an_explicit_variable_always_wins(clean_env, monkeypatch):
    """An operator who said where wins over any inference. Every existing
    deployment is configured this way and must be unaffected."""
    monkeypatch.setenv("DOSYNC_DB", "/tmp/chosen.db")
    assert paths.resolve_state("dosync.db", "DOSYNC_DB") == Path("/tmp/chosen.db")


def test_an_existing_database_in_the_working_directory_keeps_being_used(clean_env):
    """The rule that matters most. A running deployment with 42,000 entries must
    not come up with an empty chain because the layout changed — that would lose
    exactly the history this protocol exists to protect."""
    _, work = clean_env
    (work / "dosync.db").write_text("existing deployment")

    assert paths.resolve_state("dosync.db", "DOSYNC_DB") == work / "dosync.db"


def test_the_legacy_path_is_used_with_a_warning_not_silently(clean_env, caplog):
    """Using it quietly would leave the operator unaware their data sits inside
    a source tree."""
    import logging

    _, work = clean_env
    (work / "dosync.db").write_text("x")
    with caplog.at_level(logging.WARNING):
        paths.resolve_state("dosync.db", "DOSYNC_DB")
    assert any("git clean" in str(r.msg) or "working directory" in str(r.msg)
               for r in caplog.records)


def test_data_in_two_places_is_an_error_not_a_choice(clean_env):
    """Choosing wrong means writing to one chain and auditing the other, and
    only the operator knows which is current."""
    tmp, work = clean_env
    (work / "dosync.db").write_text("old")
    modern = paths.state_dir()
    modern.mkdir(parents=True, exist_ok=True)
    (modern / "dosync.db").write_text("new")

    with pytest.raises(RuntimeError) as e:
        paths.resolve_state("dosync.db", "DOSYNC_DB")
    assert "will not guess" in str(e.value)
    assert str(work) in str(e.value) and str(modern) in str(e.value), \
        "the error must name both paths — the operator has to go look at them"


def test_a_clean_install_uses_the_state_directory(clean_env):
    tmp, _ = clean_env
    p = paths.resolve_state("dosync.db", "DOSYNC_DB")
    assert str(p).startswith(str(paths.state_dir()))


# ── PKI ─────────────────────────────────────────────────────────────────────

def test_the_pki_directory_is_created_private(clean_env):
    """A private key in a directory with whatever permissions it inherited is
    the kind of detail nobody notices until an audit asks."""
    d = paths.certs_dir()
    assert d.exists()
    assert oct(d.stat().st_mode)[-3:] == "700", \
        f"certs directory is {oct(d.stat().st_mode)[-3:]}, expected 700"


def test_existing_certificates_are_not_abandoned(clean_env):
    """Regenerating a CA orphans every device certificate issued from it, and
    invalidates the certificate an operator installed on their laptop."""
    _, work = clean_env
    legacy = work / "certs"
    legacy.mkdir()
    (legacy / "ca.crt").write_text("existing CA")

    assert paths.certs_dir() == legacy


# ── Visibility ──────────────────────────────────────────────────────────────

def test_the_hub_can_report_which_paths_it_resolved(clean_env):
    """An operator editing /etc/dosync/policies.json while the hub reads
    ~/.config/dosync/policies.json believes they are protected by a policy the
    hub never loaded. Silent divergence between what is edited and what runs is
    the failure this project keeps finding."""
    d = paths.describe()
    assert set(d) == {"mode", "config_dirs", "state_dir", "certs_dir"}
    assert d["mode"] in ("user", "system")


def test_configuration_is_found_in_the_config_directory(clean_env):
    tmp, _ = clean_env
    cfg = tmp / "cfg" / "dosync"
    cfg.mkdir(parents=True)
    (cfg / "policies.json").write_text("{}")

    assert paths.resolve_config("policies.json", "DOSYNC_POLICIES") == \
        cfg / "policies.json"


def test_absent_configuration_is_none_not_a_missing_path(clean_env, monkeypatch):
    """A hub with no deployment policies is conforming — absence is a normal
    state and callers should not have to tell it from a path that happens not to
    exist.

    The system leg of the cascade is isolated here, and only here: this is the
    one test whose premise is that NOTHING is configured, and /etc/dosync is
    part of "everything". Left live, it asserted a property of the host rather
    than of the code — green on a development laptop, red on the reference
    deployment, whose only sin was having /etc/dosync/policies.json. The
    cascade's real system path stays asserted, unmocked, in
    test_config_cascades_from_user_to_system.
    """
    tmp, _ = clean_env
    monkeypatch.setattr(paths, "config_dirs",
                        lambda: [tmp / "cfg" / "dosync", tmp / "etc" / "dosync"])
    assert paths.resolve_config("policies.json", "DOSYNC_POLICIES") is None


# ── Found by installing from PyPI on a clean machine (2026-08-08) ───────────

def test_the_hub_says_when_it_is_only_reachable_locally():
    """The commonest deployment is a headless Raspberry Pi whose operator is on
    SSH and wants the dashboard from their laptop. Binding to loopback is the
    right default — a hub reachable from the whole network because nobody chose
    that is worse than one needing a flag — but `Uvicorn running on
    http://127.0.0.1` does not tell them why their browser cannot connect.

    The default does not change. The silence does.
    """
    import inspect

    from dosync import cli

    src = inspect.getsource(cli)
    assert "loopback only" in src
    assert "--host 0.0.0.0" in src, "and must say exactly how to change it"
    assert 'default=os.environ.get("DOSYNC_HOST", "127.0.0.1")' in src, \
        "the safe default must stay"


def test_the_published_version_and_the_source_version_cannot_silently_diverge():
    """`paths.py` was added under 0.4.2 while 0.4.2 was already on PyPI — two
    different artefacts with one number. Anyone reporting a bug "in 0.4.2" would
    leave us unable to tell which one they have.

    This pins the specific mistake: the version must move when behaviour does.
    """
    import re

    import dosync

    changelog = (REPO / "CHANGELOG.md").read_text()
    versions = re.findall(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", changelog, re.M)

    assert dosync.__version__ in versions or dosync.__version__ > max(versions), (
        f"version {dosync.__version__} is neither released nor ahead of the "
        f"latest changelog entry {max(versions)} — a behaviour change under an "
        f"already-published number produces two artefacts with one name")
