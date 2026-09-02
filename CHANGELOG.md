# Changelog

All notable changes to DoSync are recorded here. The protocol version and the
hub version move independently: `protocol/0.4` is the wire contract, `0.4.x` is
this implementation of it.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **The audit log lives in its own module.** `hub.py` held five
  responsibilities in 3,710 lines — the capability registry, four resolvers, the
  audit log, a timed executor and the device-health monitor — and none could be
  touched without risk to the other four. The resolver redesign that the Phase 1
  measurements call for is not safe until those are separated, so this is the
  first of several moves.

  `AuditLog` is now `dosync/audit.py`, re-exported from `dosync.hub` so that
  every existing import keeps working. Nothing about the behaviour changed:
  the chaining, the persistence callback, the head checkpoint and the
  verification are the code that shipped in 0.6.3.

  One detail worth naming because it nearly slipped: the extracted module logs
  to `dosync.hub`, not `dosync.audit`. An operator filtering on the old name
  would otherwise have stopped seeing audit warnings the day the file moved —
  a behaviour change hiding inside a move that promised none.


### Added
- **The MCP server can be reached over the network, not only as a subprocess.**
  `stdio` assumes the client can start the server itself, which means they share
  a machine and a filesystem. That held while the only deployment was one
  person's laptop. It fails for every real one: a hub in a container, on a Pi
  across the room, or under a supervisor is not where the agent is.

  `DOSYNC_MCP_TRANSPORT=http` serves the same tools from the same server object
  over Streamable HTTP — a transport the pinned SDK already ships, so nothing
  about the protocol changed. An agent reaching a device this way goes through
  the hub's REST API exactly as `curl` does, and a test asserts the MCP server
  never imports the hub or the executor directly: policies and the audit chain
  apply to an agent as they do to any other caller.

  **It refuses to start over HTTP without `DOSYNC_TOKEN`.** Over stdio the
  operating system is the boundary — whoever can start the process is already on
  the machine. A port has none, and on a host network it is reachable by every
  device on the LAN. The token is compared with `hmac.compare_digest`: `!=` on
  strings stops at the first differing byte, which leaks the matching prefix
  length through response time and is enough, on a LAN with repeated
  measurements, to reconstruct a token. Idle sessions are released after 15
  minutes, because a client that disconnects without closing otherwise leaves
  its session in memory for as long as the process runs.

  Both defaults say that opening a port is a decision rather than a consequence
  of installing: the transport is `stdio` and the host is loopback unless asked
  otherwise.

### Fixed
- **The same server introduced itself differently over each transport.** The
  Streamable HTTP session manager builds its initialization options from the
  `Server` object and never sees what a transport constructs, so the version was
  reported as DoSync's over stdio and as the MCP SDK's own package version over
  HTTP. Declared on the server itself, where both read it.

- **`/mcp` answered with a 307 redirect to `/mcp/`.** An HTTP client following a
  redirect is not required to resend the `Authorization` header or the POST
  body, so a correctly configured client would have arrived unauthenticated at a
  URL it never chose. Both spellings now reach the same handler.


### Fixed
- **Forty-eight more lines of Spanish in the core, and the third rewrite of the
  guard that kept missing them.** Found while checking what the Home Assistant
  bridge looks like to a first-time reader: its module docstring opened with
  *"Conecta DoSync con Home Assistant via su API REST local"* — unmistakably
  Spanish, and invisible to a check that had already been strengthened twice.

  Each previous fix added entries to a list. Accents missed unaccented prose; a
  content-word list then missed a sentence whose only Spanish is `con` and `su`.
  A list will always have holes. The guard now matches **function words** —
  `el`, `la`, `del`, `con`, `su`, `que` — which are the part of a language a
  writer cannot avoid, two on a line, whole words only. It also rejects Spanish
  docstring section headers (`Uso:`, `Requiere:`, `Ejemplo:`), which is where a
  half-translated file leaves its clearest trace.

  It found forty-eight lines across twelve modules, including the security PKI
  header, the auth key-handling notes and three lines in the Home Assistant
  bridge itself.


## [0.6.3] — 2026-08-29

Everything here came from connecting an agent to a hub for the first time, on a
machine that had never run either.

The MCP server could not start at all against the SDK a fresh install receives.
The README had said "MCP" a dozen times without ever explaining how to connect
one. And the language guard, which matches accented characters, had let an
unaccented Spanish warning reach an operator — the second time that happened,
because the first was fixed by hand without strengthening the guard.


### Fixed
- **The MCP server could not start against the SDK a fresh install gets.** The
  2.x SDK removed `Server.list_tools()`, which `dosync/mcp_server.py` decorates
  at import time, so `python -m dosync.mcp_server` died with
  `AttributeError: 'Server' object has no attribute 'list_tools'`. Found while
  wiring an agent to a hub on a clean Windows install, which installed 2.1.1
  because the project declared `mcp>=1.0.0` — a floor with no ceiling, correct
  the day it was written and wrong from the moment 2.0 shipped. The same shape
  as the plain `uvicorn` pin that left the hub with no WebSocket library.

  Capped at `<2.0`, and the server now checks before decorating: a cap does
  nothing for an operator who already has 2.x installed, and an AttributeError
  from inside a module they never opened is a true statement about the wrong
  thing. Porting to the 2.x SDK is work to schedule, not a configuration
  problem, and saying so is more useful than a stack trace.


## [0.6.2] — 2026-08-29

One fix, found running the first semantic intent from a clean Windows install:
JSON responses did not declare their charset, so PowerShell 5.1 fell back to
Latin-1 and mangled every non-ASCII byte the hub sent. The tool this project's
own Windows instructions recommend was the one showing broken text.


### Fixed
- **JSON responses did not declare their charset, and Windows clients mangled
  every non-ASCII byte.** An operator running the first intent from a clean
  Windows install read `read-only status query â device has no sensors to read`
  where an em dash belonged.

  JSON is UTF-8 by definition, so FastAPI omits the charset — correct, and
  useless for Windows PowerShell 5.1, which falls back to Latin-1 when a
  response does not declare one. The README documents `Invoke-RestMethod` as
  the way to call this API from Windows, so the tool this project recommends
  was the one showing broken text, and every accented character in a device
  name would arrive the same way.

  Being right about the specification while a reader gets mojibake is a kind of
  correctness this project has already decided is not worth much.


## [0.6.1] — 2026-08-29

Two fixes, both found by installing 0.6.0 on a machine that had never run it —
the test that should have preceded the release rather than followed it.

SSDP discovery raised `NotImplementedError` on Windows and took the whole scan
down with a 500, because the handler written to absorb a failing discoverer
called a logger that module does not define. And an action that failed came
back with an empty reason, found while turning real bulbs green from that same
clean install.


### Fixed
- **An action that failed reported `"error": ""`.** Found controlling real
  bulbs from a clean Windows install: two of three returned
  `success: false` with an empty reason, and the log line ended at the colon.
  `pywizlight` raises exceptions whose `str()` is empty — a bulb powered off at
  the wall produces one — and six adapters did `error=str(e)`, so the hub
  reported that an action had failed and said nothing else.

  A shared `failure_reason()` now names the exception type when it carries no
  text. A type is not a diagnosis, but a blank field is strictly worse: the
  type at least says where to look. Applied across all six adapters rather than
  patched in one, with a test that parses each adapter and rejects a bare
  exception in the error field — the same class of failure as the verification
  panel swallowing `URLError`, three days apart.


### Fixed
- **SSDP discovery raised `NotImplementedError` on Windows, and the handler
  that should have absorbed it raised `NameError`.** Both found on a clean
  install of 0.6.0, minutes after publishing it.

  The first is the same bug twice. `sock_recvfrom` worked on stock asyncio and
  failed on uvloop, which `uvicorn[standard]` installs; the fix for that,
  `add_reader`, worked on both and failed on Windows, whose ProactorEventLoop
  has no reader registration for sockets. Each fix worked on the loop it was
  written against. SSDP now uses `create_datagram_endpoint`, which is defined
  on `BaseEventLoop` — inherited by all three — rather than on the abstract
  base where the other two are stubs that raise.

- **The scan called a logger that `server.py` does not define.** Three
  `log.warning(...)`/`log.info(...)` calls inside `except` blocks, in a module
  where every other line uses `logging.getLogger("dosync.server")`. A
  discoverer failing therefore raised `NameError` *from the error handler*,
  turning a failure the code was written to absorb into a 500 that took the
  whole scan down.

  Two were added the day before; the third had been latent for longer and was
  never reached because no discoverer had failed in production until Windows.
  A test now rejects any bare `log.` in that endpoint — it found the third one.


## [0.6.0] — 2026-08-27

Seventeen commits, and almost all of them came from installing the project on a
machine that had never run it and using it as a stranger would. The protocol
needed no change; what needed changing was what the system said about itself.

Three of these were the same failure in different places — the hub asserting an
outcome it had never measured. A scan reporting a transport as searched when
uvloop had made it fail instantly; a drafted adapter declared unreachable over
MQTT after three HTTP requests to an empty string; a verification panel that
silently swallowed the reason a request failed. The project has a rule about
not claiming what was not measured, and the interesting part is that it kept
being broken by the code written to enforce it.


### Security
- **A live Home Assistant token shipped in `dosync.service` for three months.**
  Issued May 2026 and valid until 2036, in a unit file at the root of a public
  repository — anyone who cloned the project received a working credential for
  one person's Home Assistant. Found only because a Windows persistence test
  happened to open the file. The token has been revoked and removed, and the
  unit now documents keeping secrets in an untracked drop-in instead.

  `.service` files were already scanned by the operator-data guard — that
  extension was added deliberately after units carrying `/home/<user>` slipped
  through. But the checks looked for addresses, paths, room names and device
  identifiers. Nobody had asked whether a file contained a *secret*, so the
  guard written for exactly this class of leak looked straight past it. It now
  detects JWTs, cloud provider keys and private key blocks, with placeholders
  exempt, and a second test rejects any non-placeholder secret in the shipped
  unit. Both were confirmed to fail against the real token before being kept.

### Added
- **How to keep the hub running, for Linux and Windows.** The project shipped a
  systemd unit at its root for months while no user document mentioned it, and
  this page referred to *"your service"* as though the reader already had one —
  the reference deployment runs under systemd only because its operator wrote
  the unit himself. It surfaced while testing Windows, which was never the
  special case: it was where the omission became visible.

  DoSync implements supervision on no platform; it delegates, to systemd or to
  the task scheduler. Both paths are documented, and the Windows one was
  verified on Windows 11 ARM64 in a virtual machine rather than written from
  memory — including the failure it makes obvious: a scheduled task does not
  inherit your environment, so without `DOSYNC_DB` the hub starts perfectly
  against a different database, reports `devices: 0`, and looks like the
  inventory was lost.


### Added
- **A manifest records who described the device and what was checked.** A
  manifest states what a device can do. When a language model drafted that
  statement from an announcement it interpreted, and most of it was never tried
  against the hardware, those are facts about the claim — not about the file
  carrying it. They lived in a YAML comment, which the parser discards on load
  and any edit removes, so the hub could not tell `cancel_job` written by a
  model and tried by nobody from the same action written by an engineer who
  tested it.

  Declarative adapters may now carry a `provenance:` block, which the loader
  keeps on the manifest; `dosync-manage draft-adapter` writes it alongside the
  human-readable header, stating the same facts twice so a reader can see if
  one of them was quietly edited. The dashboard marks such a device, and the
  marker names which actions were checked against the device and which were
  not.

  **Descriptive only.** A drafted adapter is not less authorised than a
  hand-written one — it is differently attested, and what to do about that is
  the operator's judgement. A test asserts that no module branches on
  provenance: a hub that refused actions because a model wrote their
  description would be deciding on evidence it cannot evaluate, and the step
  after that is trusting some models more than others. Absence stays the honest
  default: a file that claims nothing about itself has nothing asserted about
  it.


### Fixed
- **The reason a request failed vanished from the verification panel.**
  `URLError: <urlopen error [Errno 111] Connection refused>` was rendered into
  `innerHTML` unescaped, so the browser read `<urlopen error …>` as an unknown
  element and dropped it. The operator saw `no answer — URLError:` with the
  cause silently removed — the panel built to stop a draft asserting unmeasured
  things was hiding what it had measured.

  Everything that panel renders comes from a file the operator pasted or from a
  device's own reply, and neither is ours to trust: the same path would have
  run an injected script element out of a draft just as willingly. The URL,
  method, status, action, reason and transport are all escaped now, and a
  failure whose exception carries no text says so instead of trailing off after
  a dash.


### Fixed
- **An MQTT draft was probed over HTTP, and the hub then reported MQTT as
  unreachable without having spoken it once.** Found the first time a real
  MQTT draft went through the dashboard check. Its `publish` actions carry no
  `request`, so the method defaulted to `GET`; an MQTT transport has no
  `base_url`, so the URL was the empty string. The hub issued three requests
  to `''`, reported `ValueError: unknown url type`, and concluded that *"this
  draft assumes mqtt and the device is not speaking it"* — a claim about a
  transport it had never used.

  That is the same failure this project has already fixed in the scan, in
  simulation reporting and in the audit: asserting an outcome that was never
  measured. What a draft can be tried with now follows from its transport
  rather than from a default that only makes sense for one of them, and a
  transport is only called unreachable when something was actually tried.

  The same three actions were also listed as verifiable and untestable
  simultaneously. Every declared entry now lands in exactly one list, and a
  test asserts both that the sets do not overlap and that together they
  account for everything the draft declares.


### Added
- **A drafted adapter can be checked from the dashboard before it is saved.**
  Until now the only way to find out whether a draft matched the device was to
  install it and watch. A model given an empty announcement wrote a confident
  adapter for an unrelated protocol; given the vendor's own announcement it
  wrote an honest one — right manufacturer, `# UNVERIFIED` markers, candid
  comments about what it could not determine — whose endpoints were still
  invented. Both files read as plausible. The printer refused the connection on
  every path in both.

  The three outcomes are kept distinct, because they mean different things:
  nothing answering says the *transport* is wrong, not a route; a draft whose
  every request changes something cannot be judged either way and is not
  reported as success; and a partial pass is stated as the count it is.

  What could not be tried is shown alongside what was, with the reason. A
  `POST cancel_job` on a printer that may be printing is precisely what must
  not be run to find out whether it exists — so it stays unverified by design,
  and a verdict that hid it would read as coverage it never had.

  Describing a device now hands off to this check rather than mentioning it:
  the step existing and the operator knowing it exists at the moment they need
  it are different things.


### Added
- **`POST /v1/adapters/verify` — a drafted adapter is tried against the real
  device before anyone relies on it.** A draft cannot be judged by reading it.
  Given an empty announcement, a model wrote a confident adapter for an
  unrelated protocol; given the vendor's announcement it wrote an honest one
  whose endpoints were still invented, and marked `# UNVERIFIED`. Both files
  look correct. Four `GET`s told the difference in under a second: the printer
  returned no response at all on every path, on every port.

  `GET`, `HEAD` and `OPTIONS` only, and only the requests the draft itself
  declares — this never probes for a device's real API and never proposes a
  replacement, which would be guessing on the operator's behalf and would need
  per-vendor knowledge to do. A draft where nothing answers is reported as
  `transport_unreachable` rather than as a failed check, because "the whole
  transport is wrong" and "one route is wrong" are different problems.

### Fixed
- **Verification checked `actions` and ignored `sensors`, so a real draft was
  verified with zero requests.** Asked to prefer read-only endpoints, a model
  put its single `GET` under `sensors` — where a sensor belongs. Nothing was
  tested and the draft reached the operator with no objection raised. What
  makes a request safe to try is its method, not the section it sits in.

  Everything that cannot be tried is now returned too, with the reason: a
  `POST` that changes the device, an MQTT publish whose side effect *is* the
  send. A draft whose untested half is invisible is the problem this exists to
  solve.


### Fixed
- **Adoption discarded what the device announced about itself.** A real 3D
  printer announced `NT: urn:bambulab-com:device:3dprinter:1` over SSDP — the
  vendor naming itself, the most authoritative statement a device makes about
  its own identity. The discoverer captured it, along with the model, firmware
  and description document; adoption persisted only the address and service
  type, because `CapabilityManifest` had nowhere to put the rest.

  The consequence surfaced the first time the drafting flow was used for real:
  the description handed to a model read `"announcement": {}`, and the model
  filled the gap with the OctoPrint example shipped alongside it as a format
  reference. Every endpoint it produced returned HTTP 000 — that printer has no
  HTTP server at all. The hub had captured the vendor's name and thrown it away
  before asking anyone what the device was.

  Manifests now carry `discovery_evidence`, the announcement kept verbatim, and
  the scan result and the dashboard forward it through adoption. It is stored
  and never interpreted: a test asserts no module branches on a vendor name,
  because keeping the raw datum must not become the first step towards the
  catalogue of vendors this project decided not to be.

- **`to_dict()` dropped `adapter_config` for devices with no adapter** — which
  is exactly the device that still needs describing. The address and service
  type recorded at adoption vanished from every serialised form. The describe
  endpoint had been patched to read the object instead; the dict was still
  lying to every other caller.


### Fixed
- **SSDP discovery was completely broken in production while 1045 tests
  passed.** `uvicorn[standard]` — added days earlier to fix WebSocket support
  — installs uvloop, and uvloop *declares* `loop.sock_recvfrom` but raises
  `NotImplementedError` when it is called. `hasattr` reported the method as
  present, the test suite ran on the stock asyncio loop where it works, and on
  the reference deployment every SSDP scan failed instantly on both ports for
  days. A 3D printer announcing itself twice a minute was invisible.

  Replaced with `add_reader` plus a non-blocking `recvfrom`, which both loops
  support. Checking that an attribute exists is not checking that calling it
  works, so there is now a test that runs the real listen path on uvloop —
  the loop the hub actually runs on — and skips only where uvloop is absent.

- **And the scan reported that transport as one it had SEARCHED.**
  `asyncio.gather(return_exceptions=True)` turned two `NotImplementedError`s
  into an empty list, indistinguishable from having listened and heard
  nothing. A total failure now raises instead. A discoverer or adapter that
  raises is also recorded in `skipped` with the reason: previously it fell
  through and appeared in neither list, leaving a reader unable to tell it had
  been attempted at all.


### Documented
- **`pipx inject dosync <package>` for optional dependencies installed after
  the hub, framed as a general pattern rather than a per-vendor fix.** A
  vendor library installed with plain `pip install` after `pipx install
  dosync` lands in the wrong environment — `pipx` isolates the hub in its own
  venv, and the failure is silent, since the hub cannot distinguish "not
  installed" from "installed somewhere else". Found reinstalling on Windows.

  Framed deliberately without naming any single vendor's product as the
  worked example: this project already had to walk back a reference adapter
  that nagged every hub to install a vendor library regardless of whether the
  operator owned that hardware, and documenting the fix around one vendor
  again would reintroduce the same bias from the other direction. A test
  checks the explanation names no domestic-appliance vendor.

### Fixed
- **mDNS discovery searched for `_workstation._tcp`, which identifies a
  general-purpose computer, never a controllable device.** On a real network
  it offered a production Raspberry Pi — running its own separate DoSync hub,
  with its own audit chain and registered devices — as a discovery result to
  adopt from a completely unrelated hub. `_is_this_host` only filters the
  machine doing the scanning; it was never meant to filter every other piece
  of infrastructure on the network that happens to run mDNS, and it could not
  have. Removed from the active search entirely rather than filtered after
  the fact: a service type that structurally cannot identify a controllable
  device should not be searched for as though it might.
- **The hub did not recognise devices it already had.** The scan marked a
  finding as registered by comparing `device_id`, and a discovery id is not an
  inventory id: the transport fabricates the first — `wiz-auto-192-168-100-33` —
  while the second is what the operator chose to call the thing, which this
  project defends elsewhere as the right way round. So a bulb registered for
  months under a name its owner picked came back as a new device, was offered
  for adoption, and was adopted. The reference deployment ended with eleven WiZ
  entries for ten lamps.

  The address that would have revealed it sat in `adapter_config` from the
  original registration. The hub had the datum and never looked at it.

  It now **reports** the match — *a device you named X is registered at this
  same address* — and still does not decide. No merging: addresses move with
  DHCP, and a hub concluding identity from one would eventually fuse two
  different devices, which is worse than duplicating, because a duplicate is
  visible and a fusion is not. The person recognises the name they chose; that
  is what settles it, and it is what they were never shown.

  Correlation is on the address rather than the adapter, so the same device
  found over a different transport still matches — the general case of which
  the duplicate bulb is one example.

- **A discovered device's identity may not be derived from its address**, and
  the rule is now in the discoverer contract where someone writing their own
  will read it. It is not a new constraint: BLE already keyed on the device MAC,
  SSDP on the serial in a `USN`, mDNS on the announced service name. WiZ built
  its id from the IP and was the only one, which is exactly where the duplicate
  appeared.

  Where a stable identity comes from is the transport's business and only the
  transport knows it; what the protocol requires is that it survive the device
  moving. A test now checks every discoverer rather than the one that broke it —
  a rule that lives only in the head of whoever applied it is what this project
  turns into a test every other time.

  Identifiers already in a registry are untouched: this names devices discovered
  from here on, and nobody wakes up to new identifiers because the scheme
  improved.

- **A commissionable device is declared as one.** Four `_matterc._udp` findings
  were offered as ordinary adoptions and adopted. A commissionable device is
  announcing that *nobody has paired it yet* — on a shared network or in a
  building it may not be the operator's at all. Not hidden, said.


### Fixed
- **The hub offered itself as a device, three times.** A host announces
  `_workstation._tcp` from every interface it holds — loopback, the LAN, a
  docker bridge — and each announcement carries a different MAC in its name, so
  keying on identity kept all three. Filtering loopback was not enough: two of
  them arrived on routable addresses. An operator scanning the reference
  deployment was asked to adopt the hub three times before reaching a real
  device.

  Address and hostname together now, because a hub does not always resolve the
  address of a bridge interface it holds, and both discoverers use the same
  decision.

- **The drafting prompt was teaching the tag antipattern it exists to prevent.**
  `TAG-VOCABULARY.md` has a *Deprecated tags* table laid out identically to the
  category tables, and scraping the whole file swept it in — so a model
  describing a device was handed `climate`, `door-lock` and `smart-plug` as
  vocabulary. Two of those three are tags this project removed from its own
  deployment, and one of them, `smart-plug` on a bulb, is the example the
  Concepts article uses to explain why the antipattern costs something.

  Found by reading the text the button produced. Every test passed: they checked
  that the vocabulary was present, not that it was the right vocabulary.

- **A failed clipboard copy would have been reported as success.** The API needs
  a secure context — HTTPS or localhost — and a hub reached at
  `http://<lan-address>` is neither. It worked on the reference deployment, so
  this is latent rather than observed; an empty `catch` followed by *"Copied to
  your clipboard"* is the system asserting something it decided not to check.

### Changed
- **The description opens with the device instead of the format.** It runs to
  249 lines, and whoever reads it — a person or a model — should see what was
  actually found before the schema and three example files.

- **The description came out empty for exactly the devices it exists for.**
  `CapabilityManifest.to_dict()` carries `adapter_config` only when an adapter
  is declared, and a device with no adapter is the case being described — so the
  address and service type recorded at adoption vanished on the way out. The
  reference deployment's 3D printer produced a description whose evidence block
  was entirely blank: no address, no service type, nothing announced. Anyone
  reading it could not have located the device, let alone described it.

- **The dashboard button returned a JSON parse error.** The description is plain
  text; the page's `api()` helper always parses JSON, and asking it not to by
  passing a fourth argument it does not accept produced `Unexpected token 'Y'` —
  the first word of the description — on the first press. The endpoint had been
  tested with curl and the button had not.

### Added
- **A discovered device can now be described, and the description is reachable
  from where the problem shows.** A scan reports an address and a service type;
  DoSync resolves over declared capabilities and nobody has declared any, so an
  adopted device stays visible and inert. The hub already knew — it reports
  those devices at every start and marks them in the dashboard — and offered no
  next step.

  `GET /v1/devices/{id}/describe` returns **plain text**: what the hub observed,
  the normative tag vocabulary, the adapter format with three shipped examples,
  and where the resulting file goes. It sends nothing anywhere. Whoever holds it
  decides where it goes — an assistant they already use, a model their
  organisation permits, or an engineer.

  **That is not a limitation, it is the point.** In a plant, a hospital or a
  managed building, sending the topology of a network to an outside service is
  frequently prohibited and often impossible, and a feature that only worked by
  calling out would exclude precisely the deployments this protocol is for. The
  hub still does not know that language models exist.

  Reachable three ways, all giving the same text: the dashboard offers it on the
  card of any device it cannot act on; `dosync-manage draft-adapter <id>` prints
  it, with `--send` as an opt-in for an operator who has a local model; and
  `dosync_describe_device` over MCP, for an agent already connected to the hub.

- **MCP gained discovery and adoption.** The protocol's central case is an agent
  connected to a hub, and of the five tools it exposed, none could scan, see
  what was found, or adopt it — the first draft of this feature asked the
  operator to copy a prompt by hand and paste it into that same agent.
  `dosync_discover_devices` reports findings along with which transports were
  searched and which were skipped; `dosync_adopt_device` registers one by a name
  the operator chooses.

  The hub does not call a model in any of these paths. It exposes evidence and
  receives a proposal; the operator saves and approves the file, and that file
  goes through change review like any other change.

  **The prompt is a text file** — `dosync/templates/adapter-draft-prompt.md` —
  rather than a string in a module: it is the most reviewable artefact of the
  feature, someone who distrusts a model describing their hardware wants to read
  what it is asked before deciding, and a manufacturer can adapt it for their
  products without forking anything. A test checks it presumes no domain, since
  that was asked explicitly and this project does not take a property on trust
  when it can be measured.

### Removed
- **`ROADMAP.md`, which said the current release was v0.3 three months after
  v0.5.0 shipped.** It is the document a stranger opens to find out whether
  anything is happening here, and it answered wrongly — in a project whose
  entire argument is that a system must not assert things that are not so.

  Deleted rather than updated, because the failure was structural: a
  hand-maintained list of releases drifts the moment attention goes elsewhere,
  and the release history already lives in this file, where it cannot. What does
  not age moved to `docs/VISION.md`: the FamilyOS context, the explicit list of
  what the project will *not* do, and the guarantee that answers anyone deciding
  whether to build on this — DoSync stays an independent open protocol whatever
  becomes of FamilyOS.

  Three of the four "open questions" it carried had been answered since June —
  arbitration between simultaneous intents, the plugin model, mid-execution
  restarts — and copying them across would have moved stale text into a new
  file. The ones that remain are stated as they actually stand.

### Changed
- **The principles of the project existed twice, and had drifted 265 lines
  apart.** `DESIGN-PRINCIPLES.md` and `docs/DESIGN-PRINCIPLES.md` were both live
  for three months — the first created 3 June and edited through 12 August, the
  second created 20 May and edited through 31 July. Neither was a stale copy:
  the root held the founding principle (*the intelligence lives in the mind, not
  the body*) and the newest safety decision; `docs/` held the rules on adapters,
  optional dependencies and how to write a test — one of which was consulted
  this week to decide that a discovery library belongs in the core install. The
  code referenced the copy without the safety decision.

  Merged into one document in the root, section by section rather than
  concatenated, with every section from both preserved. A project whose reason
  for existing is that a system must not say two different things about itself
  cannot keep two versions of its own principles.

- **The repository root said nine things were worth opening first.** A file in
  the root is a claim on a stranger's attention, and four of those claims were
  false. `MULTIHUB-PHASE-A-DESIGN.md` had zero inbound links; `COMPATIBILITY.md`
  is for integrators; and `TUTORIAL.md` — the name a stranger opens first —
  builds a device that speaks the protocol and asks for Docker in step 1, while
  the README mentioned it once on line 135, after the list of people the project
  is *not* for.

  The root now holds what GitHub surfaces plus two declared exceptions, the
  tutorial is `docs/DEVICE-INTEGRATION.md` and states its requirements before
  its first section, and the README opens with a map of where to start. Moved
  files leave pointers: thirteen published articles link to these paths and
  cannot be edited.

  `tests/test_documentation_is_navigable.py` enforces it, and found two more
  while being written: `GOVERNANCE.md` was unreferenced too, and `ROADMAP.md`
  was in the root with nobody having decided it should be.

## [0.5.0] — 2026-08-17

Two new discovery transports, and fourteen defects found by installing the
project from scratch and using it as a stranger would — on a clean Linux
container, on Windows, and against real hardware that had nothing to do with
home automation. None of them appeared in the backlog. The protocol itself
needed no change on any platform; almost everything corrected here was what
the system said about itself.

### Added

- **SSDP discovery, written from a capture rather than a specification.** A real
  device — a 3D printer on a home network — announced continuously and was
  invisible to the mDNS scan, because it does not use mDNS. Its announcement
  taught two things a reading of the standard would not have.

  **The port is not always 1900.** That printer announces on **2021**. A
  discoverer joining only the standard group would have reported "nothing found"
  about a device shouting every few seconds — the exact false negative the
  searched/skipped reporting exists to prevent, arriving through a different
  door.

  **A vendor-namespaced device type is still a device type.**
  `urn:bambulab-com:device:3dprinter:1` is not a standard URN and it says
  *3dprinter*, which is what a person needs to decide whether to adopt it. The
  vendor namespace is dropped; the vendor's own headers are kept verbatim and
  uninterpreted, because interpreting them is where a product catalogue starts.

  The capture is the test fixture, serial and address redacted. And the same
  announcement showed the limit of all of this: the printer was bound to its
  vendor's cloud, where no local hub can command it. Discovery found it and
  could not have driven it. Finding is not controlling.

- **A discovered device can be kept even when nothing has declared what it
  does.** `/v1/discovery/adopt` required an adapter, written when the only
  discoverer was WiZ and a finding always carried one. Generalising discovery
  exposed the assumption in the worst possible place: the scan would show
  someone their printer and then refuse to let them keep it.

  Such a device is now adopted as **inventory** — known, named, visible in the
  registry, openly unable to act. That is honest only because of work landed
  this week: an adapter-less device is reported as unexecutable at every start,
  and any action on it returns `simulated`. Without those two, adopting an inert
  device would be a trap rather than a step.

  The dashboard shows what the device announced itself as — `3dprinter` tells a
  person what they are looking at, an address does not — and says plainly that
  adding it records it rather than making it actionable. Its empty-scan message
  no longer claims that only WiZ can discover.

- **mDNS/DNS-SD discovery, and a place for discoverers to live.** The scan
  endpoint already argued, in its own docstring, that discovery must not be an
  IP-only idea — and then reached exactly two transports, because the only way
  for a component to be searched was to be an *adapter*. Anything that finds
  without executing had to implement `execute` to be seen, which deformed the
  model to fit the plumbing.

  A hub now registers **transport discoverers** alongside adapters. A discoverer
  only listens and reports; it has a different lifecycle (its library is enough
  — no credentials), a different failure mode (you do not know what you have,
  rather than a device not moving), and a different trust property: enumerating
  someone's network is the first thing an attacker would want.

  `MDNSDiscoverer` covers the class of devices that announce a local API —
  printers, NAS, cameras, Shelly, Tasmota, ESPHome, Matter commissionables — with
  one implementation instead of an adapter per vendor, which is the path the
  project declined when it labelled WiZ and Shelly as reference adapters. The
  service list decides **where to listen**, never what a device can do, and it
  asks the network to enumerate its own types so unknown ones still surface.
  `zeroconf` ships in the core under the rule `pyproject.toml` already states:
  discovery libraries are needed *before* the knowledge that would justify
  installing them.

  **What this deliberately does not do** is turn a discovered service into a
  capability manifest. A `_octoprint._tcp` on the network is not yet a DoSync
  device, because DoSync resolves over declared capabilities and nobody has
  declared any. Who writes that manifest is a separate design question, left
  open.

- **A finding reports what a device announced itself as.** `service_type` and a
  `likely_actionable` ordering hint. An address says where something is; the
  service type says what it claims to be, and it is the part a person can act
  on. Ordering is presentation only — a device outside the hint is still
  reported, just not first — because a scan returning forty unranked entries
  costs the reader more than it saves them.

  Found from the user's chair: preparing a from-scratch install with a real WiFi
  3D printer surfaced that a scan would not see it. The gap was coverage, not
  design.

- **An action that never left the hub says so.** `ActionResult` carries
  `simulated` and `simulated_reason`; `intent_executed` carries
  `actions_simulated`; `direct_action_executed` carries both; and the intent API
  reports them per action and in aggregate.

  The reference deployment ran an SMS notifier whose manifest named no adapter.
  Every `notify` fell back to the simulator, returned `success=True`, and was
  logged at INFO as `Executed:` — for an unknown length of time, on the
  deployment whose drills are this project's evidence. `success` and `simulated`
  answer different questions: `success=False` means something went wrong,
  `simulated=True` means nothing went anywhere. Collapsing them made the Data
  layer mislead by omission, and mislead the AI layer reasoning on top of it —
  an agent told that `notify` succeeded during an emergency concludes the people
  who needed to know were told.

  Three adapters already marked simulation inside `response`. That was the right
  instinct in the wrong place: a caller should not have to know which adapter
  answered to learn whether anything happened.

  `fallback_to_simulated` stays on by default. Simulating was never the problem
  — certification, evaluation corpora and development without hardware all need
  it. Not saying so was.

- **The hub says at boot which devices nothing can act on.** A manifest that
  declares actuators and names no adapter — or names one that is not registered
  — is reported: at registration for new arrivals, and once at startup for the
  whole fleet.

  A device whose manifest names the adapter `"simulated"` is not reported:
  declaring simulation is a legitimate choice — a test device, hardware that has
  not arrived, a certification fixture. The sweep's first hardware run flagged
  one of those alongside a genuinely misconfigured notifier, which is how a
  useful warning becomes noise an operator learns to skip, taking the real
  finding with it. `"none"` is deliberately not treated as such a request: a
  manifest saying "none" is saying it has no adapter, which is the case being
  reported.

  The startup sweep exists because the registration check alone was not enough,
  and hardware validation is what showed it: the reference deployment restarted
  with the check in place and printed nothing, because devices restored from the
  database go straight into the registry and never take the registration path.
  The check covered new arrivals and missed the entire existing fleet — which is
  exactly where a device sits misconfigured for months. The sweep runs after the
  adapters register, since running it earlier would report every device as
  unexecutable.

### Changed

- **The MCP tool descriptions describe the protocol, not one catalogue.** They
  were in Spanish, named a vendor (*"Para luces WiZ soporta…"*) in the contract
  of a generic tool, and documented `all_lights` — a convenience identifier the
  specification does not define — without saying so. An LLM reads those strings
  as the tool's contract. They now state that available actions come from the
  device's own capability manifest, that direct actions are governed under the
  reserved `direct_control` class, and that `all_lights` is client-side fan-out
  rather than a protocol feature.

  `all_lights` also selected on `["light", "wiz"]` — a vendor tag, the
  antipattern TAG-VOCABULARY documents. Now `light` alone; measured on the
  reference deployment, the selection is identical either way.

- **The core and the specification are in English.** 255 lines of Spanish
  remained across 29 files — module headers, docstrings, the MCP tool
  descriptions, user-facing SMS text. An open protocol whose core carries one
  contributor's language cannot be read or implemented by most of the people it
  asks to adopt it. Enforced by a test scoped to `dosync/`, `tools/` and
  `spec/`; `examples/` is deliberately outside it, because a demo narrating one
  deployment in its operator's language is legitimate.

  The first version of that test required three Spanish stopwords on one line
  and let 81 lines through — `Instalación:`, `Características:`,
  `Posición 0-100%` are each a single word, and they are the labels a reader
  sees first. One accented character is now enough, with panel surnames cited in
  decision comments declared as exceptions.

- **Location tags are stated as an open namespace.** The spec listed ten
  locations — `bedroom`, `kitchen`, `garage`, `basement` — in a normative
  document that an industrial or clinical implementer reads, and defined
  `appliance` as a *home* appliance. The code never restricted anything:
  `location_hit` is string equality against `context.location`, which is why the
  clinical and industrial corpora work today with `or-3` and `cell-2`. But the
  document taught that the protocol was a house.

  §5 now says what the mechanism already did: the deployment defines its
  locations, a conforming hub MUST NOT reject one for being absent from any
  list, and the residential names are examples alongside industrial and clinical
  ones. Pinned by a test that resolves an intent against a device tagged
  `death-star` — if an enumeration ever creeps in, it fails.

- **Excluded devices now say what would include them.** A device whose actuators
  fit an intent but whose tags do not is reported as excluded with the tag that
  would change that, plus a new `actuators_fit_resolution` field, instead of
  being silently dropped. Correcting the divergence by simply removing those
  devices would have discarded the one genuinely useful thing the wrong answer
  contained.

  **Consumers of `/v1/intent/explain` should note:** devices that only matched
  on actuator move from `included` to `excluded`. No resolution changes —
  benchmark precision, recall and F1 are unchanged across all four corpora
  (home, industrial, clinical, and the reference deployment snapshot).

- **The universal intent resolution contract is now in the specification.**
  Spec §6.4.1 states the resolution tags and actuators of the five universal
  intents normatively. They previously existed only in
  `_seed_universal_intents()`, so a second implementation written from `spec/`
  alone would resolve `control_access` differently and still pass certification.
  Measured cost of the gap: an industrial door tagged `access` + `security`
  (both standard vocabulary) scores F1 0.00 — roughly a quarter of the
  multi-domain agnosticism gap. The table is pinned to the seed by a test that
  parses it rather than restating it.

### Fixed

- **Three things the dashboard said that were not what the hub did.** All from
  the same from-scratch Windows install, and all of them the interface
  describing the system inaccurately rather than the system misbehaving.

  The header read `disconnected` while devices loaded, scans ran and every call
  returned 200 — the word described the WebSocket, which had no library to speak
  it, and a reader took it to mean the hub was unreachable. A reachable hub
  whose live events are not now says so.

  The token field said `API token…` and nothing else. Help existed behind a `?`
  button, and the project's own author asked where to get a key without finding
  it, which settles how discoverable it was. The field now names where the first
  token appears and points at the button.

  And the scan endpoint has always reported which transports it searched and
  which it skipped — the page discarded both. So a scan that never searched WiZ,
  because pywizlight was missing, told the operator that no device had answered.
  The bulbs were powered on. Nothing answered because nobody asked. The empty
  result now lists what was covered and what was not.

- **The hub served a WebSocket endpoint without a library that speaks it.**
  `uvicorn` without the `standard` extra ships neither `websockets` nor
  `wsproto`, so `/ws` answered 404 on every clean install and the dashboard —
  the first thing the README now tells a new user to open — read `disconnected`
  forever while every other call returned 200. Found on a from-scratch Windows
  install, and it was never a Windows problem: it was every install that did
  not happen to have `websockets` pulled in by something else, which is exactly
  why the reference deployment never showed it.

- **The scan claimed to have searched a transport it had skipped.**
  `discover_wiz` returns an empty list when pywizlight is absent — it logs and
  does not raise — so the scan appended WiZ to `searched` regardless. The same
  Windows install reported *"no devices answered on this network"* about bulbs
  that were powered on and reachable, because the library to reach them was
  missing and nothing said so. Reporting a transport as searched when it was
  skipped is the one failure that whole distinction exists to prevent.

- **A finding from a discovering adapter could not be adopted.** The install
  found a television over Bluetooth, the dashboard offered it, the person named
  it, and adoption answered `422` — because it handled WiZ and adapter-less
  findings and rejected everything else. Doing exactly what the interface asks
  and getting nothing is worse than not being offered the option.

  Such a finding is adopted as inventory now, keeping the adapter that found
  it. The rejection remains for adapters that never discover — MQTT, a
  proprietary bus, a drone that answers no broadcast — because nothing found
  those devices, the request was written by hand, and naming manual
  registration is the honest reply.

- **The Quick Start led with `curl` and mentioned the dashboard much later.** A
  clean install on Windows made the cost obvious: adopting a device needs no
  terminal and no JSON — a 3D printer, a television and a Bluetooth sensor were
  adopted through the dashboard on the reference deployment without a line being
  typed — and the page opened with four API calls, telling anyone who does not
  write code that the project was not for them while a button doing the same job
  sat one section lower. The dashboard comes first now; the API calls are framed
  as what they are, the way to build against DoSync rather than to set it up.

- **Windows was undocumented, and the first command failed there.** Six things a
  clean Windows machine needed that this page did not say: `pipx` is not
  installed with Python on Windows, so the very first command of the Quick Start
  failed before the reader saw anything of the project; `pipx ensurepath`
  requires reopening the terminal; `export` is not a PowerShell command; `curl`
  is an alias for `Invoke-WebRequest`, a different program with a different
  syntax; `setup_pki.sh` is a shell script; and escaping JSON for `curl.exe`
  from PowerShell produces `JSON decode error`, because the escaped quotes are
  passed through literally.

  The hub itself ran on Windows without a change — the protocol was fine and its
  documentation was not.

- **Two cosmetic defects a real scan exposed, and one milestone it reached.**
  The first live scan with all four earlier fixes in place found a 3D printer as
  `3dprinter` at its own address — a device with nothing to do with home
  automation, discovered and correctly identified without an adapter, a vendor
  catalogue, or a line of code written by its operator.

  In the same output: a television's name came back as `75&quot; QLED`, because
  XML escapes its entities and nothing unescaped them — the right bytes and the
  wrong name. And that television appeared twice, because it publishes two UPnP
  devices (a DIAL receiver and an IP control server) with different UUIDs and
  the same hardware behind them. Technically distinct; to the person deciding
  what to adopt, one television reported twice. Findings are now grouped per
  host, keeping the entry that names a device type over one that only says
  `rootdevice`.

- **One device announcing many times was many devices.** SSDP devices announce
  repeatedly — once as `upnp:rootdevice`, once per service, once bare — and each
  announcement carries a different `USN` of the form `uuid:XXX::urn:YYY`. Keyed
  on the whole USN, a network with two devices reported twelve findings, and the
  dashboard asked about every one of them in its own dialog. Identity is the
  uuid before `::`, and the announcement that names a device type wins over the
  one that only repeats the uuid.

- **The hub discovered its own search.** Multicast returns to the sender, so
  every scan received the hub's own M-SEARCH, parsed it as an announcement, and
  reported the hub as a device offering `ssdp:all` — once per port.

- **A device is named by its description document when it serves one.** SSDP
  headers give a type; the document at `Location` gives `friendlyName`,
  `manufacturer` and `modelName`. A television announced itself as
  `IPControlServer` in its headers and as a `75" QLED` by Samsung in its
  document, and only one of those is worth showing someone. Best-effort: a
  device that does not serve the document is still a finding, because discovery
  must not depend on a second request succeeding.

- **SSDP `Location` comes in two shapes, and the parser knew one.** A 3D printer
  sent a bare address (`Location: 192.0.2.91`); a TV on the same network sent a
  full URL (`Location: http://192.0.2.105:9110/ip_control`). Written against the
  printer, the parser split on `/` and reported every device of the second kind
  at the address `http:` — which showed up in the dashboard offering to adopt
  something "@ http:".

  Written from one capture, broken by the next capture on the same network. Both
  shapes are now fixtures. A device is also named by what it called itself
  rather than by its Location URL: vendors publish a name under their own header
  (`DevName.bambu.com`), and the first attempt to read those tested the whole
  key for a `name` suffix, which matches nothing because the key ends in the
  vendor's domain.

- **The mDNS meta-query was asked and never used.** `_services._dns-sd._udp`
  asks a network to name every service type it offers, and its answers are
  *types*, not devices — so a scan has to open a browser for each one. Nothing
  did. The first real run returned only the types hard-coded in the list, while
  the docstring promised that unknown ones would still surface and a test
  asserted the query was present. The query was present and inert.

  Two smaller things the same run showed: one host announcing on loopback, the
  LAN and a docker bridge came back as three findings instead of one, and the
  hub reported finding itself.

- **The discoverer registry was consulted and never filled.** The scan loop
  shipped; the registration did not. A `str.replace` whose anchor did not match
  left the file untouched and reported nothing — `replace` returns the original
  string rather than failing — so the endpoint asked a registry no code ever
  built.

  On the reference deployment the scan returned 200 with mDNS in neither
  `searched` nor `not_searchable`: absent entirely, which is the one outcome the
  searched/skipped reporting exists to make impossible. Every unit test passed
  throughout, because they exercised the pieces and none asserted the pieces
  were connected. There are now tests for the wiring itself.

- **A reference adapter no longer presumes the operator's hardware.** The WiZ
  adapter logged a WARNING at import time on every hub — *"pywizlight not
  installed. Install with: pip install pywizlight"* — including on hubs whose
  operator owns nothing from that vendor and never will. It was also the only
  adapter registered unconditionally: BLE checks whether its library imports,
  MAVLink is opt-in, the Home Assistant bridge needs a token.

  That contradicted the project's own adapter taxonomy, which calls WiZ and
  Shelly *reference* adapters — worked examples of how an adapter is written,
  not endorsement. A worked example does not get to tell a stranger what to
  install.

  It now registers only when its library is present and says nothing when it is
  not. An operator who has registered a WiZ device is told where it matters
  instead: the startup sweep names their device and says its actions will be
  simulated.

- **The Quick Start did not run as written.** A clean-room install — `pipx
  install dosync` on a machine with nothing on it — worked: the package
  installed, the hub started, printed its key, and the protocol did everything
  it claims. Then all four documented `curl` commands returned `401 Missing
  Authorization header`, because none of them carried the token the hub had just
  printed, and nothing told the reader to keep it.

  Nothing was broken. The first minute of a stranger's experience simply looked
  like it was, immediately after the page said "that is a working hub" — which
  is the worst shape a defect can take for a project with no users yet.

  `tests/test_quickstart_is_runnable.py` now checks that every documented
  request to an authenticated endpoint carries a token, and that the reader is
  told where that token comes from. Its own first version was vacuous: it listed
  `"/"` among the public paths and matched with `startswith`, so every path
  counted as public and the check passed while the header was missing — the same
  false positive as a grep for "FOUND" matching "NOT FOUND".

- **The README carried the access section twice.** Two versions of "how to set
  or disable the API token" sat one after the other, saying nearly the same
  thing in different words — a rewrite that never deleted the original. The
  second is the better one (it notes that `DOSYNC_AUTH` in the environment wins
  over the dashboard, and that changing access is recorded in the audit chain),
  so the first was removed.

- **The README understated its own API by more than three times**, listing "12+
  endpoints" against 44. Of all the numbers a project can get wrong, this is the
  one that costs it something.

- **The public site advertised four things that were no longer true.** Its
  roadmap still read *"IEEE WF-IoT 2026 — submitted, decision pending"* a month
  after the decision arrived; it offered `pip install dosync` at 0.4.2 with
  0.4.3 on PyPI; it claimed 894 automated tests against 930; and it listed the
  Node.js implementation as Done, which the README had requalified the day
  before.

  The first one is the one that matters, and not because of the paper. The page
  carries a section titled *"We audited the five properties we advertise. Two of
  them were false."* A line announcing a pending decision that resolved a month
  earlier is exactly what that section promises does not happen here.

  The test count had been corrected twenty-four hours earlier — 866 to 894 — and
  was stale again by morning. An exact number in a static page is a promise to
  update it every week and nobody keeps it, so the site now states a floor that
  writing more tests cannot falsify, and `tests/test_public_claims.py` checks
  the floor, the version, the conformance figure, that no decision is described
  as pending, and that the README and the site describe dosync-node the same way.

- **The Concepts series taught the tag antipattern.** Part 5 presents a
  Capability Manifest as the worked example of how to declare a device, and the
  example carried `["light", "climate", "smart-plug", "emergency", "wiz"]` —
  three tags the project's own vocabulary lists as deprecated: a vendor name, a
  role the device does not have, and a capability a bulb does not have. The
  material teaching people to tag devices was teaching them to tag devices
  wrong.

  Corrected, and the original kept as the lesson: those three are the three
  mistakes everyone makes, and the deployment that wrote this protocol had all
  of them for two months.

- **A working notification template no longer breaks the notification.** The
  first implementation passed the protocol's own fields as keywords alongside
  `**context`, so a template using `{location}` with a context that carried one
  — most emergencies — raised `TypeError`, and the exception escaped
  `_build_message` and took the whole notification with it.

  The fallback for *broken* templates had been written and tested; the case
  where a template *works* had not, and it was the reference deployment that
  would have found out, during an emergency. Context now provides the base
  fields and the protocol's override them, `TypeError` and `ValueError` join
  the caught set — a template must never be able to silence an alert — and
  `tests/test_notification_templates.py` covers the rendering paths that were
  missing.

- **The repository no longer carries one deployment's configuration.** An audit
  found the reference deployment across the tree: its LAN address in the
  certification CLI's own `--help`, in `setup_pki.sh`, in the benchmark docs and
  in the public site; `/home/<user>/...` hard-coded in a sensitivity tool that
  therefore ran on exactly one machine; the operator's device inventory inside
  the **normative tag vocabulary**; and room names — one of them a child's
  bedroom — in evaluation fixtures, demos, the GPIO adapter and `index.html`.

  A protocol that calls itself domain-agnostic cannot ship one household as its
  worked example. Identifiers are now role-based and domain-neutral
  (`light-zone1-01`, not `wiz-cocina-01`), addresses are placeholders or
  documentation ranges, and the paper and docs were renamed in step so the
  published tables still resolve against the published fixtures. Benchmark
  metrics are unchanged, which is how the rename was verified.

  Also translated the remaining Spanish docstrings and comments in the core and
  the adapters: the project requires English, and one contributor's language in
  a product surface is the same defect in a different form.

  The rule is now written in CONTRIBUTING.md and enforced by
  `tests/test_no_operator_data.py`, with an allow-list that must carry a reason.
  Two prior design panels had already decided this and the repository drifted
  anyway — a rule without a test is an intention.

- **The explanation and the decision now evaluate the same devices.** `explain()`
  reported devices as *included* that `resolve()` structurally could not act on:
  the two disagreed on who the candidates were. `resolve()` selected through the
  tag index, `explain()` iterated every active device, so a device matching only
  on **actuator** scored 12 in the explanation and was never a candidate in the
  decision.

  Measured across three registries: 2 in the reference deployment
  (`ensure_safety`), 2 industrial, 5 clinical — including an operating-room
  ventilation unit and a patient-facing display, both listed as participating in
  an emergency that never touches them. An operator auditing *"what does my
  system do in an emergency?"* planned around devices that would not move.

  This contradicted the project's first advertised property: the score reported
  is the score decided with. v9 (0.4.0) unified the scoring **formula** and left
  the candidate **set** split. Both callers now share `_candidates()`, which also
  owns the emergency force-inclusion that previously lived inside `resolve()`
  alone — sharing the set without carrying that rule would have fixed one
  divergence by creating another, in emergencies.

  Found by measuring, not by reading: it surfaced while evaluating a proposal to
  select devices by declared actuator, which measured zero change because the
  hard filter was never the gate — the candidate index was.

- **A withdrawn device is no longer planned into an emergency.** `active()`
  filters quarantined devices and its docstring states why — "it must not be
  planned into an emergency, because the operator already believes it is gone".
  `find_by_tags()` and `find_emergency_capable()` are raw indexes and filtered
  nothing, so `resolve()` planned them regardless: the contract was documented
  in one method and broken in two.

  Found on the reference deployment by counting, once explain and resolve shared
  a candidate set: 21 devices reported for `ensure_safety`, 20 for every other
  intent. The extra one was `luz-declarativa`, quarantined after its declarative
  file was removed and entering every emergency since — through the emergency
  force-inclusion, carrying no emergency tag, so no tag audit would have shown
  it. Force-inclusion exists to beat the tag filter, not the operator.

- **Importing a module no longer mutates the environment.** The notifications
  adapter read a `.env` at import time and applied it with
  `os.environ.setdefault`, silently, inside a bare `except Exception: pass`.
  Two tests failed on the reference deployment and passed everywhere else: they
  deleted `DOSYNC_POLICIES` with monkeypatch, an import ran, and `setdefault`
  put it straight back — a test cannot isolate an environment that an import
  un-isolates. Loading is now an explicit `load_env_file()` call the hub makes
  once at startup, reporting how many settings it applied, and Twilio settings
  are read when used rather than frozen at first import.

- **The operator-data check no longer reports clean while missing things.** Its
  first published version scanned seven file extensions and four kinds of hit,
  and an independent audit of the same commit found four categories it had
  waved through: systemd units carrying `/home/<user>` and the deployment
  address (`.service` was not scanned), a SQL export with room names (`.sql`
  was not scanned), the reference deployment's device identifiers in the spec's
  JSON schemas, `concepts.html` and an example policy file (nothing looked for
  identifiers at all), and vendor brands in a benchmark `.py` (that check only
  read `.json`).

  A test that reports clean while missing four categories is worse than no test,
  because it converts an unexamined repository into an examined one. It now
  scans every tracked text file and looks for identifiers too, and the systemd
  units ship as templates against `/opt/dosync` rather than one person's home
  directory.

### Removed

- **A country's emergency number is no longer inside the protocol.** The SMS
  body for `ensure_safety` ended with `Llamar al 107 (SAME)` — one country's
  medical emergency service, hard-coded in the notifications adapter. An
  operator anywhere else received, during a real emergency, a number that does
  not answer. It was the only finding of its audit with a physical consequence.

  It was **not** made configurable. An option for "who to call" still assumes
  there is someone to call: an industrial deployment stops a line, an aerial one
  notifies a ground station. The five hand-written message templates went with
  it. What remains is what the hub can state truthfully in any domain — which
  intent fired, at what urgency, where, and any message the caller passed — and
  a deployment that wants its own wording supplies
  `DOSYNC_NOTIFICATION_TEMPLATES`. A broken template logs and falls back rather
  than silencing an emergency notification.

## [0.4.3] — 2026-08-08

### Changed
- **A deployment now has one place to live.** Configuration resolves
  `~/.config/dosync/` → `/etc/dosync/`; state (audit chain, PKI, checkpoints,
  archived segments) goes to `~/.local/state/dosync/` or `/var/lib/dosync/`.
  Paths cascade because the tension is real: `pipx` installs as a user who
  cannot write to `/etc`, a systemd unit runs as root and should.

  Found preparing to reflash the reference deployment, whose configuration lived
  in nine places — four of which its own author had forgotten, and three of
  which sat inside a git clone where `git clean -fdx` would have destroyed a
  42,000-entry audit chain and the CA's private key. This protocol argues its
  evidence survives root access; it did not survive tidying a repository.

  **Nothing breaks.** An explicit `DOSYNC_*` variable always wins, an existing
  database in the working directory keeps being used with a warning saying where
  it belongs, and finding data in two places raises rather than choosing —
  choosing wrong means writing to one chain while auditing another.

  The PKI directory is created 0700; it previously inherited whatever it got.

- **The hub says when it is only reachable locally.** Binding to loopback stays
  the default, but a headless Raspberry Pi whose operator is on SSH now gets
  told how to reach the dashboard from another machine, instead of a browser
  that will not connect and no explanation. Found by installing from PyPI on a
  clean machine and trying.

### Documentation
- `docs/DEPLOYMENT-LAYOUT.md` — where a deployment lives, how to migrate an
  existing one, and backup as two directories instead of nine locations.
  Deliberately not in the protocol spec: mandating Linux paths would tie an
  implementation in Rust on FreeBSD to conventions it does not share.

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
- **The connection indicator flickered between "live" and "live events
  unavailable" on a hub that was never actually disconnecting.** The dashboard
  auto-connects on page load using a saved token; pressing Connect by hand
  while that was in flight created a second WebSocket. Each socket's `onclose`
  scheduled its own reconnect independently, so an old socket closing after a
  newer one had already reported "live" flipped the status back down and
  queued a redundant retry — not a real drop, two generations of `connect()`
  briefly disagreeing about which was current. Found reinstalling on Windows
  from scratch, the fourth thing that install turned up. A generation counter
  now guards every socket callback: once superseded, a stale socket's events
  are inert.
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
