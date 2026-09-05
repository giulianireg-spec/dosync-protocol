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


def test_adapter_descriptions_reaching_an_agent_are_in_english():
    """The device descriptions an adapter publishes are read by a model.

    These are short strings — "Estado", "Activa", "Brillo 0-100%" — with no
    accents and no function words, which is the documented blind spot of the
    general detector and the reason fourteen of them survived five rewrites of
    it. They are not internal comments: they are what an agent receives when it
    asks what a device does, and a model reasoning in English got
    "Bloqueado/desbloqueado" as the meaning of a lock's state.

    A general language classifier is not needed here. The strings live in
    bounded mapping tables, so a word list specific to this narrow context is
    both sufficient and honest about its scope.
    """
    import re
    from pathlib import Path

    # `sensor`, `temperatura`, `color` and `valor` are deliberately absent:
    # the first is spelled identically in both languages, the rest are close
    # enough that they fired on correct English ("Sensor reading", "Color RGB").
    # A guard that flags correct text gets switched off, and these carry no
    # signal the remaining words do not.
    spanish = re.compile(
        r"\b(?:estado|activa|activar|brillo|bloqueado|desbloqueado|encendida|"
        r"apagada|alarma|puerta|luces|posicion|abierto|cerrado|nivel|"
        r"objetivo|apagar|encender|actual)\b", re.I)

    root = Path(__file__).resolve().parent.parent / "dosync" / "adapters"
    hits = []
    for path in sorted(root.glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if "Spec(" not in line:
                continue
            for quoted in re.findall(r'"([^"]{2,})"', line):
                # Identifiers are the protocol's own vocabulary, not prose.
                if quoted in ("set_brightness", "set_color_temp", "set_temperature",
                              "sensor", "alarm", "temperature", "state", "value"):
                    continue
                if spanish.search(quoted):
                    hits.append(f"{path.name}:{i} → {quoted}")

    assert not hits, (
        "adapter descriptions an agent will read are in Spanish:\n  "
        + "\n  ".join(hits))


def test_the_core_and_the_spec_are_in_english():
    """The protocol, its adapters and its specification are written in English.

    Not style: reach. An open protocol whose core carries one contributor's
    language cannot be read, audited or implemented by most of the people it
    asks to adopt it — and the project requires English in code, comments and
    docs for exactly that reason.

    Measured on 2026-08-12: 174 lines of Spanish across 29 files, including
    module headers, the MCP tool descriptions an LLM reads as a contract, and
    user-facing SMS text.

    Scope: dosync/, tools/ and spec/ — the protocol and what implements it.
    examples/ is deliberately excluded: the demos narrate one deployment in its
    operator's own language, which is legitimate for a demo and not for a
    protocol.
    """
    # An accented character is enough. The first version also required three
    # Spanish stopwords on the same line, and let 81 lines through — "Instalación:",
    # "Características:", "Posición 0-100%" are each one word. A detector tuned
    # to catch prose misses exactly the labels a reader sees first.
    accented = re.compile(r"[áéíóúñÁÉÍÓÚÑ¡¿]")
    # Accents alone are not enough, and a user-facing warning proved it twice.
    # `Usar: DOSYNC_AUTH=false para deshabilitar, o DOSYNC_TOKEN=<token> para
    # autenticar.` is unambiguously Spanish, carries no accent, and reached an
    # operator on a clean install. A Spanish docstring in the same file had
    # slipped through earlier for the same reason and was fixed by hand without
    # strengthening this check — which guaranteed the second time.
    #
    # Whole words only: `para` is Spanish, `parameters` is not, and a single
    # match is not enough, since `este` and `cada` turn up inside URLs.
    # Third iteration of this check, and the previous two failed the same way:
    # each fix added items to a list, and a list always has holes. Accents
    # missed `Usar: DOSYNC_AUTH=false para deshabilitar`. A content-word list
    # then missed `Conecta DoSync con Home Assistant via su API REST local`,
    # which is unmistakably Spanish and contains no content word from it.
    #
    # Function words are the fix: `con`, `su`, `el`, `del` are the parts of a
    # language that cannot be avoided, unlike vocabulary. Two on one line, whole
    # words only.
    # Fifth iteration. Each of the previous four added entries to a list, and
    # each was defeated by a line whose Spanish used words the list happened not
    # to contain — most recently `Ejecuta un PhasedActionPlan: cada fase en
    # paralelo`, where the only listed word was `un`.
    #
    # The list is now the closed class itself: articles, prepositions,
    # conjunctions, demonstratives, quantifiers and the copula. These are the
    # words a language cannot do without, unlike vocabulary — which is why a
    # vocabulary list keeps losing and this should not.
    spanish_function_words = re.compile(
        r"\b(?:el|la|los|las|lo|un|una|unos|unas|del|al|con|sin|por|para|"
        r"desde|hasta|entre|sobre|tras|durante|segun|mediante|"
        r"su|sus|mi|mis|tu|tus|nuestro|nuestra|"
        r"que|como|cuando|donde|porque|aunque|pero|sino|mientras|"
        r"este|esta|estos|estas|ese|esa|esos|esas|aquel|aquella|"
        r"cada|todo|toda|todos|todas|otro|otra|otros|otras|"
        r"es|son|era|eran|ser|esta|estan|hay|"
        r"mas|muy|ya|si|no|se|le|nos|te|ni)\b")
    # `y` and `o` count, but never on their own. In Python they are axes,
    # coordinates and one-letter loop variables — `x, y = point` and
    # `set the y and o axis` would both read as Spanish. Requiring a second,
    # longer function word alongside them keeps `Bridge entre DoSync y Home
    # Assistant` (which has `entre`) while dropping the false positives.
    #
    # Measured: removing them outright lost six of the fifteen lines this
    # version found, so they carry real signal — just not alone.
    spanish_short_conjunctions = re.compile(r"\b(?:y|o)\b")

    # Single words that cannot occur in an English docstring at all. These are
    # section headers, which is exactly where a translated file leaves traces.
    spanish_headers = re.compile(
        r"^\s*(?:Uso|Requiere|Ejemplo|Ejemplos|Nota|Notas|Instalación|"
        r"Configuración|Advertencia|Resumen|Parámetros|Devuelve|Retorna)\s*:",
        re.MULTILINE)
    # Known limits, stated rather than discovered later:
    #   `la = latitude, lo = longitude` reads as Spanish. Two-letter names for
    #   coordinates would trip this; the pattern does not occur here today.
    #   Short Spanish with no accents, no function words and no section header
    #   still passes — `Requiere autenticacion` has none of the four signals.
    #   Test files are not scanned. They are written for the author; the core
    #   and the specification are what a third party reads. A decision, not an
    #   oversight.

    spanish_words = re.compile(
        r"\b(?:para|desde|hasta|entre|sobre|cuando|donde|porque|aunque|"
        r"usar|debe|puede|tiene|hacer|dispositivo|dispositivos|"
        r"deshabilitar|autenticar|habilitar|ejecutar|configurar|"
        r"archivo|archivos|todos|todas|esto|esta|este|estos|estas|"
        r"mismo|misma|cada|otro|otra|carga|nuevo|nueva)\b",
        re.IGNORECASE)
    # Surnames from design panels, cited in code comments that record why a
    # decision was made. Attribution is not prose, and rewriting a person's name
    # to satisfy a language rule would be worse than the rule.
    allowed_names = re.compile(r"Benítez|Ordóñez|Aguirre|Nakamura|Ferreyra|Paredes|"
                               r"Llamar al 107")
    hits = []
    for path in _files():
        rel = str(path.relative_to(REPO))
        if not rel.startswith(("dosync/", "tools/", "spec/")):
            continue
        if _allowed(path):
            continue
        for i, line in enumerate(_read(path).splitlines(), 1):
            if allowed_names.search(line):
                continue
            if (accented.search(line)
                    or spanish_headers.search(line)
                    or len(set(w.lower()
                               for w in spanish_words.findall(line))) >= 2
                    or len(set(w.lower()
                               for w in spanish_function_words.findall(line))) >= 2
                    or (spanish_short_conjunctions.search(line)
                        and spanish_function_words.search(line))):
                hits.append(f"{rel}:{i} → {line.strip()[:70]}")
    assert not hits, (
        "the protocol core or its specification contains Spanish prose:\n  "
        + "\n  ".join(hits[:20]))


# ── Credentials, not just names and paths (2026-08-26) ─────────────────────

#: Placeholders are the point: a file that says REPLACE_WITH_YOUR_API_KEY is
#: doing the right thing and must not trip this.
PLACEHOLDER_MARKERS = (
    "replace_with", "your_", "<your", "example", "changeme", "placeholder",
    "xxxx", "...", "redacted", "dummy", "sample",
)

#: Shapes that are credentials whatever they are called. A JWT is three
#: base64 segments; the rest are the token formats a deployment realistically
#: carries. Deliberately narrow: a false positive here costs a moment, and a
#: miss costs what this test was written for.
CREDENTIAL_PATTERNS = (
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def test_no_credentials_anywhere_in_the_repository():
    """A live Home Assistant token shipped in `dosync.service` for three months.

    Issued in May 2026 and valid until 2036, in a unit file at the root of a
    public repository, and found only because a Windows persistence test
    happened to open it. Anyone who cloned the project got a working credential
    for one person's Home Assistant.

    `.service` files were already scanned — that extension was added
    deliberately after units carrying `/home/<user>` slipped through. But the
    checks looked for addresses, paths, room names and device identifiers.
    Nobody had asked whether a file contained a *secret*, so the guard written
    to catch exactly this class of leak looked straight past it.

    Placeholders are exempt on purpose: a unit that says
    `HA_TOKEN=REPLACE_WITH_YOUR_TOKEN` is doing the right thing.
    """
    hits = []
    for path in _files():
        text = _read(path)
        for label, pattern in CREDENTIAL_PATTERNS:
            for match in pattern.finditer(text):
                found = match.group(0)
                if any(m in found.lower() for m in PLACEHOLDER_MARKERS):
                    continue
                line = next((i for i, l in enumerate(text.splitlines(), 1)
                             if found[:24] in l), 0)
                hits.append(f"{path.relative_to(REPO)}:{line} → {label}: "
                            f"{found[:20]}…")
    assert not hits, (
        "a credential is committed to this repository. Revoke it first — it is "
        "public and cloned copies keep it — then remove it and keep secrets in "
        "an untracked drop-in:\n  " + "\n  ".join(hits))


def test_the_shipped_unit_carries_no_environment_secrets():
    """The unit is a template others copy, so what it models matters.

    A unit that hard-codes a token teaches every reader to hard-code a token,
    and the file is the first thing an operator opens when setting up a service.
    """
    unit = REPO / "dosync.service"
    if not unit.exists():
        return
    text = unit.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "Environment=" not in stripped:
            continue
        for secret_ish in ("TOKEN", "SECRET", "PASSWORD", "KEY", "CREDENTIAL"):
            if secret_ish in stripped.upper():
                assert any(m in stripped.lower() for m in PLACEHOLDER_MARKERS), (
                    f"the shipped unit sets {secret_ish} to something that is not "
                    f"a placeholder:\n    {stripped[:100]}\n"
                    "Secrets belong in an untracked drop-in, not in the template "
                    "everyone copies.")
