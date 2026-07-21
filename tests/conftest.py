"""Pytest configuration — runs before ANY test module is imported.

Why this file exists (2026-07-14): server.py builds its hub at MODULE level:

    hub = DoSyncHub(db_path=":memory:" if _certify_mode
                    else os.environ.get("DOSYNC_DB", "dosync.db"))

Six test files import `server`, and Python caches modules — whichever imports
first decides the DB for the whole run, using whatever env was set at that
instant. In practice that meant the suite built its hub on the real ./dosync.db,
fired intents at it, and wrote to its audit log: running the tests grew a
developer's database to 150KB and left `audit_integrity=False` behind, from
concurrent writers on a shared file. A test that setenv'd inside itself was
already too late to prevent this.

pytest imports conftest.py before collecting anything, which is the one place
where setting the env is guaranteed to happen first. setdefault (not assignment)
so an explicit DOSYNC_DB from the caller still wins.

The project rule this enforces: tests never touch a real database.
"""

import os

os.environ.setdefault("DOSYNC_DB", ":memory:")


# ── Automatic test taxonomy (parada técnica 2026-07-21, Morales) ─────────────
# Rather than tag 54 test files by hand (and let the tags rot), classify each
# test by what its SOURCE uses: a test that constructs a TestClient exercises
# the HTTP layer end to end (e2e); everything else is unit. This keeps the
# taxonomy honest — it is derived from the code, not asserted alongside it.
import inspect


def pytest_collection_modifyitems(config, items):
    import pytest
    for item in items:
        try:
            src = inspect.getsource(item.function)
        except (OSError, TypeError):
            src = ""
        if "TestClient" in src or "TestClient" in (item.module.__dict__.get("__doc__") or ""):
            item.add_marker(pytest.mark.e2e)
        else:
            item.add_marker(pytest.mark.unit)
