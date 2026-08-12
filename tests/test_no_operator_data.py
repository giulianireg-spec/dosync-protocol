"""Nothing in this repository may depend on the machine it was written on.

DoSync is domain-agnostic infrastructure. An artefact that only runs, only makes
sense, or is only verifiable on one operator's hardware narrows the protocol to
that installation — which contradicts the claim the project makes everywhere
else.

This is not hypothetical. On 2026-08-12 an audit found the reference
deployment's data across fourteen files: an evaluation fixture carrying the
author's room names in Spanish (including a child's bedroom) and his television's
brand and model, a sensitivity tool with `/home/<user>/...` hard-coded that could
not run anywhere else, the deployment's LAN address in the certification CLI's
own `--help`, and the deployment's device inventory inside the normative tag
vocabulary. Some of it had been published the day before, against a standard the
project had already decided in a design panel.

The rule is written in CONTRIBUTING.md. This file is what makes it hold: a rule
without a test is an intention, and this project has watched that happen enough
times to stop relying on remembering.

Exceptions belong in ALLOWED below, each with a reason. The point is not that
there are no exceptions — it is that they are visible and argued.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Extensions deliberately wide. The first version scanned seven and missed four
# real hits: systemd units carrying /home/<user> (.service), a SQL export with
# room names (.sql), and config files. An audit that stops at the extensions you
# happened to think of reports clean and is not.
SCAN_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".sh", ".html", ".txt",
                 ".service", ".sql", ".conf", ".cfg", ".ini", ".toml", ".env",
                 ".service.d", ".timer", ".css", ".js"}
SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache",
             "certs", "checkpoints", "audit-segments", "site-packages", ".mypy_cache"}

# Documentation/example ranges are fine — they are reserved for exactly this.
# RFC 5737 (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) plus the generic
# examples a reader recognises as placeholders.
ALLOWED_ADDRESSES = {
    "192.0.2.", "198.51.100.", "203.0.113.",       # RFC 5737 documentation
    "127.0.0.1", "0.0.0.0", "localhost", "::1",
    "192.168.1.1", "192.168.1.10", "192.168.1.40", "192.168.1.100",
    "192.168.100.1", "192.168.100.X", "192.168.100.12",
    "192.168.0.1", "10.0.0.1", "8.8.8.8",
}

# The reference deployment's own addresses. These are the ones that must never
# reappear — they name real machines on someone's LAN.
FORBIDDEN_ADDRESSES = ["192.168.100.109", "192.168.100.108"]

# Words that betray one deployment: its language and its rooms. English location
# tags from the vocabulary are NOT here — `bedroom` is a defined tag; `cocina` is
# one person's kitchen.
FORBIDDEN_WORDS = [
    "cocina", "comedor", "habitacion", "habitación", "niños", "ninos",
    "dormitorio", "abuela", "entrada", "oficina",
]

ALLOWED: dict[str, str] = {
    # path fragment -> why it may contain what would otherwise fail
    "tests/test_no_operator_data.py":
        "this file names the forbidden patterns in order to forbid them",
    "CONTRIBUTING.md":
        "states the rule, and must show what it forbids to be usable",
    "CHANGELOG.md":
        "history is not rewritten; entries describe defects using their own terms",
    "docs/TECH-DEBT-BACKLOG.md":
        "a dated record of findings; rewriting what an entry observed would falsify it",
}


def _files():
    """Only files git tracks — the repository, not the working directory.

    The first version walked the filesystem, and failed on the reference
    deployment over nine signed certification reports sitting in the working
    directory. Those are gitignored operator artefacts: they were never going to
    be published, and flagging them made the suite red on the one machine that
    actually runs the protocol — the inverted form of the very defect this file
    exists to prevent, and it would have blocked the pre-push hook over nothing.

    What belongs in the repository is what git tracks. Local artefacts are the
    operator's business; .gitignore is what keeps them out.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                             capture_output=True, text=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is unavailable; cannot determine what the repository tracks")
    for name in out.stdout.split("\0"):
        if not name:
            continue
        path = REPO / name
        if path.suffix.lower() not in SCAN_SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _allowed(path: Path) -> bool:
    rel = str(path.relative_to(REPO))
    return any(frag in rel for frag in ALLOWED)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def test_no_real_machine_addresses():
    """The reference deployment's LAN addresses name real machines."""
    hits = []
    for path in _files():
        if _allowed(path):
            continue
        text = _read(path)
        for addr in FORBIDDEN_ADDRESSES:
            if addr in text:
                line = next((i for i, l in enumerate(text.splitlines(), 1) if addr in l), 0)
                hits.append(f"{path.relative_to(REPO)}:{line} → {addr}")
    assert not hits, (
        "the reference deployment's address is in the repository; use "
        "<hub-address> or a documentation range:\n  " + "\n  ".join(hits))


def test_no_absolute_user_paths():
    """A hard-coded home directory makes an artefact unrunnable for everyone else."""
    pattern = re.compile(r"(/home/[a-z][a-z0-9_-]{2,}|/Users/[a-z][a-z0-9_-]{2,})", re.I)
    generic = {"/home/user", "/home/pi", "/home/runner", "/Users/user",
               "/home/dosync", "/home/claude"}
    hits = []
    for path in _files():
        if _allowed(path):
            continue
        for i, line in enumerate(_read(path).splitlines(), 1):
            for m in pattern.findall(line):
                if m.lower() in generic or "<" in line:
                    continue
                hits.append(f"{path.relative_to(REPO)}:{i} → {m}")
    assert not hits, (
        "an absolute user path is in the repository; take it as an argument or "
        "an environment variable:\n  " + "\n  ".join(hits[:20]))


def test_no_operator_language_or_room_names():
    """One deployment's language and rooms are not the protocol's vocabulary."""
    hits = []
    for path in _files():
        if _allowed(path):
            continue
        text = _read(path).lower()
        for word in FORBIDDEN_WORDS:
            if re.search(rf"\b{re.escape(word)}\b", text):
                line = next((i for i, l in enumerate(text.splitlines(), 1)
                             if re.search(rf"\b{re.escape(word)}\b", l)), 0)
                hits.append(f"{path.relative_to(REPO)}:{line} → '{word}'")
    assert not hits, (
        "an operator's own room names or language are in the repository:\n  "
        + "\n  ".join(hits[:20]))


# Device identifiers from the reference deployment. Renamed to role-based ones
# (light-zone1-01 …) so that fixtures, docs and the paper describe a topology
# rather than one household. `wiz-a4c138`-style names stay legal: they are the
# vendor's own factory names, used in docs to make a point about naming.
DEPLOYMENT_DEVICE_IDS = [
    "wiz-cocina", "wiz-comedor", "wiz-habitacion", "wiz-living1-", "wiz-living2-",
    "wiz-ninos", "rpi-pir-01", "rpi-dht22-01", "notifier-sms-01", "alarm-test-01",
    "tv_philips", "qn75q7faagcfv",
]


def test_no_device_identifiers_from_the_reference_deployment():
    """The published tables must resolve against the published fixtures.

    Renaming the fixtures and leaving the paper, the articles or the site
    pointing at the old identifiers is worse than not renaming at all: the
    evidence stops matching the data, and reproducibility was the point.
    """
    hits = []
    for path in _files():
        if _allowed(path):
            continue
        text = _read(path)
        for dev in DEPLOYMENT_DEVICE_IDS:
            if dev in text:
                line = next((i for i, l in enumerate(text.splitlines(), 1) if dev in l), 0)
                hits.append(f"{path.relative_to(REPO)}:{line} → {dev}")
    assert not hits, (
        "an identifier from the reference deployment is in the repository; use "
        "role-based ones (light-zone1-01):\n  " + "\n  ".join(hits[:20]))


def test_published_fixtures_carry_no_vendor_hardware():
    """A fixture describes a topology, not the brands someone happens to own."""
    brands = ["philips", "qled", "samsung", "signify", "sonoff", "shelly"]
    hits = []
    # Every tracked benchmark file, not only .json — the brands were in a .py.
    for path in (p for p in _files() if "benchmarks" in p.parts):
        text = _read(path).lower()
        for brand in brands:
            # `"adapter": "wiz"` names a DoSync adapter, not the operator's brand.
            for i, line in enumerate(text.splitlines(), 1):
                if brand in line and '"adapter"' not in line:
                    hits.append(f"{path.relative_to(REPO)}:{i} → '{brand}'")
    assert not hits, (
        "a published fixture names the operator's hardware:\n  " + "\n  ".join(hits[:20]))


def test_the_rule_is_written_down():
    """A test nobody can find the rule for is a trap, not a guardrail."""
    text = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "depend on the machine it was written on" in text, \
        "CONTRIBUTING.md no longer states the rule this file enforces"
