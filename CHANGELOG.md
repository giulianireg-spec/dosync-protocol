# Changelog

All notable changes to DoSync are recorded here. The protocol version and the
hub version move independently: `protocol/0.4` is the wire contract, `0.4.x` is
this implementation of it.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — behavior
- **`db audit-verify` now performs additional checks and can fail where it
  previously passed.** Besides the hash links it compares the chain against a
  head mark recorded separately, and against a signed checkpoint when
  `--checkpoint` is given. Anyone running this command in cron or CI should
  expect a non-zero exit on a chain whose tail was removed — which is the point,
  but it is new behavior on an existing command. A legitimate `audit-archive`
  does NOT trip it.

### Added
- Monotonic `seq` on every audit entry, inside the hashed content, so gaps and
  reordering break verification.
- `db audit-checkpoint` — an Ed25519-signed statement of the chain head, meant
  to be stored off the hub. It is the only layer that detects a history
  rewritten wholesale by someone with full database access.
- `docs/AUDIT-THREAT-MODEL.md` — attacker model, a verification matrix that
  includes what is NOT detected, and a compliance runbook.
- `DOSYNC_AUDIT_HEAD_EVERY` (default 25) — how often the head mark is persisted.

## [0.4.2] — 2026-07-26

### Changed
- **`bleak` is now a core dependency and the BLE adapter registers by default**
  (`DOSYNC_BLE_ENABLED=false` opts out). Discovery libraries are not like control
  libraries: you install `dosync[wiz]` because you know you own WiZ bulbs, but
  discovery is how you find out what you own — so the dependency is needed before
  the knowledge that would justify it. Left optional, a user scanned, saw nothing,
  and concluded DoSync does not support Bluetooth: a false belief produced by
  packaging. Adds ~20 MB; a hub with no radio degrades to reporting the transport
  as unsearchable.

### Added
- `GET /v1/discovery/scan` asks every adapter and reports `searched` and
  `not_searchable` — "nothing found" means something different when a transport
  was never searched.
- `POST /v1/discovery/adopt` — register one scanned candidate under a name the
  operator chose, recorded in the audit chain. Scanning itself registers nothing.
- `PATCH /v1/devices/{id}` — rename without re-registering the whole manifest.
- `discover()` / `can_discover()` as optional adapter methods; BLE implements
  discovery over Bluetooth radio, with no broadcast address involved.
- Dashboard: Scan, rename and remove controls; an empty hub says what to do next.

## [0.4.1] — 2026-07-22

First published release. `pip install dosync`.

### Fixed
- **The container lost its database on every restart.** `Dockerfile` and
  `docker-compose.yml` set `DOSYNC_DB_PATH`; the hub reads `DOSYNC_DB`. Nothing
  failed and nothing warned — the database was simply written inside the image
  instead of the mounted volume, so `docker compose down` destroyed the audit
  chain each time. The compose files now use the correct name, the hub accepts
  the old one as a deprecated alias (with a warning) so existing deployments
  keep their data, and a structural test now fails if any deployment file sets a
  `DOSYNC_*` variable no code reads.
- **The version was declared in three places that disagreed.**
  `dosync/__init__.py` said `0.1.0`, `server.py` hardcoded `0.4.0` four times,
  and `pyproject.toml` carried its own copy — so `import dosync;
  dosync.__version__` reported a number three releases stale. `__init__.py` is
  now the single source; pyproject reads it and the server imports it.
- License metadata moved to an SPDX expression (`license = "Apache-2.0"` plus
  `license-files`), removing three setuptools deprecation warnings whose builds
  stop being supported in February 2027.
- The startup log announced port 47200 no matter where the hub was listening.
  It now reports the real port and the database path — an installed
  `dosync-hub` writes to the current directory by default, which surprises
  people who run it from different places.

## [Unreleased]

### Added
- **The project is installable.** `pip install dosync` now provides the library
  and three console scripts — `dosync-hub`, `dosync-manage`, `dosync-certify`.
  Until now DoSync could only be run from a clone, which put the largest
  friction at the very first step: evaluating it required cloning a repository,
  resolving dependencies by hand and setting `PYTHONPATH`. Optional extras
  (`dosync[wiz]`, `[ha]`, `[mqtt]`, `[ble]`, `[sms]`, `[mavlink]`, `[mcp]`,
  `[all]`) keep the core install free of libraries for hardware you do not own.
- `verify_with` bindings and independent-observation verification (spec §7.5):
  an action can declare which sensor confirms its effect, producing a
  `verification` result separate from `success` — `verified`, `contradicted`,
  `unverifiable` or `unverified`.
- Device-initiated heartbeat, `POST /v1/heartbeat` (spec §7.4), for devices the
  hub cannot poll, plus cause attribution for unreachable devices.
- Conformance certification tier (52 checks) covering the 0.4 protocol features.
- Anchored audit-chain archiving: `dosync-manage db audit-archive` segments the
  chain while keeping it verifiable end to end.
- Formal claim state machine for concurrent intents (spec §3.1) with invariants
  bound to the tests that would catch their violation.

### Changed
- The hub application, operator CLI and certification suite moved into the
  package (`dosync/server.py`, `dosync/manage.py`, `dosync/certify.py`) so they
  ship with an install. The repository-root `server.py`, `manage.py` and
  `certify.py` remain as aliases, so `uvicorn server:app`, existing systemd
  units, and `python3 manage.py ...` keep working unchanged.
- The container image now installs the built wheel instead of copying loose
  scripts: the image runs exactly what a user's `pip install` produces.
- Retired every deprecated `asyncio.get_event_loop()` call.

### Fixed
- **A security alert that had never fired.** `register_device` raises an
  `alert_anomaly` intent when a device's capabilities change without a firmware
  version bump ("may indicate compromise"). It called `execute_intent` without
  its required `executor` argument, so every invocation raised `TypeError` —
  swallowed whole by a bare `except Exception: pass`. The anomaly was always
  written to the audit chain, so the evidence existed; the alert itself was dead
  for as long as the code had existed. Hub-initiated intents now run through the
  same executor, arbitration and auditing as any other.
- A stray `@dataclass` on the `VerificationStatus` enum made every verification
  status compare equal to every other (`contradicted == verified` was `True`)
  and left the type unhashable.
- `pytest.ini` had no `asyncio_mode`, so coroutine tests were reported as passed
  without being executed.
