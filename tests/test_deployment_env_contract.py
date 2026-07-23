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
