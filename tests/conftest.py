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
