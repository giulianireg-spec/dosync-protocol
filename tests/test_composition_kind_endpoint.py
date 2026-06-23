"""
DoSync — /v1/intent-classes composition_kind validation (HTTP layer).

Covers the FastAPI endpoint's handling of the composition_kind field with the
in-memory TestClient: it is accepted for a known kind, rejected (422) for an unknown
kind, defaults to None for a flat intent, and is echoed back / surfaced in the list.
No network, no Pi, no hardware.

Why not cert: the cert exercises the happy path of intent-class registration against
the Pi, but does not systematically check the composition_kind validation path
(unknown-kind rejection, None default) — that error-path coverage is what this adds,
fast, in CI.

Run: DOSYNC_AUTH=false python3 tests/test_composition_kind_endpoint.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DOSYNC_AUTH", "false")

from fastapi.testclient import TestClient
import server

client = TestClient(server.app)

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  \u2713  {name}")
    else:
        _FAIL += 1
        print(f"  \u2717  {name}")


def _cleanup(name):
    try:
        client.delete(f"/v1/intent-classes/{name}")
    except Exception:
        pass


# ── Known kind is accepted ────────────────────────────────────────────────────

def test_known_composition_kind_accepted():
    _cleanup("inspect_area_test")
    r = client.post("/v1/intent-classes", json={
        "name": "inspect_area_test",
        "urgency": "info",
        "resolution_tags": ["aerial"],
        "resolution_actuators": ["take_off", "go_to"],
        "description": "Inspect a perimeter",
        "domain": "robotics",
        "composition_kind": "perimeter",
    })
    check("known composition_kind accepted (200)", r.status_code == 200)
    if r.status_code == 200:
        check("response echoes composition_kind",
              r.json().get("composition_kind") == "perimeter")
    _cleanup("inspect_area_test")


def test_composition_kind_persisted_and_listed():
    _cleanup("inspect_area_test")
    client.post("/v1/intent-classes", json={
        "name": "inspect_area_test", "urgency": "info",
        "resolution_tags": ["aerial"], "resolution_actuators": ["take_off"],
        "description": "d", "domain": "robotics", "composition_kind": "perimeter",
    })
    r = client.get("/v1/intent-classes")
    classes = {c["name"]: c.get("composition_kind") for c in r.json()["intent_classes"]}
    check("composition_kind surfaced in list",
          classes.get("inspect_area_test") == "perimeter")
    _cleanup("inspect_area_test")


# ── Unknown kind is rejected ──────────────────────────────────────────────────

def test_unknown_composition_kind_rejected():
    _cleanup("survey_grid_test")
    r = client.post("/v1/intent-classes", json={
        "name": "survey_grid_test", "urgency": "info",
        "resolution_tags": ["aerial"], "resolution_actuators": ["take_off"],
        "description": "d", "domain": "robotics", "composition_kind": "grid",
    })
    check("unknown composition_kind rejected (422)", r.status_code == 422)
    _cleanup("survey_grid_test")


# ── Flat intent defaults to None ──────────────────────────────────────────────

def test_flat_intent_has_null_kind():
    _cleanup("water_plants_test")
    r = client.post("/v1/intent-classes", json={
        "name": "water_plants_test", "urgency": "info",
        "resolution_tags": ["irrigation"], "resolution_actuators": ["water"],
        "description": "d", "domain": "garden",
    })
    check("flat intent accepted (200)", r.status_code == 200)
    if r.status_code == 200:
        check("flat intent composition_kind is null",
              r.json().get("composition_kind") is None)
    _cleanup("water_plants_test")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} composition_kind endpoint tests passed.")
    if _FAIL:
        raise SystemExit(1)
