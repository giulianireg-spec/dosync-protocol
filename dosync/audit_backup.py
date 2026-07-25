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


GENESIS = "0" * 64


def verify_entries(entries: list[dict], anchor_prev_hash: str = GENESIS) -> bool:
    """Re-run the live AuditLog SHA-256 chain check over a list of entries.

    Imported from hub to guarantee identical semantics; falls back to an inline
    equivalent only if the import shape ever changes (kept byte-for-byte the
    same as AuditLog.verify).

    AUDIT-ARCHIVE (2026-07-19): a chain no longer necessarily starts at the
    genesis hash. When older entries have been archived to a segment file, the
    live chain's first entry links to the LAST ARCHIVED entry — that hash is
    the anchor, and verification starts from it. Genesis is just the anchor of
    a chain that has never been archived.
    """
    prev = anchor_prev_hash
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


def build_backup(entries: list[dict],
                 anchor_prev_hash: str = GENESIS) -> dict[str, Any]:
    """Build the backup document (manifest + entries) for a list of entries.

    An archived chain does not start at genesis; the backup records the anchor
    it verifies from, so the file stays self-contained: `verify --file` needs
    nothing but the file."""
    canonical = _canonical(entries)
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "anchor_prev_hash": anchor_prev_hash,
        "manifest": {
            "count": len(entries),
            "first_timestamp": entries[0].get("timestamp") if entries else None,
            "last_timestamp": entries[-1].get("timestamp") if entries else None,
            "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "chain_valid_at_backup": verify_entries(entries, anchor_prev_hash),
            "backed_up_at": time.time(),
        },
        "entries": entries,
    }


def write_backup(entries: list[dict], path: str,
                 anchor_prev_hash: str = GENESIS) -> dict[str, Any]:
    """Serialize a backup to disk. Returns the manifest."""
    doc = build_backup(entries, anchor_prev_hash)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return doc["manifest"]


SEGMENT_FORMAT_VERSION = "dosync-audit-segment/v1"


def write_segment(entries: list[dict], path: str, anchor_prev_hash: str,
                  generation: int) -> dict[str, Any]:
    """Write an ARCHIVE SEGMENT: a slice of the chain moved out of the live DB.

    The segment is self-describing and independently verifiable: it records the
    anchor it chains FROM (the previous segment's last hash, or genesis for the
    first generation), so `verify_entries(seg["entries"], seg["anchor_prev_hash"])`
    proves its integrity standalone — and consecutive generations interlock:
    segment N+1's anchor MUST equal segment N's last_hash. The full history is
    verifiable end to end by walking the segments in order, then the live DB.
    """
    if not entries:
        raise ValueError("refusing to write an empty segment")
    canonical = _canonical(entries)
    doc = {
        "format_version": SEGMENT_FORMAT_VERSION,
        "manifest": {
            "generation": generation,
            "anchor_prev_hash": anchor_prev_hash,
            "first_hash": entries[0].get("hash"),
            "last_hash": entries[-1].get("hash"),
            "count": len(entries),
            "first_timestamp": entries[0].get("timestamp"),
            "last_timestamp": entries[-1].get("timestamp"),
            "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "archived_at": time.time(),
        },
        "entries": entries,
    }
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
    return doc["manifest"]


def read_segment(path: str) -> dict[str, Any]:
    """Load and integrity-check an archive segment file."""
    with open(path) as f:
        doc = json.load(f)
    if doc.get("format_version") != SEGMENT_FORMAT_VERSION:
        raise ValueError(f"Not an audit segment file: {doc.get('format_version')}")
    entries = doc.get("entries", [])
    expected = doc["manifest"]["payload_sha256"]
    actual = hashlib.sha256(_canonical(entries).encode()).hexdigest()
    if actual != expected:
        raise ValueError("Segment payload checksum mismatch — the file was altered after writing")
    return doc


def file_sha256(path: str) -> str:
    """SHA-256 of the file bytes as written — the fingerprint the live chain's
    `audit_archived` entry binds, so the segment cannot be silently swapped."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


CHECKPOINT_FORMAT_VERSION = "dosync-audit-checkpoint/v1"


def build_checkpoint(entries: list[dict], anchor: dict | None) -> dict[str, Any]:
    """The attested statement: at this moment the chain held N entries ending in H.

    Shared by the CLI and the hub's scheduler so a checkpoint means the same
    thing however it was produced — a document whose meaning depended on which
    code path wrote it would be worthless as evidence.

    `seq` is omitted rather than null for chains written before sequence numbers
    existed: a signed compliance artifact should not hand an auditor a field to
    interpret.
    """
    if not entries:
        raise ValueError("refusing to checkpoint an empty chain")
    last = entries[-1]
    anchor = anchor or {}
    doc = {
        "format_version":   CHECKPOINT_FORMAT_VERSION,
        "head_hash":        last.get("hash"),
        "entry_count":      len(entries),
        "anchor_prev_hash": anchor.get("anchor_prev_hash", GENESIS),
        "archived_total":   anchor.get("archived_total", 0),
        "created_at":       time.time(),
    }
    if last.get("seq") is not None:
        doc["seq"] = last["seq"]
    return doc


def checkpoint_filename(now: float | None = None) -> str:
    """A name unique per run, in UTC.

    Learned from production: a date-only name silently replaced that morning's
    checkpoint, and the older one — attesting to a longer history — was the more
    valuable of the two.
    """
    import datetime as _dt
    ts = _dt.datetime.fromtimestamp(now or time.time(), _dt.timezone.utc)
    return f"cp-{ts.strftime('%Y%m%dT%H%M%SZ')}.json"


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
