"""The commands in the README must work as written.

A clean-room install on 2026-08-15 followed the Quick Start literally: `pipx
install dosync`, `dosync-hub`, then the four documented curl commands. The
install worked, the hub started, the protocol did everything it claims — and all
four commands returned `401 Missing Authorization header`, because none of them
carried the token the hub had just printed.

That is the worst shape a defect can take. Nothing was broken; the first minute
of a stranger's experience just looked like it was, immediately after the page
said "that is a working hub".

The check is deliberately narrow: it does not run the commands (that needs a
live hub), it asserts that every documented request to an authenticated endpoint
carries an Authorization header. A reader copying a line must not be the one who
discovers it is incomplete.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Endpoints reachable without a token, by design: a liveness probe a load
#: balancer can call, and the OpenAPI document that describes the surface.
#: Matched EXACTLY. An earlier version listed "/" here and used startswith,
#: which made every path public and the whole check vacuous — it passed while
#: the header was missing, which is the failure mode this project has hit
#: before (a grep for "FOUND" matching "NOT FOUND").
PUBLIC_PATHS = frozenset({"/v1/health", "/openapi.json", "/docs", "/redoc", "/"})


def _curl_invocations(text: str):
    """Every `curl` command in the document, joined across line continuations."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    for line in joined.splitlines():
        line = line.strip()
        if line.startswith("curl ") and "47200" in line:
            yield line


def _hits_authenticated_endpoint(command: str) -> bool:
    url = re.search(r"https?://[^\s'\"]+", command)
    if not url:
        return False
    path = re.sub(r"https?://[^/]+", "", url.group(0)).split("?")[0]
    return path not in PUBLIC_PATHS


def test_documented_curl_commands_carry_a_token():
    failures = []
    for doc in ("README.md", "docs/QUICKSTART.md", "CONTRIBUTING.md"):
        path = REPO / doc
        if not path.exists():
            continue
        for command in _curl_invocations(path.read_text(encoding="utf-8")):
            if not _hits_authenticated_endpoint(command):
                continue
            if "Authorization" not in command:
                failures.append(f"{doc}: {command[:96]}")
    assert not failures, (
        "documented commands hit authenticated endpoints without a token — a "
        "reader copying them gets 401 on their first try:\n  "
        + "\n  ".join(failures))


def test_the_quick_start_tells_the_reader_where_the_token_comes_from():
    """A header referencing a variable nobody was told to set is the same defect."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Quick start")
    section = readme[start:start + 6000]
    assert "DOSYNC_TOKEN" in section, "the quick start uses no token"
    assert re.search(r"export DOSYNC_TOKEN|printed on first start|shown only once",
                     section), \
        "the quick start uses a token without saying where the reader gets it"


def test_the_quick_start_leads_with_the_dashboard():
    """Adopting a device needs no terminal, and the page used to imply otherwise.

    The Quick Start opened with four `curl` calls and mentioned the dashboard
    much further down. Someone who does not write code read that and concluded
    the project was not for them — while a button doing the same job, better,
    sat one section lower. A 3D printer, a television and a Bluetooth sensor
    were adopted through it on the reference deployment without a line typed.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    start = readme.index("## Quick start")
    dashboard = readme.index("localhost:47200", start)
    first_curl = readme.index("curl -X POST", start)
    assert dashboard < first_curl, \
        "the Quick Start reaches an API call before it mentions the dashboard"


def test_windows_is_documented():
    """Six things a clean Windows machine needed that this page did not say.

    pipx is not installed with Python on Windows, so the very first command in
    the Quick Start failed before the reader saw anything of the project;
    `ensurepath` requires reopening the terminal; `export` is not a PowerShell
    command; `curl` is an alias for a different program; `setup_pki.sh` is a
    shell script; and escaping JSON for `curl.exe` produces a JSON decode error.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for needed in ("python -m pip install --user pipx", "$env:DOSYNC_TOKEN",
                   "Invoke-RestMethod", "curl.exe"):
        assert needed in readme, f"Windows readers are still missing: {needed}"


def test_powershell_examples_do_not_use_the_curl_alias():
    """`curl` in PowerShell is Invoke-WebRequest, which fails confusingly."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    block = readme[readme.index("The same calls in PowerShell"):]
    block = block[:block.index("</details>")]
    for line in block.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("curl "), \
            f"a PowerShell example uses the curl alias: {stripped[:60]}"
