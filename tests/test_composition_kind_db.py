"""
Tests for the composition_kind intent-class column and its migration
(dosync/db.py: schema, save/get/list, idempotent additive migration).

Pure logic, fully offline: in-memory and temp-file SQLite.
"""

import os
import sqlite3
import tempfile

from dosync.db import DoSyncDB

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


def _fresh_db():
    d = DoSyncDB(":memory:")
    d.init()
    return d


# ── New databases ─────────────────────────────────────────────────────────────

def test_new_db_has_column():
    d = _fresh_db()
    d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off", "go_to"],
                        "Inspect an area's perimeter", "robotics",
                        composition_kind="perimeter")
    check("composition_kind persisted on a new DB",
          d.get_intent_class("inspect_area")["composition_kind"] == "perimeter")


def test_normal_intent_has_null_kind():
    d = _fresh_db()
    d.save_intent_class("water_plants", "info", ["irrigation"], ["water"],
                        "Water the garden", "garden")
    check("a normal intent has composition_kind None",
          d.get_intent_class("water_plants")["composition_kind"] is None)


def test_universals_have_null_kind():
    d = _fresh_db()
    check("seeded universal has composition_kind None",
          d.get_intent_class("ensure_safety")["composition_kind"] is None)


# ── Preservation across upserts ───────────────────────────────────────────────

def test_resave_without_kind_preserves_it():
    d = _fresh_db()
    d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                        "v1", "robotics", composition_kind="perimeter")
    # Re-save WITHOUT passing composition_kind (e.g. a description update).
    d.save_intent_class("inspect_area", "info", ["aerial", "drone"],
                        ["take_off", "go_to", "land"], "v2 updated", "robotics")
    check("re-saving without kind preserves the existing kind",
          d.get_intent_class("inspect_area")["composition_kind"] == "perimeter")


def test_resave_can_change_kind():
    d = _fresh_db()
    d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                        "v1", "robotics", composition_kind="perimeter")
    d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                        "v2", "robotics", composition_kind="grid")
    check("an explicit new kind replaces the old one",
          d.get_intent_class("inspect_area")["composition_kind"] == "grid")


# ── list_intent_classes ───────────────────────────────────────────────────────

def test_list_includes_kind():
    d = _fresh_db()
    d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                        "desc", "robotics", composition_kind="perimeter")
    by_name = {c["name"]: c.get("composition_kind") for c in d.list_intent_classes()}
    check("list surfaces composition_kind for a composition intent",
          by_name.get("inspect_area") == "perimeter")
    check("list surfaces None for a universal",
          by_name.get("ensure_safety") is None)


# ── Migration of an existing (legacy) database ────────────────────────────────

def test_migration_adds_column_without_data_loss():
    path = tempfile.mktemp(suffix=".db")
    try:
        # Build a legacy intent_classes table WITHOUT composition_kind, with a row.
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE intent_classes (
            name TEXT PRIMARY KEY, urgency TEXT, resolution_tags TEXT,
            resolution_actuators TEXT, description TEXT, domain TEXT,
            is_universal INTEGER, created_at REAL)""")
        conn.execute("INSERT INTO intent_classes VALUES "
                     "('legacy','info','[]','[]','old','general',0,123.0)")
        conn.commit()
        conn.close()

        # Opening with DoSyncDB must migrate (add the column) without losing the row.
        d = DoSyncDB(path)
        d.init()
        row = d.get_intent_class("legacy")
        check("legacy row survives migration", row is not None and row["name"] == "legacy")
        check("migrated legacy row has composition_kind None",
              row["composition_kind"] is None)
        # And the migrated DB can now mark a composition intent.
        d.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                            "d", "robotics", composition_kind="perimeter")
        check("migrated DB accepts a composition intent",
              d.get_intent_class("inspect_area")["composition_kind"] == "perimeter")
        d.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_migration_is_idempotent():
    path = tempfile.mktemp(suffix=".db")
    try:
        d1 = DoSyncDB(path)
        d1.init()
        d1.save_intent_class("inspect_area", "info", ["aerial"], ["take_off"],
                             "d", "robotics", composition_kind="perimeter")
        d1.close()
        # Re-open: migration runs again, must not error or lose data.
        d2 = DoSyncDB(path)
        d2.init()
        check("re-init is idempotent and preserves data",
              d2.get_intent_class("inspect_area")["composition_kind"] == "perimeter")
        d2.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                _FAIL += 1
                print(f"  \u2717  {name} — EXCEPTION: {e}")
    print(f"\n{_PASS}/{_PASS + _FAIL} composition_kind DB tests passed.")
    if _FAIL:
        raise SystemExit(1)
