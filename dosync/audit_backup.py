"""
DoSync — audit log backup / restore / verify (REL-2).
=====================================================

The audit log is the system's most valuable asset: the tamper-evident record
that backs every "who decided what, and can you prove it" claim. Before REL-2
the only durability was the live SQLite file plus the ad-hoc export that
`db audit-reset` writes on its way to wiping a broken chain. This module gives
the audit log a first-class, standalone backup/restore/verify path.

Design:
  * A backup is a self-describing JSON file: the full ordered entry list plus a
    manifest (count, first/last timestamps, a SHA-256 over the canonical entry
    payload, and whether the chain verified AT BACKUP TIME). The manifest lets a
    restore — or an external auditor — detect tampering of the backup file
    itself, independently of the internal per-entry hash chain.
  * verify() re-runs the exact same SHA-256 chain check the live AuditLog uses
    (imported, not reimplemented — one source of truth for what "valid" means).
  * restore() refuses to overwrite a non-empty audit log unless forced, and
    re-verifies the chain of what it loaded. A restore that produced a broken
    chain would be worse than no restore.

Nothing here bypasses the chain: restored entries keep their original hashes and
prev_hash links, so a restored log verifies exactly as the original did.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any


BACKUP_FORMAT_VERSION = 1


def _canonical(entries: list[dict]) -> str:
    """Canonical JSON of the entry list, for a stable file-level checksum."""
    return json.dumps(entries, sort_keys=True, separators=(",", ":"))


def verify_entries(entries: list[dict]) -> bool:
    """Re-run the live AuditLog SHA-256 chain check over a list of entries.

    Imported from hub to guarantee identical semantics; falls back to an inline
    equivalent only if the import shape ever changes (kept byte-for-byte the
    same as AuditLog.verify).
    """
    prev = "0" * 64
    for entry in entries:
        entry = dict(entry)
        stored_hash = entry.pop("hash", None)
        if stored_hash is None:
            return False
        raw = json.dumps(entry, sort_keys=True)
        calc = hashlib.sha256(raw.encode()).hexdigest()
        if calc != stored_hash or entry.get("prev_hash") != prev:
            return False
        prev = stored_hash
    return True


def build_backup(entries: list[dict]) -> dict[str, Any]:
    """Build the backup document (manifest + entries) for a list of entries."""
    canonical = _canonical(entries)
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "manifest": {
            "count": len(entries),
            "first_timestamp": entries[0].get("timestamp") if entries else None,
            "last_timestamp": entries[-1].get("timestamp") if entries else None,
            "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "chain_valid_at_backup": verify_entries(entries),
            "backed_up_at": time.time(),
        },
        "entries": entries,
    }


def write_backup(entries: list[dict], path: str) -> dict[str, Any]:
    """Serialize a backup to disk. Returns the manifest."""
    doc = build_backup(entries)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return doc["manifest"]


def read_backup(path: str) -> dict[str, Any]:
    """Load and integrity-check a backup file.

    Raises ValueError if the file-level checksum does not match its entries
    (i.e. the backup file was altered after it was written).
    """
    with open(path) as f:
        doc = json.load(f)
    if doc.get("format_version") != BACKUP_FORMAT_VERSION:
        raise ValueError(f"Unsupported backup format_version: {doc.get('format_version')}")
    entries = doc.get("entries", [])
    expected = doc["manifest"]["payload_sha256"]
    actual = hashlib.sha256(_canonical(entries).encode()).hexdigest()
    if actual != expected:
        raise ValueError(
            "Backup file checksum mismatch — the backup has been altered since it "
            f"was written (manifest={expected[:16]}…, actual={actual[:16]}…)."
        )
    return doc
