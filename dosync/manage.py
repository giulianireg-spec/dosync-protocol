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
import hashlib
import json
import os
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
    chosen = getattr(args, "token", None)
    try:
        token = auth.generate_key(args.label, token=chosen)
    except ValueError as e:
        print(f"\n  Refused: {e}\n")
        sys.exit(1)
    if chosen and len(chosen) < 20:
        warn("That token is short. It is accepted, but a bearer token has no "
             "lockout — prefer a passphrase of several words.")

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
    p_create.add_argument("--token", default=None,
                          help="Choose the token yourself instead of receiving a random "
                               "one (minimum 12 characters). Useful when a person, rather "
                               "than a program, has to type it.")

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
    p_aver = db_sub.add_parser("audit-verify", help="Verify the audit log SHA-256 chain (live DB, a backup file, or an archive segment)")
    p_aver.add_argument("--file", default=None, help="Verify a backup file instead of the live DB")
    p_aver.add_argument("--segment", default=None, help="Verify an archive segment file standalone (prints its sha256 for cross-checking against the live chain's audit_archived entry)")
    p_aver.add_argument("--checkpoint", default=None,
                        help="Verify the live chain against a signed checkpoint exported earlier "
                             "(detects a chain rewritten wholesale, which local checks cannot)")

    p_ex = sub.add_parser(
        "examples",
        help="Copy the bundled declarative device examples where you can edit them")
    p_ex.add_argument("--out", default=None,
                      help="Destination (default: DOSYNC_DECLARATIVE_DIR or ./declarative)")

    p_cp = db_sub.add_parser(
        "audit-checkpoint",
        help="Emit a signed checkpoint of the chain head, to store OFF this machine")
    p_cp.add_argument("--out", help="Checkpoint file (default: audit_checkpoint_<ts>.json)")
    p_cp.add_argument("--force", action="store_true",
                      help="Overwrite an existing checkpoint (discards the evidence it held)")
    p_arst = db_sub.add_parser("audit-restore", help="Restore the audit log from a backup file")
    p_arst.add_argument("--file", required=True, help="Backup file to restore from")
    p_arst.add_argument("--force", action="store_true", help="Overwrite a non-empty audit log")

    p_arc = db_sub.add_parser(
        "audit-archive",
        help="Archive the oldest chain entries to an anchored segment file (dry-run; --apply with the hub STOPPED)")
    p_arc.add_argument("--keep", type=int, default=2000,
                       help="How many recent entries stay in the live DB (default 2000)")
    p_arc.add_argument("--out", help="Segment file path (default: audit_segment_g<N>_<ts>.json)")
    p_arc.add_argument("--apply", action="store_true",
                       help="Write the archive (stop the hub first)")

    p_mig = db_sub.add_parser(
        "migrate-sensor-kind",
        help="Add SensorSpec.kind to persisted manifests (dry-run; --apply with the hub STOPPED)")
    p_mig.add_argument("--apply", action="store_true",
                       help="Write the patches (stop the hub first)")

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

    # `examples` copies files and never touches the database — requiring one
    # would mean a user could not get the examples until they had started a hub,
    # which is backwards: the examples are how they set the hub up.
    if args.group == "examples":
        declarative_examples(args)
        return

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
        elif args.command == "migrate-sensor-kind": db_migrate_sensor_kind(args)
        elif args.command == "audit-archive":  db_audit_archive(args)
        elif args.command == "audit-checkpoint": db_audit_checkpoint(args)
        elif args.command == "audit-verify":  db_audit_verify(args)
        elif args.command == "audit-restore": db_audit_restore(args)

    elif args.group == "certs":
        if   args.command == "status":         certs_status(args)
        elif args.command == "rotate":         certs_rotate(args)
        elif args.command == "rotate-adapter": certs_rotate_adapter(args)
        else: certs_parser.print_help()

    else:
        parser.print_help()


def _sensor_kind_patches(manifest: dict) -> list[tuple[str, str, str]]:
    """Compute the kind patches one persisted manifest needs.

    Returns [(sensor_id, old_kind, new_kind), ...] — empty if nothing to change.

    The rules come from the ADAPTERS, the single source of truth for what each
    sensor kind should be — not from a table duplicated here that would drift:
      * WiZ bulbs (adapter "wiz" / manufacturer "Philips WiZ"): brightness and
        state describe the lamp → device_state.
      * HA bridge entities (device_id "ha-<domain>-…"): per-sensor kinds exactly
        as HA_DOMAIN_MAP now declares them (climate keeps current_temp as
        environment — it measures the room).
      * Everything else: untouched. This migration only ADDS "kind" keys; it
        never removes or rewrites anything an operator declared.
    """
    caps = manifest.get("capabilities", {})
    sensors = caps.get("sensors", [])
    if not sensors:
        return []

    device_id = manifest.get("device_id", "")
    wanted: dict[str, str] = {}

    if manifest.get("adapter") == "wiz" or manifest.get("manufacturer") == "Philips WiZ":
        wanted = {"brightness": "device_state", "state": "device_state"}
    elif device_id.startswith("ha-"):
        from dosync.adapters.homeassistant import HA_DOMAIN_MAP
        domain = device_id.split("-", 2)[1] if device_id.count("-") >= 2 else ""
        spec = HA_DOMAIN_MAP.get(domain)
        if spec:
            wanted = {sn.id: sn.kind for sn in spec["sensors"]}

    patches = []
    for sn in sensors:
        new = wanted.get(sn.get("id"))
        old = sn.get("kind", "environment")
        if new and old != new:
            patches.append((sn["id"], old, new))
    return patches


def db_migrate_sensor_kind(args):
    """SENSOR-KIND data migration (2026-07-17).

    Persisted manifests predate SensorSpec.kind, so on restore every sensor
    defaults to "environment" — including a lamp's brightness. Re-registration
    through the adapters would fix it, but the API path is LOSSY by design
    (GET /v1/devices strips adapter_config, which holds the lamp IPs), and
    discovery generates wiz-auto-<ip> ids that would duplicate room-named
    devices. This is a data migration, so it is done as one: hub stopped, the
    manifest_json patched in place — only ADDING "kind" keys, everything else
    byte-for-byte intact — hub restarted.

    Dry-run by default; --apply writes. STOP THE HUB FIRST for --apply: SQLite
    has one writer, and the hub's in-memory registry would diverge from the DB
    until restart anyway.
    """
    db = get_db(args.db)
    manifests = db.load_devices()

    plan = []
    for m in manifests:
        patches = _sensor_kind_patches(m)
        if patches:
            plan.append((m, patches))

    if not plan:
        print("Sensor-kind migration\n  Nothing to do — every manifest already declares its kinds.")
        return

    total = sum(len(p) for _, p in plan)
    print(f"Sensor-kind migration — {len(plan)} device(s), {total} sensor(s) to patch\n")
    for m, patches in plan:
        print(f"  {m['device_id']}")
        for sid, old, new in patches:
            print(f"      {sid}: {old} -> {new}")

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply (WITH THE HUB STOPPED) to write.")
        return

    import copy
    with db._cursor() as cur:
        for m, patches in plan:
            patched = copy.deepcopy(m)
            wanted = {sid: new for sid, _, new in patches}
            for sn in patched["capabilities"]["sensors"]:
                if sn.get("id") in wanted:
                    sn["kind"] = wanted[sn["id"]]
            cur.execute(
                "UPDATE devices SET manifest_json = ? WHERE device_id = ?",
                (json.dumps(patched), patched["device_id"]),
            )
    print(f"\nApplied. Restart the hub; then re-run this command — it must report "
          f"'Nothing to do' (the migration is idempotent).")


def db_audit_archive(args):
    """AUDIT-ARCHIVE (2026-07-19): segment the chain with a hash anchor.

    The live chain grows without bound (24k entries and roughly doubling every
    few days at the reference deployment), all of it reloaded into memory at
    every hub start. Archiving moves the oldest entries to a self-describing
    SEGMENT file while keeping the cryptography honest end to end:

      * the segment records the anchor it chains FROM, so it verifies standalone;
      * the DB stores the new anchor (last archived hash), so the live chain
        verifies from there instead of genesis;
      * and the act of archiving leaves its OWN `audit_archived` entry in the
        live chain, binding the segment file's SHA-256 — the same philosophy as
        policy_modified binding the policy file: an operation this consequential
        must itself be tamper-evident. A silently swapped archive file would
        contradict the hash the chain remembers.

    Consecutive generations interlock (segment N+1's anchor == segment N's
    last_hash), so the FULL history remains verifiable by walking the segments
    in order and then the live DB. Dry-run by default; --apply writes.
    RUN WITH THE HUB STOPPED: the hub's in-memory chain would diverge until
    restart, and SQLite has one writer.
    """
    from dosync import audit_backup as ab

    keep = args.keep
    if keep < 1:
        print("audit-archive — --keep must be >= 1 (the chain head stays live)")
        sys.exit(1)

    db = get_db(args.db)
    anchor = db.get_audit_anchor() or {}
    start_anchor = anchor.get("anchor_prev_hash", ab.GENESIS)
    generation = anchor.get("generations", 0) + 1

    with db._cursor() as cur:
        cur.execute("SELECT id, entry_json FROM audit_log ORDER BY timestamp, id")
        rows = cur.fetchall()
    entries = [json.loads(r[1]) for r in rows]

    # Fail-loudly: refuse to archive a chain that does not verify. Archiving
    # would freeze the corruption into a "trusted" segment.
    if not ab.verify_entries(entries, start_anchor):
        print("audit-archive — REFUSED\n  The live chain does not verify from its anchor. "
              "Investigate before archiving; archiving now would enshrine the corruption.")
        sys.exit(1)

    if len(entries) <= keep:
        print(f"audit-archive\n  Nothing to do — {len(entries)} entries, keep={keep}.")
        return

    cut = len(entries) - keep
    archived, remaining = entries[:cut], entries[cut:]
    out = args.out or f"audit_segment_g{generation}_{int(time.time())}.json"

    print(f"audit-archive — generation {generation}")
    print(f"  Live entries:  {len(entries)}")
    print(f"  To archive:    {len(archived)}  (through hash {archived[-1]['hash'][:16]}...)")
    print(f"  To keep live:  {len(remaining)}")
    print(f"  Segment file:  {out}")
    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply (WITH THE HUB STOPPED) to archive.")
        return

    manifest = ab.write_segment(archived, out, start_anchor, generation)
    seg_sha = ab.file_sha256(out)

    # The archival is itself a chain event: append audit_archived at the tail,
    # computed EXACTLY like AuditLog.append (prev_hash, timestamp, sorted-json
    # sha256) so the live chain stays verifiable.
    tail_hash = remaining[-1]["hash"]
    # Continue the sequence: this marker is a chain entry like any other, and a
    # hole in the numbering is indistinguishable from a removed entry.
    _seqs = [e["seq"] for e in entries if e.get("seq") is not None]
    arch_entry = {
        "type": "audit_archived",
        "seq": (max(_seqs) + 1) if _seqs else None,
        "generation": generation,
        "archived_count": len(archived),
        "segment_first_hash": manifest["first_hash"],
        "segment_last_hash": manifest["last_hash"],
        "segment_file": os.path.basename(out),
        "segment_sha256": seg_sha,
        "prev_hash": tail_hash,
        "timestamp": time.time(),
    }
    raw = json.dumps(arch_entry, sort_keys=True)
    arch_entry["hash"] = hashlib.sha256(raw.encode()).hexdigest()

    archived_ids = [r[0] for r in rows[:cut]]
    with db._cursor() as cur:
        cur.executemany("DELETE FROM audit_log WHERE id = ?",
                        [(i,) for i in archived_ids])
        cur.execute("INSERT INTO audit_log (entry_json, hash, timestamp) VALUES (?, ?, ?)",
                    (json.dumps(arch_entry), arch_entry["hash"], arch_entry["timestamp"]))

    # The head high-water mark must follow the operation that just rewrote the
    # log. Without this, the mark still pointed at the pre-archive tail and the
    # next `audit-verify` reported "entries removed from the end" after a
    # perfectly legitimate archive — a false alarm that trains operators to
    # ignore the real one.
    db.set_audit_head(arch_entry.get("seq"), arch_entry["hash"])

    db.set_audit_anchor({
        "anchor_prev_hash": manifest["last_hash"],
        "generations": generation,
        "archived_total": anchor.get("archived_total", 0) + len(archived),
        "last_archive_file": out,
        "last_archive_sha256": seg_sha,
        "archived_at": time.time(),
    })

    print(f"\nArchived. Segment sha256: {seg_sha[:32]}...")
    print(f"  The live chain now anchors at {manifest['last_hash'][:16]}... and carries an")
    print(f"  audit_archived entry binding the segment file. Restart the hub; then run")
    print(f"  'db audit-verify' (live) and 'db audit-verify --segment {out}' to confirm both.")


def declarative_examples(args):
    """Copy the bundled device examples somewhere the operator can edit them.

    The examples ship inside the package, which is necessary and not sufficient:
    a user who installed from PyPI has them on disk under site-packages, where
    nobody looks and nothing should be edited. This puts them where the hub
    reads device files.
    """
    from dosync.declarative import bundled_examples_dir, copy_examples_to

    target = args.out or os.environ.get("DOSYNC_DECLARATIVE_DIR", "declarative")
    header("Declarative examples")
    written = copy_examples_to(target)
    if written:
        for name in written:
            print(f"  {C.GREEN}+{C.RESET} {name}")
        print(f"\n  Copied {len(written)} example(s) to {target}/")
        print("  Edit one to describe your device, then restart the hub.")
    else:
        print(f"  Everything already present in {target}/ — nothing copied.")
        print(f"  (Originals: {bundled_examples_dir()})")
    print()


def db_audit_checkpoint(args):
    """Emit a SIGNED checkpoint of the chain head, for storage off this machine.

    The hash chain proves no entry was altered. The head record in `audit_meta`
    additionally reveals a removed tail. Neither survives an adversary who can
    write to the whole database: they rewrite the entries, recompute the hashes,
    and update the metadata to match. Nothing that lives only on this machine
    can detect that, because the attacker owns everything the check would
    consult.

    A checkpoint breaks that circle by leaving. It states "at this moment the
    chain had N entries ending in H", signed with the hub's Ed25519 key. Copy it
    somewhere the hub cannot reach — another host, a mailbox, a printout — and a
    later `audit-verify --checkpoint` proves the chain still contains that exact
    history. A rewritten chain cannot match a checkpoint it never saw.

    Two honest limits, stated because a security control that oversells itself
    is worse than none: an attacker holding the signing key can forge new
    checkpoints (keep the key off the hub for high-assurance deployments), and a
    checkpoint left ON this machine protects nothing — its value comes entirely
    from being stored somewhere else.
    """
    from dosync import cert_signing

    db = get_db(args.db)
    head = db.get_audit_head()
    entries = db.load_audit_log()

    if not entries:
        print("audit-checkpoint\n  Chain is empty — nothing to checkpoint.")
        return

    last = entries[-1]
    anchor = db.get_audit_anchor() or {}
    # `seq` is absent on entries written before sequence numbers existed. A
    # signed compliance artifact should not carry a null field an auditor has to
    # interpret, so it is omitted entirely in that case — `entry_count` and
    # `head_hash` already identify the attested point, and the checkpoint check
    # matches on the hash.
    _seq = last.get("seq")
    doc = {
        "format_version": "dosync-audit-checkpoint/v1",
        "head_hash":      last.get("hash"),
        "entry_count":    len(entries),
        "anchor_prev_hash": anchor.get("anchor_prev_hash", "0" * 64),
        "archived_total": anchor.get("archived_total", 0),
        "created_at":     time.time(),
    }
    if _seq is not None:
        doc["seq"] = _seq
    signed = cert_signing.sign_report(doc)

    out = args.out or f"audit_checkpoint_{int(time.time())}.json"

    # A checkpoint is evidence, and the OLDEST one is the most valuable — each
    # attests to a longer stretch of history, so the one a careless filename
    # clobbers is the one that narrows an attacker's window most. Found in
    # production: a suggested filename with only date granularity replaced that
    # morning's checkpoint, and the runbook's systemd unit used `%i`, which
    # expands to nothing outside a template unit and would have overwritten the
    # same file every day. Silently destroying evidence is not an option here.
    if os.path.exists(out) and not args.force:
        print("audit-checkpoint — REFUSED")
        print(f"  {out} already exists. A checkpoint is evidence; overwriting it")
        print( "  discards proof about the history it attested to. Choose another")
        print( "  name (the default includes a timestamp), or pass --force if you")
        print( "  are certain this one is expendable.")
        sys.exit(1)

    with open(out, "w") as f:
        json.dump(signed, f, indent=2, sort_keys=True)

    print("audit-checkpoint")
    _seq_note = f" (seq {doc['seq']})" if "seq" in doc else \
        " (chain predates sequence numbers)"
    print(f"  Entries:    {doc['entry_count']}{_seq_note}")
    print(f"  Head:       {doc['head_hash'][:32]}...")
    if doc["archived_total"]:
        print(f"  Archived:   {doc['archived_total']} entries in earlier segments")
    print(f"  Written:    {out}")
    print(f"  Signed by:  {signed['signature']['public_key'][:16]}...")
    print("\n  STORE THIS OFF THIS MACHINE. A checkpoint kept on the hub proves")
    print("  nothing against an attacker who controls the hub.")


def db_audit_backup(args):
    """Back up the audit log to a self-describing JSON file. Read-only."""
    from dosync import audit_backup
    db = get_db(args.db)
    entries = db.load_audit_log()
    out = args.out or f"audit_backup_{int(time.time())}.json"
    _anchor = db.get_audit_anchor()
    try:
        manifest = audit_backup.write_backup(
            entries, out,
            anchor_prev_hash=(_anchor or {}).get("anchor_prev_hash", audit_backup.GENESIS))
    except OSError as e:
        # This runs unattended from a systemd timer: a Python traceback in the
        # journal tells an operator nothing actionable. Say what failed and why.
        print(f"Audit backup — FAILED\n  Cannot write {out}: {e.strerror}")
        print("  Check the directory exists and is writable by this user.")
        sys.exit(1)
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
    anchor_prev = audit_backup.GENESIS
    anchor_note = ""
    if getattr(args, "segment", None):
        try:
            doc = audit_backup.read_segment(args.segment)
        except ValueError as e:
            print(f"Audit verify — FAILED\n  {e}")
            sys.exit(1)
        entries = doc["entries"]
        m = doc["manifest"]
        anchor_prev = m["anchor_prev_hash"]
        source = f"archive segment {args.segment} (generation {m['generation']})"
        anchor_note = (f"  Anchors from: {anchor_prev[:16]}...\n"
                       f"  File sha256:  {audit_backup.file_sha256(args.segment)[:32]}... "
                       f"(cross-check against the audit_archived entry in the live chain)")
    elif args.file:
        try:
            doc = audit_backup.read_backup(args.file)   # also checks file-level checksum
        except ValueError as e:
            print(f"Audit verify — FAILED\n  {e}")
            sys.exit(1)
        entries = doc["entries"]
        anchor_prev = doc.get("anchor_prev_hash", audit_backup.GENESIS)
        source = f"backup file {args.file}"
    else:
        db = get_db(args.db)
        entries = db.load_audit_log()
        db_anchor = db.get_audit_anchor()
        if db_anchor:
            anchor_prev = db_anchor.get("anchor_prev_hash", audit_backup.GENESIS)
            anchor_note = (f"  Anchored:    generation {db_anchor.get('generations')}, "
                           f"{db_anchor.get('archived_total')} entries archived "
                           f"(latest: {db_anchor.get('last_archive_file')})")
        source = "live database"
    ok = audit_backup.verify_entries(entries, anchor_prev)
    print("Audit verify")
    print(f"  Source:      {source}")
    print(f"  Entries:     {len(entries)}")
    if anchor_note:
        print(anchor_note)
    print(f"  Chain valid: {'yes ✓' if ok else 'NO ✗ — tamper or corruption detected'}")

    # Links prove nothing was ALTERED. They cannot prove nothing was REMOVED
    # from the end — every surviving link of a truncated chain is intact. The
    # two checks below compare the chain against records kept elsewhere.
    #
    # Both treat their reference as a HIGH-WATER MARK, not as a mirror of the
    # tail. Archiving legitimately shortens the live chain and the head lags by
    # design (it is written in batches), so demanding equality reported tamper
    # after every archive — a false alarm that teaches operators to ignore the
    # real one, and broke the exit code that automation depends on.
    if not getattr(args, "file", None) and not getattr(args, "segment", None):
        _db = get_db(args.db)
        head = _db.get_audit_head()
        if head and entries:
            by_seq = {e.get("seq"): e.get("hash") for e in entries
                      if e.get("seq") is not None}
            seqs = [s for s in by_seq if s is not None]
            m_seq, m_hash = head.get("seq"), head.get("hash")
            if m_seq is None or not seqs:
                print("  Head record: present (chain predates sequence numbers)")
            elif m_seq in by_seq:
                if by_seq[m_seq] == m_hash:
                    behind = max(seqs) - m_seq
                    extra = f" — chain has grown {behind} entries since" if behind else ""
                    print(f"  Head record: consistent ✓ (entry {m_seq}{extra})")
                else:
                    print(f"  Head record: ALTERED ✗ — entry {m_seq} does not match "
                          f"the recorded hash")
                    ok = False
            elif m_seq > max(seqs):
                print(f"  Head record: TRUNCATED ✗ — recorded up to entry {m_seq}, "
                      f"chain now ends at {max(seqs)}")
                ok = False
            else:
                print(f"  Head record: entry {m_seq} has been archived "
                      f"(verify it in the segment)")
        elif entries:
            print("  Head record: none (chain predates head checkpoints)")

    if getattr(args, "checkpoint", None):
        from dosync import cert_signing
        try:
            with open(args.checkpoint) as f:
                cp = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  Checkpoint:  FAILED to read — {e}")
            sys.exit(1)
        sig_ok, sig_msg = cert_signing.verify_report(cp)
        print(f"  Checkpoint:  signature {'valid ✓' if sig_ok else 'INVALID ✗ — ' + sig_msg}")
        if not sig_ok:
            ok = False
        else:
            cp_head = cp.get("head_hash")
            hashes = [e.get("hash") for e in entries]
            # How much was archived AFTER the checkpoint was taken. Entries the
            # checkpoint counted may now live in a segment rather than the log,
            # and that is an operation the operator performed, not an attack.
            now_anchor = _db.get_audit_anchor() if not getattr(args, "file", None) else {}
            archived_since = ((now_anchor or {}).get("archived_total", 0)
                              - cp.get("archived_total", 0))

            if cp_head in hashes:
                pos = hashes.index(cp_head) + 1
                expected = cp.get("entry_count")
                if expected is not None:
                    # Archiving removes entries from the FRONT, so the attested
                    # head slides down by exactly that many positions.
                    expected -= max(archived_since, 0)
                if expected is not None and pos != expected:
                    print(f"    ✗ attested head is at entry {pos}, expected {expected} "
                          f"— history before the checkpoint was altered")
                    ok = False
                else:
                    note = (f" ({archived_since} entries archived since)"
                            if archived_since > 0 else "")
                    print(f"    attested head found at entry {pos} of {len(entries)} ✓{note}")
            elif archived_since > 0:
                # The attested head was archived out of the live log. The live
                # database cannot confirm or deny it; the segment can.
                print(f"    attested head not in the live chain — {archived_since} "
                      f"entries archived since the checkpoint")
                print(f"    → verify it inside the segment: "
                      f"audit-verify --segment {(now_anchor or {}).get('last_archive_file', '<segment>')}")
            else:
                print(f"    ✗ attested head {str(cp_head)[:16]}... NOT PRESENT in the chain "
                      f"— this history was rewritten or replaced")
                ok = False

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
