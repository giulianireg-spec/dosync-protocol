"""
fix_production_tags.py — Correct device tags in DoSync production DB

Tags are stored inside manifest_json (JSON field in the devices table).
Applies the standard tag vocabulary from spec/TAG-VOCABULARY.md.

Usage:
    python3 fix_production_tags.py [--db dosync.db] [--dry-run]
"""

import sqlite3
import json
import argparse

CORRECTIONS = {
    "wiz-living1-01":           ["light", "emergency", "energy", "living-room"],
    "wiz-living1-02":           ["light", "emergency", "energy", "living-room"],
    "wiz-living2-01":           ["light", "emergency", "energy", "living-room"],
    "wiz-living2-02":           ["light", "emergency", "energy", "living-room"],
    "wiz-comedor-01":           ["light", "emergency", "energy", "dining-room"],
    "wiz-comedor-02":           ["light", "emergency", "energy", "dining-room"],
    "wiz-cocina-01":            ["light", "emergency", "energy", "kitchen"],
    "wiz-cocina-02":            ["light", "emergency", "energy", "kitchen"],
    "wiz-habitacion-ninos-01":  ["light", "emergency", "energy", "bedroom"],
    "wiz-habitacion-principal": ["light", "emergency", "energy", "bedroom"],
    "rpi-pir-01":               ["sensor", "motion", "security", "emergency", "entrance"],
    "notifier-sms-01":          ["notification", "communication", "emergency"],
}

def fix_tags(db_path: str, dry_run: bool):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    changed = 0
    for device_id, new_tags in CORRECTIONS.items():
        cursor.execute(
            "SELECT manifest_json FROM devices WHERE device_id = ?", (device_id,)
        )
        row = cursor.fetchone()
        if not row:
            print(f"  ⚠  Not found: {device_id}")
            continue

        manifest = json.loads(row["manifest_json"])
        old_tags = manifest.get("tags", [])

        if sorted(old_tags) == sorted(new_tags):
            print(f"  ✓  {device_id}: already correct")
            continue

        print(f"  →  {device_id}")
        print(f"     old: {old_tags}")
        print(f"     new: {new_tags}")

        if not dry_run:
            manifest["tags"] = new_tags
            cursor.execute(
                "UPDATE devices SET manifest_json = ?, updated_at = datetime('now') WHERE device_id = ?",
                (json.dumps(manifest), device_id)
            )
            changed += 1

    if not dry_run:
        conn.commit()
        print(f"\n✓  {changed} device(s) updated — restart the hub to reload from DB")
    else:
        print(f"\n(dry-run — no changes written)")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix production device tags per TAG-VOCABULARY.md")
    parser.add_argument("--db", default="dosync.db", help="Path to dosync.db (default: dosync.db)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    print(f"DoSync tag fix — {'DRY RUN' if args.dry_run else 'LIVE'} — {args.db}\n")
    fix_tags(args.db, args.dry_run)
