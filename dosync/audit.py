"""Tamper-evident audit log.

Extracted verbatim from `hub.py` on 2 September 2026. It lived there beside the
capability registry, four resolvers, the timed executor and the device-health
monitor — five responsibilities in one 3,710-line module, none of which could be
changed without risk to the other four.

Nothing about the behaviour changed in the move. The chaining, the persistence
callback, the head checkpoint and the verification are the code that shipped in
0.6.3; only its address is different.

What makes this worth keeping separate: the chain records not just which device
was called but why the resolver chose it, which is what makes an execution
auditable after the fact rather than merely logged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

# Deliberately "dosync.hub" and not "dosync.audit": these records went to that
# logger in 0.6.3, and an operator filtering on it would silently stop seeing
# audit warnings the day this module moved. An extraction that changes where log
# records land is not the behaviour-preserving move it claims to be.
log = logging.getLogger("dosync.hub")


# ── Audit log ─────────────────────────────────────────────────────────────────

class AuditLog:
    """
    Tamper-evident chained log for all intent executions.
    SHA-256 chains each entry to the previous one.
    """

    def __init__(self):
        self._entries: list[dict] = []
        self._prev_hash = "0" * 64
        self._persist_cb = None   # set by DoSyncHub after db.init()
        # Head checkpoint recorder, wired by DoSyncHub. Stores (seq, hash) of an
        # entry somewhere OTHER than the audit_log table, so removing rows from
        # that table contradicts a record the deletion did not touch.
        self._checkpoint_cb = None
        self._next_seq = 0
        # The head is a HIGH-WATER MARK, not a mirror of the tail: it asserts
        # "the chain contained at least this much". That is what lets it be
        # written in batches instead of on every append — writing it every time
        # cost 57% extra per entry (2.69ms vs 1.72ms on a Pi 5), which lands
        # precisely during an emergency, when one intent can produce dozens of
        # entries in seconds. Batching only widens the window of entries a
        # truncation could hide; it never produces a false alarm, because a head
        # that lags behind the chain means the chain GREW, which is not an
        # attack. Layer 3 (exported checkpoints) covers the adversarial case, so
        # layer 2 can afford to be cheap.
        self._head_every = int(os.environ.get("DOSYNC_AUDIT_HEAD_EVERY", "25"))
        self._since_head = 0
        # AUDIT-ARCHIVE: where THIS chain begins. Genesis for a chain that has
        # never been archived; the last archived entry's hash otherwise — set at
        # restore, and updated in place when the hub archives itself.
        #
        # This assignment belongs HERE and nowhere else. It spent one release
        # inside flush_head() by accident, which meant every checkpoint write
        # silently reset a live archived chain's anchor to genesis: in-memory
        # verify() then failed, and /v1/status reported audit_integrity=false on
        # a chain that was perfectly intact.
        self.anchor_prev_hash = "0" * 64
        self.anchor_prev_hash = "0" * 64

    def _record_head(self, force: bool = False) -> None:
        """Persist the head high-water mark, batched. Best-effort by design: a
        failure here weakens truncation detection, it does not corrupt the
        chain, so it is logged rather than raised."""
        if self._checkpoint_cb is None or not self._entries:
            return
        self._since_head += 1
        if not force and self._since_head < self._head_every:
            return
        self._since_head = 0
        last = self._entries[-1]
        try:
            self._checkpoint_cb(last.get("seq"), last.get("hash"))
        except Exception as e:  # pragma: no cover - defensive
            log.warning("Audit head not recorded: %s", e)

    def flush_head(self) -> None:
        """Force the head to disk — called at shutdown and before operations
        that rewrite the log, so the mark is current when it matters."""
        self._record_head(force=True)

    def append(self, entry: dict) -> str:
        # Monotonic sequence number (2026-07-25). The hash chain alone cannot see
        # a TRUNCATION: drop the last entry and what remains still verifies,
        # because every surviving link is intact. A sequence number makes the
        # chain's LENGTH part of what it asserts, so a missing tail becomes
        # visible once the expected head is known (see checkpoints below).
        #
        # It is inside the hashed content, so it cannot be edited without
        # breaking the link. Entries written before this change have no `seq`
        # and are accepted as-is — a chain in production must keep verifying
        # across the upgrade, so verification only checks continuity where the
        # field exists.
        entry["seq"] = self._next_seq
        entry["prev_hash"] = self._prev_hash
        entry["timestamp"] = time.time()
        raw = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(raw.encode()).hexdigest()
        entry["hash"] = entry_hash
        self._prev_hash = entry_hash
        self._next_seq += 1
        self._entries.append(entry)
        if self._persist_cb:
            self._persist_cb(entry)
        self._record_head()
        return entry_hash

    def verify(self, head_mark: dict | None = None) -> bool:
        """Verify the chain's links, and optionally that it still CONTAINS a
        previously recorded point.

        Links alone prove no entry was altered. They cannot prove none was
        REMOVED from the end — every surviving link of a truncated chain is
        intact. `head_mark` is a `{"seq", "hash"}` recorded elsewhere (see
        `db.set_audit_head`), and it is treated as a HIGH-WATER MARK rather than
        a mirror of the tail:

          * the chain grew past it → fine, that is what a live chain does;
          * the marked entry is present with the marked hash → fine;
          * the marked entry is present with a DIFFERENT hash → altered;
          * the chain no longer reaches that sequence → truncated.

        Treating it as equality was wrong and produced false alarms: the mark
        lags behind by design (it is written in batches), and after archiving
        the tail is a marker the mark had never seen. A security check that
        cries wolf during normal operation teaches operators to ignore it.
        """
        prev = self.anchor_prev_hash
        prev_seq = None
        max_seq = None
        by_seq: dict[int, str] = {}
        for entry in self._entries:
            stored_hash = entry.pop("hash")
            raw = json.dumps(entry, sort_keys=True)
            calc = hashlib.sha256(raw.encode()).hexdigest()
            entry["hash"] = stored_hash
            if calc != stored_hash or entry["prev_hash"] != prev:
                return False
            seq = entry.get("seq")
            if seq is not None:
                # Gaps and reordering are breaks even when every hash matches.
                if prev_seq is not None and seq != prev_seq + 1:
                    return False
                prev_seq = seq
                max_seq = seq
                by_seq[seq] = stored_hash
            prev = stored_hash

        if head_mark:
            m_seq, m_hash = head_mark.get("seq"), head_mark.get("hash")
            if m_seq is not None and m_hash is not None:
                if m_seq in by_seq:
                    return by_seq[m_seq] == m_hash
                # Absent: truncated if the chain stops short of the mark.
                # Below the chain's range means those entries were ARCHIVED,
                # which is a documented operation, not tampering.
                if max_seq is not None and m_seq > max_seq:
                    return False
        return True

    def entries(self) -> list[dict]:
        return list(self._entries)


# ── DoSync Hub ────────────────────────────────────────────────────────────────
