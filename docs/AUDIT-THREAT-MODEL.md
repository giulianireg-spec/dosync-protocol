# Audit chain — threat model

*What the tamper-evidence of DoSync's audit chain does and does not prove.
Written 2026-07-25, after testing the chain against concrete attacks rather than
asserting its properties.*

This document exists because "tamper-evident" is often used as though it meant
"unforgeable", and it does not. A deployment choosing DoSync for accountability —
a care facility, a plant, a hospital — needs to know exactly which claims it can
make to a regulator and which it cannot. Overstating this would be worse than
saying nothing: it would put someone in the position of defending a guarantee
that does not hold.

---

## The claim, stated precisely

> Every action DoSync takes on a device is recorded in a SHA-256 hash chain. Any
> alteration of a recorded action is detectable. Removal of records from the end
> is detectable. Wholesale replacement of the history is detectable **only if a
> signed checkpoint was exported off the hub beforehand**.

Everything below is the reasoning behind each clause.

---

## Attacker model

| Attacker | Capability assumed |
|---|---|
| **A. Application-level** | Can call the hub's API. Cannot touch the database file. |
| **B. Partial host** | Can write to `audit_log` (a compromised process, a bad migration, a partially restored backup). |
| **C. Full host** | Can write anything on the machine: the whole database, the code, the signing key. |

Attacker C is not hypothetical: it is anyone with root on the hub, and it
includes **the operator themselves**. That case matters most, because the
purpose of an audit chain is precisely to constrain the party who owns the
system.

---

## What each layer defends

### Layer 1 — Hash links
Each entry carries the hash of the previous one, and its own hash covers its
whole content. Changing any recorded field, or inserting an entry into the
middle, breaks every link after it.

- **Stops:** A and B, for modification and insertion.
- **Does not stop:** truncation. Dropping the last entry leaves every surviving
  link intact, so the chain still verifies. This is a property of hash chains,
  not a defect in this one.

### Layer 2 — Sequence numbers and the head record
Each entry carries a monotonic `seq`, inside the hashed content. The latest
`(seq, hash)` is written to `audit_meta` — a **different table** — as entries are
appended. Verification compares the chain's actual head against that record.

- **Stops:** B, for truncation. Deleting rows from `audit_log` contradicts a
  record the deletion did not touch.
- **Does not stop:** C. An attacker who can write to both tables keeps them
  consistent. This layer reliably catches accidental deletion, a truncated
  restore, buggy code, and a compromise that reaches the log but not the
  metadata — a real and common class, but not a determined adversary.

### Layer 3 — Signed checkpoints, exported
`dosync-manage db audit-checkpoint` emits a signed statement: *at this moment
the chain held N entries ending in hash H*. Its value comes entirely from
**leaving the machine**. Later, `audit-verify --checkpoint FILE` proves the chain
still contains that exact history.

- **Stops:** C, for any rewrite of history recorded before the checkpoint. A
  forged chain cannot contain a head it never produced, and the signature cannot
  be recomputed without the key.
- **Does not stop:**
  - Rewriting events that happened **after** the last checkpoint. Checkpoint
    frequency is the size of the window an attacker can still edit.
  - An attacker holding the **signing key**, who can mint checkpoints for a
    forged chain. For high-assurance deployments the key belongs off the hub.
  - A checkpoint stored **on the hub**, which protects nothing.

---

## Verified, not asserted

Each row corresponds to a test in `tests/test_audit_chain_integrity.py`,
including the two that assert a failure to detect — because a limit that is
documented but unproven is exactly the kind of claim this project avoids.

| Attack | Layer 1 | + Layer 2 | + Layer 3 (exported) |
|---|---|---|---|
| Alter a recorded entry | **detected** | detected | detected |
| Insert into the middle | **detected** | detected | detected |
| Remove entries from the end | not detected | **detected** | detected |
| Rewrite everything, fix the head | not detected | not detected | **detected** |
| Rewrite everything, hold the key | not detected | not detected | not detected |
| Edit or remove events since the last checkpoint | not detected | partially¹ | not detected |
| Edit events after the last checkpoint | not detected | not detected | not detected |


¹ The head mark is written in batches (`DOSYNC_AUDIT_HEAD_EVERY`, default 25
entries) and flushed at shutdown, so removals below the last mark are caught
while the most recent entries are not. **Checkpoint frequency is the size of the
window an attacker can still edit** — this is the single most important
operational lever in this document.

---

## Runbook — audit evidence for a regulated deployment

### What is the protocol, and what is yours

This distinction matters more here than anywhere else in the document, because
the guarantee is split across the two.

**The protocol provides the mechanism.** A conforming implementation emits a
signed checkpoint of the chain head and verifies a chain against one. The
document format, the signature, and the semantics of the verification are part
of DoSync and are the same everywhere. If your hub cannot do this, it is not
conforming.

**The deployment provides everything else, and none of it is standardised:**

| Decision | Who decides | Why the protocol stays out of it |
|---|---|---|
| How often to checkpoint | You, but a default exists | The hub generates them **daily by default** (`DOSYNC_CHECKPOINT_INTERVAL`, 86400s). Frequency IS the window an attacker can still edit, so shorten it if your devices warrant it — but the default is not "none", because a guarantee requiring opt-in is one most installations lack |
| Where to store checkpoints | You, through a standard setting | `DOSYNC_CHECKPOINT_EXPORT_DIR` is the configuration point and the hub copies each checkpoint there. It has no default — no destination is universally right — but leaving it unset is **not silent**: the hub warns at startup and on every checkpoint, and `/v1/status` reports `checkpoint_export: not_configured` |
| How to automate it | You | systemd, cron, a Kubernetes CronJob, or a person with a calendar reminder are all valid |
| How long to keep them | You | Retention is a compliance question your regulator answers, not the protocol |

So: **the protocol can be certified; your checkpoint routine cannot.** No
conformance test can tell whether you actually export the artifact, and any
implementation claiming otherwise is overreaching. What the protocol guarantees
is that IF you export checkpoints, a rewritten history becomes detectable. The
"if" is yours.

The units below are **one worked example** — the reference deployment runs a
Raspberry Pi with systemd. Adapt freely; only two things are load-bearing:
**the filename must be unique per run**, and **the artifact must leave the
machine**.

### Daily, automated

**Generation is already automatic.** The hub writes a signed checkpoint every
`DOSYNC_CHECKPOINT_INTERVAL` seconds (default 86400) into
`DOSYNC_CHECKPOINT_DIR` (default `checkpoints/`), with a filename unique per
run. You do not need a timer for that part, and `/v1/status` reports
`checkpoint_age_s` so monitoring can catch a scheduler that has stopped.

**Export has a standard setting, but no default destination.** Point
`DOSYNC_CHECKPOINT_EXPORT_DIR` at a location and the hub copies every checkpoint
there. Leave it unset and the hub says so, repeatedly — a hub quietly producing
artifacts nobody collects is the failure this whole layer exists to prevent.

How much that buys depends on where you point it, and the difference is worth
being precise about:

| Destination | What it protects against |
|---|---|
| Unset | Nothing beyond local corruption |
| A directory this hub can write to (usually a network mount) | Loss of the local database; a remote filesystem keeping snapshots may hold history the hub cannot reach — but root here can usually delete there too |
| Pull-based transfer, where the far side fetches and the hub holds no credentials to it | A host-level adversary. Only here is "the hub cannot reach it" literally true |

The units below are **one worked example** of the third arrangement.

```ini
# /etc/systemd/system/dosync-checkpoint.service
[Unit]
Description=Export a signed DoSync audit checkpoint
After=dosync.service

[Service]
Type=oneshot
User=dosync
WorkingDirectory=/var/lib/dosync
# The filename MUST be unique per run. systemd has no date specifier, so the
# timestamp comes from a shell, and `%%` escapes the percent signs systemd would
# otherwise consume.
#
# This originally read `cp-%i.json`. `%i` is systemd's INSTANCE NAME, valid only
# in template units; in a plain unit it expands to nothing, so every run would
# have written the same file and destroyed the previous day's evidence without a
# word. A compliance runbook that quietly overwrites its own evidence is worse
# than no runbook: it produces confidence without the artifact.
ExecStart=/bin/sh -c '/usr/local/bin/dosync-manage db audit-checkpoint \
          --out /var/lib/dosync/checkpoints/cp-$(date -u +%%Y%%m%%dT%%H%%M%%SZ).json'
# THE POINT OF THE WHOLE EXERCISE: get it off this machine. A checkpoint that
# stays on the hub proves nothing against anyone who controls the hub. Replace
# this with whatever destination your environment allows — an object store, a
# backup host, a mailbox — as long as the hub cannot write to it afterwards.
ExecStartPost=/usr/bin/rsync -a /var/lib/dosync/checkpoints/ \
              backup-host:/audit/dosync/
```

```ini
# /etc/systemd/system/dosync-checkpoint.timer
[Unit]
Description=Daily DoSync audit checkpoint

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now dosync-checkpoint.timer
```

The command refuses to overwrite an existing checkpoint (`--force` only if you
are certain one is expendable). **Older checkpoints are worth more than newer
ones** — each covers a longer stretch of history. Keep them all; they are under
a kilobyte each.

### Weekly, reviewed by a human

```bash
dosync-manage db audit-verify --checkpoint /path/to/latest/checkpoint.json
echo "exit $?"      # 0 = the chain still contains the attested history
```

Exit code 0 is the whole report. A legitimate archive does not change it; a
truncation or a rewrite does. Wire it into whatever alerts your team already
reads — it is designed to be silent until it matters.

### On any incident, or when asked by an auditor

```bash
# 1. Does the live chain verify, and does it contain the attested history?
dosync-manage db audit-verify --checkpoint <oldest checkpoint you still hold>

# 2. Do the archived segments verify standalone?
dosync-manage db audit-verify --segment /var/lib/dosync/segments/seg-g1.json

# 3. What happened in the window under review?
curl -s "$HUB/v1/audit?limit=500" | jq '.entries[] | select(.timestamp > START)'
```

### What to hand over

| Artifact | Why it matters |
|---|---|
| The oldest checkpoint still held, plus the newest | Bounds the period whose integrity is provable |
| Output of `audit-verify --checkpoint`, exit code included | The verification itself, reproducible by the auditor |
| Archived segments for the period | The history no longer in the live database |
| This document | States plainly what the evidence does and does not prove |

An auditor can re-run every one of these without access to the hub, using only
the files. That is the property worth having: the evidence does not depend on
trusting the machine that produced it.

---

## What this means for a deployment

**You can tell a regulator:** every action the system took is recorded; no record
can be altered or removed without detection, given a checkpoint routine.

**You cannot tell a regulator:** that the operator is unable to forge history.
Nobody running their own hub can claim that, with any software. What you can
show is that forging it requires defeating an exported, signed artifact that the
hub does not control — and that the attempt leaves the checkpoint and the chain
in visible contradiction.

**Operational guidance**

- Export a checkpoint on a schedule (daily is a reasonable default) to a
  destination the hub cannot write to.
- Keep at least the most recent checkpoint outside the hub. Older ones narrow
  the editable window further.
- For high-assurance use, keep the signing key off the hub.
- Run `audit-verify --checkpoint` as part of any incident review, not only when
  something is already suspected.

---

## What would strengthen this further

Recorded honestly as not-yet-done rather than implied:

- **Third-party notarization.** Publishing checkpoint hashes to a service the
  operator does not control removes the "operator forges their own history"
  case entirely. Currently the strongest remaining gap.
- **Per-entry signing.** Signing each entry rather than periodic heads shrinks
  the editable window to zero, at a cost in write throughput on modest hardware
  like a Raspberry Pi.
- **Hardware-backed keys.** A TPM or secure element makes key extraction
  materially harder than reading a file.
