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

import asyncio
import shutil
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

# ── Checkpoint policy ─────────────────────────────────────────────────────────
#
# Moved here with CheckpointKeeper, which calls both. They were module-level
# functions in hub.py; leaving them there would have meant audit.py importing
# from hub.py, which imports from audit.py. Re-exported from dosync.hub, where
# the tests and the deployment-contract checks look for them.





def _assurance_is_regulated() -> bool:
    """Does this deployment have to prove anything to someone else?

    DoSync's audit machinery serves two different needs that look alike. In a
    home or a small shop the operator is the only interested party: the chain is
    a log that answers "what did the system do", and nobody will ever be asked
    to demonstrate it was not edited. In a care facility, a plant, or anywhere a
    regulator or an insurer can ask, the same chain has to function as EVIDENCE,
    which requires exported checkpoints and a routine behind them.

    Defaulting to `standard` is deliberate. Warning a household about an
    adversary who controls the host means warning them about themselves — a
    warning they cannot act on and would be right to ignore, which is how a
    system teaches people that its warnings are noise. A deployment that needs
    the stronger posture says so, and gets told when its evidence is incomplete.
    """
    return os.environ.get("DOSYNC_ASSURANCE", "standard").lower() in (
        "regulated", "high", "audited")



def checkpoint_export_mode() -> str:
    """How checkpoints leave this host, derived from CONFIGURATION.

    Read at status time rather than remembered from the last write. The state
    used to be set only when a checkpoint was produced, so a hub that had just
    restarted reported "unknown" about a setting it could see plainly — and a
    monitor checking this field would be blind for a whole interval after every
    restart, which is exactly when someone is most likely to be watching.
    """
    if os.environ.get("DOSYNC_CHECKPOINT_EXPORT_EXTERNAL", "").lower() in (
            "1", "true", "yes"):
        return "external"
    if os.environ.get("DOSYNC_CHECKPOINT_EXPORT_DIR"):
        return "configured"
    return "not_configured"


class CheckpointKeeper:
    """Scheduling, writing, exporting and archiving of audit checkpoints.

    These four methods lived on `DoSyncHub` and were left behind when `AuditLog`
    moved out on 2 September — an extraction that took the chain and left its
    administration on the other side. They belong here: everything they touch is
    the chain, the database, and their own bookkeeping.

    `DoSyncHub` keeps `write_checkpoint`, `maybe_archive` and
    `start_checkpoint_scheduler` as delegating methods, because `server.py` and
    the audit tests call them on the hub.
    """

    def __init__(self, db, audit_log):
        self._db = db
        self._audit = audit_log
        self._last_checkpoint_at: float | None = None
        self._last_checkpoint_path: str | None = None
        self._checkpoint_export_state: str = "unknown"
        self._last_checkpoint_export_at: float | None = None

    async def start_checkpoint_scheduler(self, interval: float = None,
                                         directory: str = None) -> None:
        """Emit signed audit checkpoints on a schedule, by default.

        The protocol's tamper-evidence has a limit that only a checkpoint can
        close: an adversary with write access to the whole database can rewrite
        the chain, recompute every hash, and update the head record to match.
        Nothing stored on this machine can detect that. An exported checkpoint
        can, because the attacker never had it.

        Leaving that to a routine each operator invents means most deployments
        will not have it — a guarantee that requires opt-in is a guarantee most
        installations lack. So the hub GENERATES checkpoints itself, on a default
        interval, the same way it already applies default risk parameters like
        DOSYNC_UNREACHABLE_TTL (1800s) and DOSYNC_INTENT_TIMEOUT.

        What the hub CANNOT do is export them, and that distinction is the whole
        reason the deployment still has work to do. A checkpoint sitting in this
        directory protects nothing against anyone who controls this machine; it
        becomes evidence only once a copy exists somewhere the hub cannot reach.
        The hub does the part it can and says plainly which part it cannot.

        Args:
            interval:  seconds between checkpoints. Defaults to
                       DOSYNC_CHECKPOINT_INTERVAL (86400 = daily). Set to 0 to
                       disable — a deliberate choice, not a default.
            directory: where to write them. Defaults to DOSYNC_CHECKPOINT_DIR
                       ("checkpoints").
        """
        if interval is None:
            interval = float(os.environ.get("DOSYNC_CHECKPOINT_INTERVAL", "86400"))
        if interval <= 0:
            log.warning("Audit checkpoints DISABLED (DOSYNC_CHECKPOINT_INTERVAL=%s). "
                        "A rewritten history will not be detectable.", interval)
            return
        if directory is None:
            from .paths import resolve_state
            directory = str(resolve_state("checkpoints", "DOSYNC_CHECKPOINT_DIR",
                                          create=True))

        _export = os.environ.get("DOSYNC_CHECKPOINT_EXPORT_DIR")
        log.info("Audit checkpoints scheduled every %.0fs → %s", interval, directory)
        _external = os.environ.get("DOSYNC_CHECKPOINT_EXPORT_EXTERNAL", "").lower() in (
            "1", "true", "yes")
        if _export:
            log.info("Audit checkpoints will be exported to %s", _export)
        elif _external:
            log.info("Audit checkpoints are collected externally "
                     "(DOSYNC_CHECKPOINT_EXPORT_EXTERNAL) — this hub keeps no copy "
                     "elsewhere and holds no credentials to the collector.")
        elif _assurance_is_regulated():
            # Said at STARTUP, not only when the first checkpoint is written a
            # day later: an operator who is going to configure this should learn
            # it now, not after a day of producing artifacts nobody collects.
            # Only for deployments that declared they must prove things — see
            # _assurance_is_regulated for why this is not everyone's problem.
            log.warning(
                "DOSYNC_ASSURANCE=regulated but no checkpoint export is configured. "
                "Checkpoints will stay on this host, where they prove nothing against "
                "anyone who controls it. Set DOSYNC_CHECKPOINT_EXPORT_DIR to push copies "
                "somewhere, or DOSYNC_CHECKPOINT_EXPORT_EXTERNAL=true if something else "
                "collects them from here.")

        # Write first if one is overdue, THEN settle into the interval.
        #
        # Sleeping first looks harmless and is not: a hub that restarts more
        # often than the interval never reaches the first write, so it produces
        # no evidence at all. Found by looking at why a pull of `cp-*.json`
        # came back empty after a day of work — every restart had reset a
        # 24-hour timer that never elapsed. Deployments restart for updates,
        # power and configuration changes; an interval that only survives
        # uninterrupted uptime is not an interval.
        last = self._db.get_last_checkpoint_at()
        if last is None or (time.time() - last) >= interval:
            self.write_checkpoint(directory)

        # Archiving rides the same timer. Both are maintenance the deployment
        # should not have to remember, and a hub that checkpoints faithfully
        # while its chain grows without bound has solved the smaller problem.
        self.maybe_archive()

        while True:
            try:
                await asyncio.sleep(interval)
                self.maybe_archive()
                self.write_checkpoint(directory)
            except asyncio.CancelledError:
                log.info("Audit checkpoint scheduler stopped")
                break
            except Exception as e:
                log.warning("Audit checkpoint cycle error: %s", e)

    def write_checkpoint(self, directory: str = None) -> str | None:
        """Write one signed checkpoint. Returns its path, or None if the chain
        is empty. Used by the scheduler and available for a manual call."""
        from . import audit_backup, cert_signing

        if directory is None:
            from .paths import resolve_state
            directory = str(resolve_state("checkpoints", "DOSYNC_CHECKPOINT_DIR",
                                          create=True))

        # The mark must be current before attesting to it.
        self._audit.flush_head()
        entries = self._audit.entries()
        if not entries:
            return None

        doc = audit_backup.build_checkpoint(entries, self._db.get_audit_anchor())
        signed = cert_signing.sign_report(doc)

        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, audit_backup.checkpoint_filename())
        if os.path.exists(path):
            return None          # same second; the previous one already attests
        with open(path, "w") as f:
            json.dump(signed, f, indent=2, sort_keys=True)

        self._last_checkpoint_at = time.time()
        self._last_checkpoint_path = path
        try:
            self._db.set_last_checkpoint_at(self._last_checkpoint_at)
        except Exception as e:      # pragma: no cover - defensive
            log.warning("Could not persist the checkpoint timestamp: %s", e)
        log.info("Audit checkpoint written: %s (%d entries, head %s…)",
                 path, doc["entry_count"], doc["head_hash"][:16])

        self._export_checkpoint(path)
        return path

    def _export_checkpoint(self, path: str) -> None:
        """Copy a checkpoint to `DOSYNC_CHECKPOINT_EXPORT_DIR`, if configured.

        A checkpoint that never leaves this host proves nothing against anyone
        who controls this host — so "where does it go" is not an afterthought,
        it is the step that turns the artifact into evidence. The DESTINATION is
        deployment-specific (a mounted share, a synced folder, removable media),
        but the CONFIGURATION POINT is part of the protocol.

        **Whether any of this matters depends on who you are proving things to.**
        In a home or a small shop the operator IS the only interested party;
        there is no third party to convince, and the chain is a useful log rather
        than evidence. Warning such a deployment about an adversary who controls
        the host — which is to say, about themselves — teaches them that DoSync's
        warnings are noise. So the nagging is gated on `DOSYNC_ASSURANCE`, which
        defaults to `standard` and says nothing. A deployment that must satisfy
        an auditor sets `regulated` and gets told, loudly, when its evidence is
        not leaving the building.

        Honest about how much an export buys, because the gradation matters:

          * **Not configured** — no protection against a compromised host.
          * **A directory this hub can write to** (typically a network mount) —
            better: the copy survives destruction of the local database, and a
            remote filesystem that keeps snapshots or versions may hold history
            the hub cannot reach. But an attacker with root here can generally
            delete there too.
          * **Pull-based transfer**, where the remote side fetches and the hub
            holds no credentials to it — the strongest arrangement, and the only
            one where "the hub cannot reach it" is literally true. The protocol
            cannot implement this side of it; it is your infrastructure.
        """
        # A PULL arrangement — something outside fetching checkpoints from here —
        # is the STRONGEST option in the table above, and in it this variable is
        # correctly unset: pointing it at a mount the hub can write to would be a
        # downgrade. So "unset" alone cannot mean "misconfigured", or the warning
        # fires loudest at the best setup. The operator declares which it is.
        if os.environ.get("DOSYNC_CHECKPOINT_EXPORT_EXTERNAL", "").lower() in (
                "1", "true", "yes"):
            self._checkpoint_export_state = "external"
            log.info("Audit checkpoint retained for external collection "
                     "(DOSYNC_CHECKPOINT_EXPORT_EXTERNAL): %s", path)
            return

        target = os.environ.get("DOSYNC_CHECKPOINT_EXPORT_DIR")
        if not target:
            self._checkpoint_export_state = "not_configured"
            if _assurance_is_regulated():
                log.warning(
                    "Audit checkpoint NOT exported: neither DOSYNC_CHECKPOINT_EXPORT_DIR "
                    "nor DOSYNC_CHECKPOINT_EXPORT_EXTERNAL is set, and this deployment "
                    "declares DOSYNC_ASSURANCE=regulated. A checkpoint kept only on this "
                    "host does not detect a rewritten history.")
            else:
                log.debug("Audit checkpoint kept locally (%s); no export configured.", path)
            return
        try:
            import shutil
            os.makedirs(target, exist_ok=True)
            shutil.copy2(path, os.path.join(target, os.path.basename(path)))
            self._checkpoint_export_state = "ok"
            self._last_checkpoint_export_at = time.time()
            log.info("Audit checkpoint exported to %s", target)
        except Exception as e:
            self._checkpoint_export_state = "failed"
            log.error("Audit checkpoint export to %s FAILED: %s — the checkpoint "
                      "exists locally but is not yet evidence.", target, e)

    def maybe_archive(self, keep: int = None, directory: str = None) -> str | None:
        """Archive the oldest chain entries if the live chain has grown too big.

        The hub does this ITSELF, while running, and that is what makes it safe.
        `manage.py db audit-archive` requires the hub stopped because it is a
        SECOND process contending for a single-writer database — a constraint of
        that arrangement, not of archiving. In-process there is no second writer,
        so the operation that needed a maintenance window becomes routine.

        It needs to be automatic because it is not optional. The reference
        deployment went from 2,000 live entries to 16,258 in five days: an
        unbounded chain grows memory, slows every restart, and lengthens every
        verification, forever. Leaving that to an operator's memory is the same
        mistake as leaving checkpoints to it — and worse for a home or a small
        shop, where nobody is watching entry counts and the failure arrives
        months later as "why is this slow now".

        Refuses on a chain that does not verify: archiving corruption would seal
        it into a segment that later reads as trusted history.

        Args:
            keep:      live entries to retain. Defaults to DOSYNC_AUDIT_MAX_LIVE
                       (10000); 0 disables archiving entirely.
            directory: where segments go. Defaults to DOSYNC_ARCHIVE_DIR
                       ("audit-segments").
        """
        import hashlib as _hashlib

        from . import audit_backup as ab

        if keep is None:
            keep = int(os.environ.get("DOSYNC_AUDIT_MAX_LIVE", "10000"))
        if keep <= 0:
            return None
        if directory is None:
            from .paths import resolve_state
            directory = str(resolve_state("audit-segments", "DOSYNC_ARCHIVE_DIR", create=True))

        entries = self._audit.entries()
        if len(entries) <= keep:
            return None

        anchor = self._db.get_audit_anchor() or {}
        start_anchor = anchor.get("anchor_prev_hash", ab.GENESIS)
        generation = anchor.get("generations", 0) + 1

        if not ab.verify_entries(entries, start_anchor):
            log.error("Automatic archiving REFUSED: the live chain does not verify from "
                      "its anchor. Archiving now would seal the corruption into a segment "
                      "that later reads as trusted history. Investigate before continuing.")
            return None

        cut = len(entries) - keep
        archived, remaining = entries[:cut], entries[cut:]

        os.makedirs(directory, exist_ok=True)
        out = os.path.join(directory,
                           f"audit_segment_g{generation}_{int(time.time())}.json")
        manifest = ab.write_segment(archived, out, start_anchor, generation)
        seg_sha = ab.file_sha256(out)

        # The archival is itself a chain event, computed exactly as AuditLog
        # appends do, so the live chain stays verifiable across the seam.
        arch_entry = {
            "type": "audit_archived",
            "generation": generation,
            "archived_count": len(archived),
            "segment_first_hash": manifest["first_hash"],
            "segment_last_hash": manifest["last_hash"],
            "segment_file": os.path.basename(out),
            "segment_sha256": seg_sha,
            "automatic": True,
            "seq": self._audit._next_seq,
            "prev_hash": remaining[-1]["hash"],
            "timestamp": time.time(),
        }
        arch_entry["hash"] = _hashlib.sha256(
            json.dumps(arch_entry, sort_keys=True).encode()).hexdigest()

        self._db.replace_audit_after_archive(archived, arch_entry)
        self._db.set_audit_anchor({
            "anchor_prev_hash": manifest["last_hash"],
            "generations": generation,
            "archived_total": anchor.get("archived_total", 0) + len(archived),
            "last_archive_file": out,
            "last_archive_sha256": seg_sha,
            "archived_at": time.time(),
        })

        # In-memory state must follow the database, or the next append chains
        # from an entry that is no longer there.
        self._audit._entries = remaining + [arch_entry]
        self._audit.anchor_prev_hash = manifest["last_hash"]
        self._audit._prev_hash = arch_entry["hash"]
        self._audit._next_seq = arch_entry["seq"] + 1
        self._audit.flush_head()

        log.info("Audit chain archived automatically: %d entries → %s "
                 "(generation %d, %d kept live)",
                 len(archived), out, generation, len(remaining))
        return out
