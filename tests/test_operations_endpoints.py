"""
DoSync — Operations query endpoints (HTTP layer).

Read-only query surface for long-running operations: GET /v1/operations (active
list) and GET /v1/operations/{id} (one by id). Tested at the HTTP layer with the
in-memory TestClient — fast, no Pi, no hardware.

Why not cert: the cert suite has no long-running devices registered on the Pi, so
it never exercises these endpoints with real operations. These tests seed an
operation directly into the hub DB and verify the HTTP contract: the list returns
active operations, get-by-id returns the full record, and an unknown id is a clean
404 (not a 500).

Run: DOSYNC_AUTH=false python3 tests/test_operations_endpoints.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DOSYNC_AUTH", "false")

from fastapi.testclient import TestClient
import server
from dosync.operations import Operation, OperationState

client = TestClient(server.app)


def _seed_operation(state=OperationState.IN_PROGRESS, telemetry=True):
    """Insert an operation directly into the hub DB (as execute_intent would)."""
    server.hub.db.init_operations_table()
    op = Operation(device_id="mover-01", action="go_to", telemetry_capable=telemetry)
    if state != OperationState.PENDING:
        op.transition_to(state, reason="seed")
    server.hub.db.save_operation(op.to_dict(), terminal=op.is_terminal)
    return op


def test_list_operations_contract():
    """GET /v1/operations returns count + operations list."""
    r = client.get("/v1/operations")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body
    assert "operations" in body
    assert isinstance(body["operations"], list)


def test_list_operations_includes_active():
    """A seeded active operation appears in the list."""
    op = _seed_operation(OperationState.IN_PROGRESS)
    r = client.get("/v1/operations")
    assert r.status_code == 200
    ids = {o["operation_id"] for o in r.json()["operations"]}
    assert op.operation_id in ids


def test_list_operations_excludes_terminal():
    """A terminal (completed) operation is not in the active list."""
    op = _seed_operation(OperationState.IN_PROGRESS)
    # complete it
    live = Operation(device_id="mover-01", action="go_to", telemetry_capable=True,
                     operation_id=op.operation_id, state=OperationState.IN_PROGRESS)
    live.transition_to(OperationState.COMPLETED, reason="done")
    server.hub.db.save_operation(live.to_dict(), terminal=True)

    r = client.get("/v1/operations")
    ids = {o["operation_id"] for o in r.json()["operations"]}
    assert op.operation_id not in ids


def test_get_operation_by_id():
    """GET /v1/operations/{id} returns the full record including history."""
    op = _seed_operation(OperationState.IN_PROGRESS)
    r = client.get(f"/v1/operations/{op.operation_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["operation_id"] == op.operation_id
    assert body["device_id"] == "mover-01"
    assert body["state"] == "in_progress"
    assert "history" in body
    assert isinstance(body["history"], list)


def test_get_unknown_operation_is_404():
    """An unknown operation id is a clean 404, not a 500."""
    r = client.get("/v1/operations/op_does_not_exist")
    assert r.status_code == 404


def test_endpoints_are_read_only():
    """The query endpoints never mutate state: listing/getting an operation twice
    returns the same state, and there is no POST/DELETE that changes it here."""
    op = _seed_operation(OperationState.IN_PROGRESS)
    r1 = client.get(f"/v1/operations/{op.operation_id}").json()
    r2 = client.get(f"/v1/operations/{op.operation_id}").json()
    assert r1["state"] == r2["state"] == "in_progress"
    # POST to the collection is not allowed (no such route) → 405/404, never mutate
    r3 = client.post("/v1/operations", json={})
    assert r3.status_code in (404, 405)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} operations endpoint tests passed.")
    sys.exit(1 if failed else 0)
