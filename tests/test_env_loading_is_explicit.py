"""Importing a module must not mutate the process environment.

`dosync/adapters/notifications.py` read a .env at import time and applied it
with `os.environ.setdefault`, inside a bare `except Exception: pass`. Two tests
failed on the reference deployment and passed everywhere else: they deleted
DOSYNC_POLICIES with monkeypatch, an import then ran, and setdefault put the
variable straight back. A test cannot isolate an environment that an import
un-isolates — and the machine where it broke is the one whose behaviour the
suite exists to assert.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_importing_the_adapter_does_not_touch_the_environment(tmp_path):
    """The property, checked in a subprocess with a real .env present.

    In-process the module is already imported, so its import side effect
    cannot be observed — asserting here would assert nothing.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("DOSYNC_CANARY_XYZ=set-by-dotenv\n")
    script = (
        "import os, sys;"
        "sys.path.insert(0, %r);"
        "import dosync.adapters.notifications as n;"
        "print(os.environ.get('DOSYNC_CANARY_XYZ', 'UNSET'))" % str(REPO)
    )
    out = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                         capture_output=True, text=True, timeout=90)
    assert out.stdout.strip().endswith("UNSET"), (
        "importing the notifications adapter set an environment variable from "
        f"a .env file: {out.stdout!r}")


def test_load_env_file_is_explicit_and_reports_what_it_did(tmp_path, monkeypatch):
    from dosync.adapters.notifications import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nDOSYNC_CANARY_A=1\n\nDOSYNC_CANARY_B = 2 \n")
    monkeypatch.delenv("DOSYNC_CANARY_A", raising=False)
    monkeypatch.delenv("DOSYNC_CANARY_B", raising=False)

    assert load_env_file(env_file) == 2
    assert os.environ["DOSYNC_CANARY_A"] == "1"
    assert os.environ["DOSYNC_CANARY_B"] == "2"
    monkeypatch.delenv("DOSYNC_CANARY_A", raising=False)
    monkeypatch.delenv("DOSYNC_CANARY_B", raising=False)


def test_an_explicit_environment_variable_still_wins(tmp_path, monkeypatch):
    """The .env fills gaps; it never overrides an operator who said so."""
    from dosync.adapters.notifications import load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("DOSYNC_CANARY_C=from-file\n")
    monkeypatch.setenv("DOSYNC_CANARY_C", "from-operator")
    assert load_env_file(env_file) == 0
    assert os.environ["DOSYNC_CANARY_C"] == "from-operator"


def test_a_missing_env_file_is_a_normal_state(tmp_path):
    from dosync.adapters.notifications import load_env_file
    assert load_env_file(tmp_path / "nope.env") == 0
