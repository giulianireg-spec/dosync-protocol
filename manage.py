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
import json
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

    db_sub.add_parser("audit-reset", help="Reset broken audit log chain (creates backup first)")
    p_abak = db_sub.add_parser("audit-backup", help="Back up the audit log to a JSON file (does not modify the log)")
    p_abak.add_argument("--out", default=None, help="Backup file path (default: audit_backup_<ts>.json)")
    p_aver = db_sub.add_parser("audit-verify", help="Verify the audit log SHA-256 chain (live DB or a backup file)")
    p_aver.add_argument("--file", default=None, help="Verify a backup file instead of the live DB")
    p_arst = db_sub.add_parser("audit-restore", help="Restore the audit log from a backup file")
    p_arst.add_argument("--file", required=True, help="Backup file to restore from")
    p_arst.add_argument("--force", action="store_true", help="Overwrite a non-empty audit log")

    # ── certs subcommand ───────────────────────────────────────────────────────
    certs_parser = sub.add_parser("certs", help="TLS certificate management")
    certs_sub    = certs_parser.add_subparsers(dest="command")

    certs_sub.add_parser("status", help="Show TLS certificate status and expiry dates")

    p_rotate = certs_sub.add_parser("rotate", help="Renew hub TLS certificate (CA unchanged)")
    p_rotate.add_argument("--ip", default=None, help="Hub IP address (auto-detected if omitted)")
    p_rotate.add_argument("--force", action="store_true", help="Rotate even if cert is not near expiry")
    p_rotate.add_argument("--restart", action="store_true", default=True,
                          help="Restart hub service after rotation (default: true)")
    p_rotate.add_argument("--no-restart", dest="restart", action="store_false")

    p_rotate_adapter = certs_sub.add_parser("rotate-adapter", help="Renew an adapter TLS certificate")
    p_rotate_adapter.add_argument("name", help="Adapter name (e.g. gpio, wiz)")
    p_rotate_adapter.add_argument("--ip", default="127.0.0.1", help="Adapter IP address")

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
        if args.command == "stats":         db_stats(args)
        elif args.command == "devices":     db_devices(args)
        elif args.command == "clean":       db_clean(args)
        elif args.command == "audit-reset":   db_audit_reset(args)
        elif args.command == "audit-backup":  db_audit_backup(args)
        elif args.command == "audit-verify":  db_audit_verify(args)
        elif args.command == "audit-restore": db_audit_restore(args)

    elif args.group == "certs":
        if   args.command == "status":         certs_status(args)
        elif args.command == "rotate":         certs_rotate(args)
        elif args.command == "rotate-adapter": certs_rotate_adapter(args)
        else: certs_parser.print_help()

    else:
        parser.print_help()


def db_audit_backup(args):
    """Back up the audit log to a self-describing JSON file. Read-only."""
    from dosync import audit_backup
    db = get_db(args.db)
    entries = db.load_audit_log()
    out = args.out or f"audit_backup_{int(time.time())}.json"
    manifest = audit_backup.write_backup(entries, out)
    ok = manifest["chain_valid_at_backup"]
    print("Audit backup")
    print(f"  Entries:       {manifest['count']}")
    print(f"  Chain valid:   {'yes' if ok else 'NO — chain is broken (backup still written for review)'}")
    print(f"  payload sha256:{manifest['payload_sha256'][:32]}...")
    print(f"  Written to:    {out}")
    if not ok:
        print("  WARNING: the live chain does not verify. Investigate before trusting this log.")


def db_audit_verify(args):
    """Verify the SHA-256 chain of the live audit log, or of a backup file."""
    from dosync import audit_backup
    if args.file:
        try:
            doc = audit_backup.read_backup(args.file)   # also checks file-level checksum
        except ValueError as e:
            print(f"Audit verify — FAILED\n  {e}")
            sys.exit(1)
        entries = doc["entries"]
        source = f"backup file {args.file}"
    else:
        db = get_db(args.db)
        entries = db.load_audit_log()
        source = "live database"
    ok = audit_backup.verify_entries(entries)
    print("Audit verify")
    print(f"  Source:      {source}")
    print(f"  Entries:     {len(entries)}")
    print(f"  Chain valid: {'yes ✓' if ok else 'NO ✗ — tamper or corruption detected'}")
    sys.exit(0 if ok else 1)


def db_audit_restore(args):
    """Restore the audit log from a backup file (refuses to clobber unless --force)."""
    from dosync import audit_backup
    try:
        doc = audit_backup.read_backup(args.file)
    except ValueError as e:
        print(f"Audit restore — ABORTED\n  {e}")
        sys.exit(1)
    entries = doc["entries"]
    if not audit_backup.verify_entries(entries):
        print("Audit restore — ABORTED\n  The backup's own chain does not verify; refusing to restore a broken log.")
        sys.exit(1)

    db = get_db(args.db)
    existing = db.audit_count()
    if existing > 0 and not args.force:
        print(f"Audit restore — ABORTED\n  The audit log already has {existing} entries. "
              "Use --force to overwrite (the current log is NOT auto-backed-up; run "
              "'audit-backup' first if you want to keep it).")
        sys.exit(1)

    import sqlite3 as _sq
    conn = _sq.connect(args.db)
    try:
        conn.execute("DELETE FROM audit_log")
        for entry in entries:
            conn.execute(
                "INSERT INTO audit_log (entry_json, hash, timestamp) VALUES (?, ?, ?)",
                (json.dumps(entry), entry.get("hash", ""), entry.get("timestamp", time.time())),
            )
        conn.commit()
    finally:
        conn.close()

    # re-verify what actually landed in the DB
    db2 = get_db(args.db)
    ok = audit_backup.verify_entries(db2.load_audit_log())
    print("Audit restore")
    print(f"  Restored:    {len(entries)} entries from {args.file}")
    print(f"  Chain valid: {'yes ✓' if ok else 'NO ✗ — restore produced a broken chain!'}")
    sys.exit(0 if ok else 1)


def db_audit_reset(args):
    """
    Reset the audit log chain after detecting integrity violations.

    This command:
    1. Exports the current (broken) audit log to a JSON backup file
    2. Clears the audit_log table
    3. Creates a new first entry that documents the reset event,
       preserving accountability for the reset operation itself

    IMPORTANT: This operation is itself auditable. The reset entry records
    the reason, the number of previous entries, and the timestamp.
    The backup file preserves all previous entries for external review.

    Use this ONLY when the audit chain was broken by an external cause
    (e.g., a test hub writing to the production DB). Never use it to
    conceal legitimate audit entries.
    """
    import json
    import hashlib

    db_path = args.db
    if not Path(db_path).exists():
        print(f"  Error: database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Count existing entries
    cur.execute("SELECT COUNT(*) FROM audit_log")
    count = cur.fetchone()[0]

    # Export to backup
    backup_path = f"audit_log_backup_{int(time.time())}.json"
    cur.execute("SELECT entry_json, hash, timestamp FROM audit_log ORDER BY rowid")
    rows = cur.fetchall()
    backup = [{"entry": json.loads(r[0]), "hash": r[1], "timestamp": r[2]} for r in rows]
    backup_json = json.dumps(backup, indent=2, sort_keys=True)
    with open(backup_path, "w") as f:
        f.write(backup_json)
    backup_sha256 = hashlib.sha256(backup_json.encode()).hexdigest()
    print(f"  Backed up {count} entries to {backup_path}")
    print(f"  Backup SHA-256: {backup_sha256}")

    # Confirm
    print(f"  This will clear {count} audit log entries and start a new chain.")
    confirm = input("  Type YES to confirm: ").strip()
    if confirm != "YES":
        print("  Aborted.")
        conn.close()
        return

    # Clear audit log
    cur.execute("DELETE FROM audit_log")

    # Create reset entry — must match AuditLog.append() format:
    # entry_json includes prev_hash and hash INSIDE the JSON dict
    now = time.time()
    genesis_hash = hashlib.sha256(b"dosync-audit-genesis").hexdigest()
    reset_entry = {
        "type":             "audit_log_reset",
        "reason":           "Chain integrity violation — see DESIGN-PRINCIPLES.md",
        "previous_entries": count,
        "backup_file":      backup_path,
        "backup_sha256":    backup_sha256,
        "timestamp":        now,
        "prev_hash":        genesis_hash,
    }
    # Hash is calculated over the entry WITHOUT the hash field (same as AuditLog.append)
    raw = json.dumps(reset_entry, sort_keys=True)
    new_hash = hashlib.sha256(f"{genesis_hash}{raw}".encode()).hexdigest()
    reset_entry["hash"] = new_hash
    entry_json = json.dumps(reset_entry, sort_keys=True)

    cur.execute(
        "INSERT INTO audit_log (entry_json, hash, timestamp) VALUES (?, ?, ?)",
        (entry_json, new_hash, now)
    )
    conn.commit()
    conn.close()

    print(f"  {C.GREEN}Audit log reset.{C.RESET} New chain started with 1 entry.")
    print(f"  Previous entries backed up to: {backup_path}")
    print(f"  Restart the hub to pick up the new chain.")




# ── Certs commands ─────────────────────────────────────────────────────────────

def certs_status(args):
    """Show TLS certificate status and expiry for CA, hub, and all adapters."""
    try:
        from dosync.security import get_status
    except ImportError as e:
        err(f"Could not import dosync.security: {e}")
        sys.exit(1)

    header("TLS Certificate Status")
    status = get_status()

    if not status.ca_exists:
        err("CA certificate not found — run: python3 -m dosync.security setup")
        return

    ca = status.ca_info
    if ca:
        expiry = f"{ca.days_until_expiry}d remaining" if not ca.is_expired else "EXPIRED"
        flag = C.RED if ca.is_expired else (C.YELLOW if ca.is_expiring_soon else C.GREEN)
        ok(f"CA cert      expires {ca.not_after}  ({flag}{expiry}{C.RESET})")
    else:
        warn("CA cert found but could not read details")

    hub = status.hub_info
    if hub:
        expiry = f"{hub.days_until_expiry}d remaining" if not hub.is_expired else "EXPIRED"
        flag = C.RED if hub.is_expired else (C.YELLOW if hub.is_expiring_soon else C.GREEN)
        ok(f"Hub cert     expires {hub.not_after}  ({flag}{expiry}{C.RESET})")
    else:
        warn("Hub cert not found")

    for adapter in status.adapter_certs:
        expiry = f"{adapter.days_until_expiry}d remaining" if not adapter.is_expired else "EXPIRED"
        flag = C.RED if adapter.is_expired else (C.YELLOW if adapter.is_expiring_soon else C.GREEN)
        ok(f"Adapter [{adapter.subject:<20}] expires {adapter.not_after}  ({flag}{expiry}{C.RESET})")

    if status.errors:
        print()
        for e in status.errors:
            err(e)
    elif status.is_ready:
        print()
        ok("All certificates are valid.")


def certs_rotate(args):
    """Renew the hub TLS certificate. The CA is not changed."""
    try:
        from dosync.security import renew_hub_cert, detect_hub_ip, get_status
    except ImportError as e:
        err(f"Could not import dosync.security: {e}")
        sys.exit(1)

    header("Hub Certificate Rotation")

    status = get_status()
    hub = status.hub_info
    if hub and not hub.is_expiring_soon and not hub.is_expired and not args.force:
        ok(f"Hub cert is valid for {hub.days_until_expiry} more days — rotation not needed.")
        info("Use --force to rotate anyway.")
        return

    ip = args.ip or detect_hub_ip()
    info(f"Hub IP: {ip}")
    info("Renewing hub certificate (CA unchanged)...")

    try:
        renew_hub_cert(hub_ip=ip)
        ok("Hub certificate renewed.")
    except Exception as e:
        err(f"Rotation failed: {e}")
        sys.exit(1)

    if args.restart:
        info("Restarting hub service...")
        import subprocess
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "dosync"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            ok("Hub service restarted.")
        else:
            warn(f"Could not restart service automatically: {result.stderr.strip()}")
            info("Run manually: sudo systemctl restart dosync")
    else:
        warn("Restart skipped. Run: sudo systemctl restart dosync")

    print()
    ok("Rotation complete. Clients that trust the CA cert do not need updating.")
    info("The CA cert has not changed — no client redistribution required.")


def certs_rotate_adapter(args):
    """Renew a specific adapter TLS certificate."""
    try:
        from dosync.security import renew_adapter_cert
    except ImportError as e:
        err(f"Could not import dosync.security: {e}")
        sys.exit(1)

    header(f"Adapter Certificate Rotation — {args.name}")
    info(f"Adapter: {args.name}  IP: {args.ip}")

    try:
        cert_path, key_path = renew_adapter_cert(name=args.name, adapter_ip=args.ip)
        ok(f"Adapter cert renewed: {cert_path}")
        ok(f"Adapter key:          {key_path}")
    except Exception as e:
        err(f"Rotation failed: {e}")
        sys.exit(1)

    print()
    ok("Adapter cert rotation complete.")
    info("Restart the adapter process to apply the new certificate.")


if __name__ == "__main__":
    main()
