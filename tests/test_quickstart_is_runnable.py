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
