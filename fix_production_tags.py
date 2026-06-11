"""
fix_production_tags.py — Correct device tags in DoSync production DB

Applies the standard tag vocabulary from spec/TAG-VOCABULARY.md to the
physical devices in the reference deployment on Raspberry Pi 5.

Usage:
    python3 fix_production_tags.py [--db /path/to/dosync.db] [--dry-run]
"""

import sqlite3
import json
import argparse
import sys

# Tag corrections per device, based on TAG-VOCABULARY.md production reference
CORRECTIONS = {
    # WiZ bulbs — remove vendor/deprecated tags, add location tags
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
    # PIR sensor — add location tag
    "rpi-pir-01":               ["sensor", "motion", "security", "emergency", "entrance"],
    # SMS notifier — remove domain-specific tag, add emergency
    "notifier-sms-01":          ["notification", "communication", "emergency"],
}

def fix_tags(db_path: str, dry_run: bool):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    changed = 0
    for device_id, new_tags in CORRECTIONS.items():
        cursor.execute("SELECT tags FROM devices WHERE device_id = ?", (device_id,))
        row = cursor.fetchone()
        if not row:
            print(f"  ⚠ Not found: {device_id}")
            continue

        old_tags = json.loads(row[0]) if row[0] else []
        if sorted(old_tags) == sorted(new_tags):
            print(f"  ✓ {device_id}: already correct")
            continue

        print(f"  → {device_id}")
        print(f"    old: {old_tags}")
        print(f"    new: {new_tags}")

        if not dry_run:
            cursor.execute(
                "UPDATE devices SET tags = ? WHERE device_id = ?",
                (json.dumps(new_tags), device_id)
            )
            changed += 1

    if not dry_run:
        conn.commit()
        print(f"\n✓ {changed} device(s) updated")
    else:
        print(f"\n(dry-run — no changes written)")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix production device tags")
    parser.add_argument("--db", default="dosync.db", help="Path to dosync.db")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    print(f"DoSync tag fix — {'DRY RUN' if args.dry_run else 'LIVE'} — {args.db}\n")
    fix_tags(args.db, args.dry_run)
