# Changelog

All notable changes to DoSync are recorded here. The protocol version and the
hub version move independently: `protocol/0.4` is the wire contract, `0.4.x` is
this implementation of it.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] — 2026-08-01

A large release. Two of the five properties this project advertises were audited
against the code and found to be **false as stated**; both are now true and
tested. Alongside that, the work needed to make DoSync usable by someone who is
not a developer.

### Security

- **`POST /v1/device/action` bypassed the policy engine and the audit chain.**
  A device could be actuated with no chain entry, and a deployment policy
  forbidding it could be sidestepped by calling here instead of firing an intent.
  The MCP device-control tool used this path, so the bypass belonged to the AI
  rather than to an operator. Direct actions are now evaluated under the reserved
  `direct_control` intent class and always audited.
- **The audit chain did not detect truncation or wholesale rewriting.** Entries
  now carry a monotonic `seq`; a head high-water mark is kept in a separate
  table; and `db audit-checkpoint` emits an Ed25519-signed statement of the chain
  head to be stored off the hub — the only layer that detects a history rewritten
  by someone with full database access. `docs/AUDIT-THREAT-MODEL.md` states what
  each layer does and does not catch, including the rows that read "not
  detected".
- **`POST /v1/heartbeat/signed`** — liveness for hardware that cannot do TLS,
  authenticated by HMAC over the device's provisioning token. **Disabled by
  default.** Provides message authenticity and replay resistance; provides NO
  confidentiality — `device_id`, timestamp and report travel readable. Devices
  using it are marked `report_channel: signed_plaintext`. See spec §7.10 and the
  threat model before enabling it.
- **Third-party adapters via entry points** (`dosync.adapters`). Such an adapter
  runs inside the hub with the hub's permissions: loading one is logged at
  WARNING, recorded in the audit chain, and reported as `kind: third_party`
  regardless of what the plugin claims about itself. DoSync does not and will not
  download adapter code from a remote source (DESIGN-PRINCIPLES).
- Access is manageable without a shell: `GET/POST /v1/auth/mode` and
  `POST /v1/auth/token`, plus controls in the dashboard. Choose a password, or
  turn authentication off. `DOSYNC_AUTH` in the environment still wins, and the
  hub says so rather than silently ignoring the request. Every change is audited.

### Devices

- **Scan and adopt.** `GET /v1/discovery/scan` lists candidates on every
  searchable transport and registers nothing; `POST /v1/discovery/adopt`
  registers one under a name the operator chose. Scanning searches WiFi and
  Bluetooth out of the box.
- **Declarative adapters** — describe a device in YAML or JSON instead of writing
  code. HTTP and MQTT. Six worked examples ship in `examples/declarative/`,
  including a 3D printer and an industrial conveyor. A file that disappears
  QUARANTINES its device rather than deleting it: it leaves intent resolution,
  stays in the inventory, and removal remains an operator's act.
- `PATCH /v1/devices/{id}` renames a device without re-registering its manifest.
- `GET /v1/adapters` reports which technologies a hub speaks and on what basis
  each ships — `ecosystem` (an open standard), `reference` (one vendor's product,
  a worked example and not an endorsement), `infrastructure`, or `third_party`.

### Correctness

- **Two same-rank emergencies on one device are no longer silent.** Both execute
  and the later determines the final state, which is a fact about the deployment;
  it is now recorded as `concurrent_same_rank_claims` instead of being invisible.
- **Verification can accept a pushed reading** (`accept_cached_within_s`), so
  push-only sensors can verify at all. The window is measured against the
  ACTION, not the clock — a reading that predates dispatch confirms nothing.
  `VerificationResult.evidence` distinguishes `polled` from `pushed`, because
  `verified` must not mean two different things. New status
  `no_change_reported`: a change-reporting sensor that stayed silent is healthy,
  not absent.
- The hub archives its own audit chain while running (`DOSYNC_AUDIT_MAX_LIVE`),
  and emits checkpoints on a schedule (`DOSYNC_CHECKPOINT_INTERVAL`, daily).

### Interface

- The dashboard **ships with the package**. It never had: it sat at the
  repository root, so no install carried it, and the packaging move broke its
  path in clones too.
- It follows the scheme it was loaded over — it hardcoded `http://`, so on any
  TLS deployment the browser blocked it silently.
- The intent launcher renders the deployment's own intent classes instead of
  eight hardcoded home scenarios, and the version comes from the API instead of
  reading `v0.1` for three releases.
- Devices can be scanned, renamed and removed from the browser; an empty hub says
  what to do next; the certificate warning a self-signed hub produces is
  explained per platform.

### Packaging

- `bleak`, `pyyaml`, `aiohttp` and `paho-mqtt` are core dependencies. Discovery
  and declarative adapters are advertised capabilities, and a dependency needed
  to use one cannot be optional (DESIGN-PRINCIPLES).
- `pipx install dosync` is the documented path: plain `pip install` fails on
  Raspberry Pi OS, Debian 12+ and Ubuntu 23.04+ (PEP 668).

### Audit tooling — behaviour change

- **`db audit-verify` performs additional checks and can fail where it
  previously passed.** Besides the hash links it compares the chain against a
  head mark recorded separately, and against a signed checkpoint when
  `--checkpoint` is given. Anyone running this in cron or CI should expect a
  non-zero exit on a chain whose tail was removed — which is the point, but it
  is new behaviour on an existing command. A legitimate `audit-archive` does NOT
  trip it.
- `db audit-checkpoint` emits the signed head statement described above.
- `DOSYNC_AUDIT_HEAD_EVERY` (default 25) controls how often the head mark is
  persisted.

### Documentation

- `docs/CONFIGURATION.md` — all 49 settings, **generated from the source**, with
  a test that fails when it drifts.
- Spec §7.8 lists all 32 audit event types, §7.9 the complete endpoint surface,
  §7.10 signed heartbeats. `python3 -m dosync.spec_coverage --check` fails when
  the implementation grows past the specification.
- README leads with governance and accountability rather than "semantic layer",
  and answers how DoSync differs from W3C Web of Things and MCP.

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

### Also in 0.4.1

*These entries spent nine days under `[Unreleased]` after their contents had
shipped, so a reader saw published functionality marked as not released.
Closing a version means moving the heading, and it was missed at the time.*

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
