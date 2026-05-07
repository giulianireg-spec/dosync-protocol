"""
DoSync — Management CLI
=======================
Herramienta de administración del hub DoSync.

Uso:
    PYTHONPATH=. python3 manage.py keys list
    PYTHONPATH=. python3 manage.py keys create --label "mi-app"
    PYTHONPATH=. python3 manage.py keys reset
    PYTHONPATH=. python3 manage.py keys revoke <key_preview>
    PYTHONPATH=. python3 manage.py db stats
    PYTHONPATH=. python3 manage.py db clean
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Colores ───────────────────────────────────────────────────────────────────

class C:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def ok(msg):    print(f"  {C.GREEN}✓{C.RESET}  {msg}")
def err(msg):   print(f"  {C.RED}✗{C.RESET}  {msg}")
def warn(msg):  print(f"  {C.YELLOW}!{C.RESET}  {msg}")
def info(msg):  print(f"  {C.BLUE}·{C.RESET}  {msg}")
def header(msg): print(f"\n{C.BOLD}{msg}{C.RESET}")


# ── DB path helper ────────────────────────────────────────────────────────────

def get_db(db_path: str = "dosync.db"):
    from dosync.db import DoSyncDB
    db = DoSyncDB(db_path)
    db.init()
    return db

def get_auth(db):
    from dosync.auth import AuthManager
    return AuthManager(db, enabled=True)


# ── Keys commands ─────────────────────────────────────────────────────────────

def keys_list(args):
    header("API Keys")
    db   = get_db(args.db)
    auth = get_auth(db)
    keys = auth.list_keys()

    if not keys:
        warn("No API keys registered.")
        print("  Run: python3 manage.py keys create\n")
        return

    print(f"  {'Preview':<20} {'Label':<20} {'Created':<22} {'Last used'}")
    print(f"  {'-'*20} {'-'*20} {'-'*22} {'-'*20}")
    for k in keys:
        created  = datetime.fromtimestamp(k["created_at"]).strftime("%Y-%m-%d %H:%M")
        last     = datetime.fromtimestamp(k["last_used_at"]).strftime("%Y-%m-%d %H:%M") \
                   if k["last_used_at"] else "never"
        print(f"  {C.DIM}{k['key_preview']:<20}{C.RESET} "
              f"{k['label']:<20} {created:<22} {last}")
    print()


def keys_create(args):
    header("Create API Key")
    db    = get_db(args.db)
    auth  = get_auth(db)
    token = auth.generate_key(args.label)

    print()
    print(f"  {C.BOLD}{C.GREEN}New API key generated:{C.RESET}")
    print()
    print(f"  {C.BOLD}{token}{C.RESET}")
    print()
    warn("Save this token — it will NOT be shown again.")
    print()
    info(f"Label: {args.label}")
    info("Usage: Authorization: Bearer <token>")
    print()


def keys_reset(args):
    header("Reset API Keys")
    warn("This will DELETE all existing keys and generate a new one.")
    print()

    if not args.yes:
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            err("Cancelled.")
            return

    # Delete all keys directly via sqlite
    conn = sqlite3.connect(args.db)
    deleted = conn.execute("DELETE FROM api_keys").rowcount
    conn.commit()
    conn.close()

    db    = get_db(args.db)
    auth  = get_auth(db)
    token = auth.generate_key("recovery")

    print()
    ok(f"Deleted {deleted} existing key(s).")
    print()
    print(f"  {C.BOLD}{C.GREEN}New recovery key:{C.RESET}")
    print()
    print(f"  {C.BOLD}{token}{C.RESET}")
    print()
    warn("Save this token — it will NOT be shown again.")
    warn("Restart the hub server to apply changes.")
    print()


def keys_revoke(args):
    header("Revoke API Key")
    db   = get_db(args.db)
    auth = get_auth(db)
    keys = auth.list_keys()

    # Find key by preview
    matching = [k for k in keys if k["key_preview"].startswith(args.preview)]

    if not matching:
        err(f"No key found matching preview: {args.preview}")
        return

    if len(matching) > 1:
        err("Multiple keys match that preview. Be more specific.")
        for k in matching:
            info(f"{k['key_preview']} — {k['label']}")
        return

    key = matching[0]

    if not args.yes:
        confirm = input(
            f"  Revoke key '{key['key_preview']}' ({key['label']})? [yes/N]: "
        ).strip().lower()
        if confirm != "yes":
            err("Cancelled.")
            return

    # Get full hash from DB to delete
    conn = sqlite3.connect(args.db)
    rows = conn.execute("SELECT key_hash FROM api_keys").fetchall()
    conn.close()

    deleted = False
    for row in rows:
        if row[0].startswith(args.preview.replace("...", "")):
            auth.delete_key(row[0])
            deleted = True
            break

    if deleted:
        ok(f"Key '{key['key_preview']}' ({key['label']}) revoked.")
    else:
        err("Could not find key hash to delete.")
    print()


# ── DB commands ───────────────────────────────────────────────────────────────

def db_stats(args):
    header("Database Stats")
    db = get_db(args.db)
    s  = db.stats()

    info(f"Path:          {s['db_path']}")
    info(f"Size:          {s['db_size_kb']} KB")
    info(f"Devices:       {s['devices']}")
    info(f"Audit entries: {s['audit_entries']}")

    # Count keys
    conn = sqlite3.connect(args.db)
    keys = conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
    pres = conn.execute("SELECT COUNT(*) FROM presence_signals").fetchone()[0]
    prof = conn.execute("SELECT COUNT(*) FROM family_profile").fetchone()[0]
    conn.close()

    info(f"API keys:      {keys}")
    info(f"Presence sig.: {pres}")
    info(f"Family profile:{'yes' if prof else 'no'}")
    print()


def db_clean(args):
    header("Clean Database")
    warn("This removes ALL data: devices, audit log, presence signals, family profile.")
    warn("API keys are kept so you don't lose access.")
    print()

    if not args.yes:
        confirm = input("  Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            err("Cancelled.")
            return

    conn = sqlite3.connect(args.db)
    conn.execute("DELETE FROM devices")
    conn.execute("DELETE FROM audit_log")
    conn.execute("DELETE FROM presence_signals")
    conn.execute("DELETE FROM family_profile")
    conn.commit()
    conn.close()

    ok("Database cleaned. API keys preserved.")
    warn("Restart the hub server to apply changes.")
    print()


def db_devices(args):
    header("Registered Devices")
    db = get_db(args.db)
    devices = db.load_devices()

    if not devices:
        warn("No devices registered.")
        return

    for d in devices:
        adapter = d.get("adapter", "simulated")
        ip      = d.get("adapter_config", {}).get("ip", "") if d.get("adapter_config") else ""
        tags    = ", ".join(d.get("tags", []))
        print(f"  {C.BOLD}{d['device_id']}{C.RESET}")
        info(f"Name:    {d['device_name']}")
        info(f"Adapter: {adapter}" + (f" @ {ip}" if ip else ""))
        info(f"Tags:    {tags}")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="DoSync Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  keys list                     List all API keys
  keys create [--label NAME]    Generate a new API key
  keys reset  [--yes]           Delete all keys and generate a new one
  keys revoke <preview> [--yes] Revoke a specific key

  db stats                      Show database statistics
  db clean    [--yes]           Remove all data (keeps API keys)
  db devices                    List all registered devices

Examples:
  python3 manage.py keys list
  python3 manage.py keys create --label "dashboard"
  python3 manage.py keys reset --yes
  python3 manage.py keys revoke R0hwCgkd
  python3 manage.py db stats
        """,
    )
    parser.add_argument("--db", default="dosync.db",
                        help="Path to database (default: dosync.db)")

    sub = parser.add_subparsers(dest="group")

    # keys
    keys_parser = sub.add_parser("keys", help="Manage API keys")
    keys_sub    = keys_parser.add_subparsers(dest="command")

    keys_sub.add_parser("list", help="List all API keys")

    p_create = keys_sub.add_parser("create", help="Generate a new API key")
    p_create.add_argument("--label", default="default", help="Label for this key")

    p_reset = keys_sub.add_parser("reset", help="Delete all keys and generate new one")
    p_reset.add_argument("--yes", action="store_true", help="Skip confirmation")

    p_revoke = keys_sub.add_parser("revoke", help="Revoke a specific key")
    p_revoke.add_argument("preview", help="Key preview (first 8 chars)")
    p_revoke.add_argument("--yes", action="store_true", help="Skip confirmation")

    # db
    db_parser = sub.add_parser("db", help="Database management")
    db_sub    = db_parser.add_subparsers(dest="command")

    db_sub.add_parser("stats",   help="Show database statistics")
    db_sub.add_parser("devices", help="List all registered devices")

    p_clean = db_sub.add_parser("clean", help="Remove all data (keeps API keys)")
    p_clean.add_argument("--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if not Path(args.db).exists() and args.group != "keys":
        err(f"Database not found: {args.db}")
        info("Start the hub server first to create the database.")
        sys.exit(1)

    # Dispatch
    if args.group == "keys":
        if args.command == "list":    keys_list(args)
        elif args.command == "create": keys_create(args)
        elif args.command == "reset":  keys_reset(args)
        elif args.command == "revoke": keys_revoke(args)
        else: keys_parser.print_help()

    elif args.group == "db":
        if args.command == "stats":    db_stats(args)
        elif args.command == "devices": db_devices(args)
        elif args.command == "clean":   db_clean(args)
        else: db_parser.print_help()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
