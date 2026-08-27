# DoSync Protocol

> Governance and accountability for AI that acts on physical devices.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Protocol](https://img.shields.io/badge/protocol-v0.4-green.svg)](spec/DoSync-SPEC-v0.1.md)
[![PyPI](https://img.shields.io/pypi/v/dosync.svg)](https://pypi.org/project/dosync/)
[![Python](https://img.shields.io/pypi/pyversions/dosync.svg)](https://pypi.org/project/dosync/)
[![CI](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml/badge.svg)](https://github.com/giulianireg-spec/dosync-protocol/actions/workflows/ci.yml)
[![Certification](https://img.shields.io/badge/certification-Conformance%2052%2F52-orange.svg)](dosync/certify.py)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](dosync/mcp_server.py)

---

## The problem

Today's IoT protocols speak the language of commands. AI speaks the language of goals.

```python
# Existing protocols
lock.unlock()
light.set_brightness(100)
thermostat.set_temperature(21)

# What an AI actually expresses
"there is an emergency at home"
"nobody is home — save energy"
"good morning"
```

Someone has to translate. Today, that translation is custom code written per-device, per-platform, per-scenario. It breaks when you add a new device. It completely fails in emergencies where milliseconds matter.

**DoSync is the bridge.**

---

## What it does

DoSync is an open protocol (Apache 2.0) that lets AI systems interact with physical devices using **semantic intent** — expressing *what they want to achieve*, not *how to achieve it*.

When the hub receives `"ensure_safety / emergency"`, every registered device figures out its own role automatically based on its declared capabilities — no hardcoded rules, no manual configuration.

---

## How is this different from what already exists?

A fair question, and the honest answer is that DoSync sits **above** most of what
it gets compared to, not against it.

| | What it does | What it does not decide |
|---|---|---|
| **Matter, Zigbee, MQTT** | Move commands to devices | Which device should act, or whether it should |
| **W3C Web of Things** (Thing Description) | Describe a device's properties, actions and events, with semantic annotations | Which devices serve a goal, what an operator forbids, or what happened afterwards |
| **MCP, A2A** | Connect an agent to tools | Anything about a tool being a door lock and the action being irreversible |
| **DoSync** | Resolve a goal to a plan, constrain it, execute it, and prove what happened | Transport, device description, or agent connectivity — it uses all three |

**Specifically on W3C Web of Things**, since it is the closest and the most
established: a Thing Description tells you a lock exposes a `lock` action and
how to invoke it. That is genuinely the right way to describe a device, and
DoSync does not compete with it. What a description cannot do is decide that a
lock is one of the things that should respond to *"there is an emergency"*,
refuse to touch it because this deployment forbids it, arbitrate when two
intents want it at once, or leave evidence afterwards that survives someone with
root access. Those are the questions DoSync answers.

**On MCP**: DoSync ships an MCP server. It is a distribution channel, not a
rival. MCP is how an agent reaches DoSync; DoSync is what happens between the
agent's goal and a device moving.

### The five things

Everything above reduces to five properties. Each is verifiable in a running
hub — the numbers below come from the reference deployment, not from a
brochure:

1. **Explainable resolution.** `GET /v1/intents/{class}/explain` returns which
   devices were evaluated, which were included, and the score breakdown behind
   each. The score it reports is the same value the resolver decided with — one
   computation, not a narration of one.
2. **Policies the AI cannot route around.** A deployment declares what must not
   happen; every path to a device is evaluated against it, including direct
   device actions and the MCP tool. This was not true until we audited our own
   claim and found the hole.
3. **A record that resists tampering — and says where it stops.** SHA-256 chain
   with policy provenance, plus sequence numbers, a head mark and signed
   exportable checkpoints. What it detects and what it cannot is written down in
   [the threat model](docs/AUDIT-THREAT-MODEL.md), including the rows that read
   "not detected".
4. **Formal arbitration of physical conflict.** A per-device claim state machine
   with stated invariants ([consistency model §3.1](spec/CONSISTENCY-MODEL.md)), so an
   emergency and a routine wanting the same device is resolved by rule rather
   than by timing.
5. **Failure semantics that do not lie.** `contradicted` (the device said yes,
   the sensor disagrees) is distinct from `unverifiable` (we could not look), and
   `likely_powered_off` from `indeterminate`. The system says what it does not
   know.

None of these is claimed to be unbreakable. Claim 3 in particular has documented
limits, on purpose: a protocol whose value is honesty cannot make absolute
security claims and stay coherent.

---

## Scope and safety boundaries

DoSync coordinates **non-safety-critical systems** — lighting, access, climate, notifications, logging — and produces a tamper-evident record of every action. It is infrastructure for coordination and auditability, not a certified safety system.

DoSync is **not** certified to IEC 61508 / IEC 62304 / ISO 13849 and must not be the sole or primary mechanism for:

- Primary control of medical devices or life-support systems
- Fire suppression, gas detection, or emergency shutdown of SIL-rated machinery
- Any function where a failure could cause injury or loss of life

In regulated or industrial environments, DoSync **complements** the certified safety systems already in place — coordinating the peripherals around them and recording what happened — but never replaces them. The certified safety system remains in charge of safety.

See [Protocol Specification §12.3](spec/DoSync-SPEC-v0.1.md) for the full operational boundaries.

---

## Is DoSync for you?

DoSync earns its place in specific situations — and honestly gets in the way in others. A quick filter:

**It probably fits if you:**
- Are building an **AI agent that acts on physical devices** and need an auditable record of what it did, when, and why.
- Coordinate **heterogeneous devices** (different brands / transports) and want one semantic layer — express a goal, devices resolve it — with a tamper-evident audit trail.
- Work in **robotics or physical automation** and want a policy + safety layer (emergency preemption, confirmation policies) *between* the AI and the actuators.
- Keep hand-writing per-device command sequences and wish you could just say *"secure the space."*

**It's probably not for you if you:**
- Want home automation (schedules, motion → light). [Home Assistant](https://www.home-assistant.io/) and its automations — and its MCP server — already do that better; DoSync would be overhead.
- Have a single device or one brand's ecosystem — you don't need a coordination layer.
- Don't need auditability or a policy layer.

**If the first list is you:** the fastest way in is the **[20-minute device tutorial](docs/DEVICE-INTEGRATION.md)** — build a device that senses, expresses a goal, and acts, with the full audit trail. Or open an [issue](https://github.com/giulianireg-spec/dosync-protocol/issues) describing what you're trying to coordinate and we'll tell you honestly whether DoSync fits.

---

## Demo

[![DoSync Demo](https://img.shields.io/badge/▶_Watch_Demo-YouTube-red?style=for-the-badge)](https://youtu.be/2czAqoIrd08)

**What you'll see:** Claude AI triggers a physical emergency protocol in real time — 10 Philips WiZ bulbs at full brightness, SMS notification sent, audit log updated. No commands. No rules. No cloud.

---

## How it works

```
User / AI says:  "there is an emergency at home"
                          │
                       DoSync Hub
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   💡 All lights      📱 SMS sent     🚨 Alarm
   at maximum       to family        activated
   (10 WiZ bulbs)   immediately
          │               │               │
          └───────────────┴───────────────┘
                          │
                  Audit log updated
              (SHA-256 tamper-evident)
```

The **Capability-based Resolver** matches the intent against every device's **Capability Manifest** — what it can sense, what it can do, whether it's emergency-capable. No rules to write. Add a new device and it participates automatically.

Benchmark (Raspberry Pi 5, Python 3.11.2):

| Devices | Mean | p99 | Within 500ms limit |
|---|---|---|---|
| 38 (production) | 0.076ms | 0.097ms | ✓ |
| 1000 | 1.336ms | 5.690ms | ✓ |
| 5000 | 9.163ms | 24.541ms | ✓ (20× margin) |

---

## Protocol architecture

| Layer | Name | Role |
|---|---|---|
| 5 | **Intent** | AI expresses semantic goals |
| 4 | **Semantic** | Resolver maps intent → device actions |
| 3 | **Registry** | Devices self-declare capabilities on join |
| 2 | **Secure channel** | mTLS, local PKI — no internet required |
| 1 | **Transport (HAL)** | Reference: WiFi/HTTP-WS · MQTT. Via bridge: Zigbee · Z-Wave · Thread · Matter (Home Assistant). Native BLE/radio bindings: roadmap |

---

## Where to start

| You want to… | Go to |
|---|---|
| **run a hub and see your devices** | [Quick start](#quick-start), below — about five minutes, no terminal after the first command |
| **build a device that speaks the protocol** | [docs/DEVICE-INTEGRATION.md](docs/DEVICE-INTEGRATION.md) — needs Docker, a broker and Python |
| **implement DoSync yourself, or check conformance** | [spec/](spec/) — the wire format, the JSON schemas, and `certify.py` |
| **understand why it is built this way** | [DESIGN-PRINCIPLES.md](DESIGN-PRINCIPLES.md) |
| **know its scope, or who decides** | [docs/VISION.md](docs/VISION.md) · [GOVERNANCE.md](GOVERNANCE.md) |

Three of those four were reachable only by guessing. The one called `TUTORIAL.md`
sat in the repository root, where a reader looks first, and asked for Docker in
its first step.

## Quick start

### Install and run — five minutes, no hardware

```bash
pipx install dosync      # recommended
dosync-hub
```

<details>
<summary><b>On Windows</b> — pipx is not installed with Python there</summary>

Every step below was found by running it on a clean Windows machine. None of it
is exotic; it is simply what Windows needs and what this page used to omit.

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
```

**Close PowerShell and open it again.** `ensurepath` edits your PATH and the
session you are in cannot see the change. Then:

```powershell
pipx install dosync
dosync-hub
```

Two things the rest of this page assumes that PowerShell does not provide:
`export VAR=value` is `$env:VAR = "value"`, and `curl` is an alias for
`Invoke-WebRequest`, which is a different program with a different syntax — use
`curl.exe`, or the PowerShell examples further down. `setup_pki.sh` is a shell
script and needs Git Bash or WSL.

If a control library for your hardware needs installing afterwards, a plain
`pip install` will not reach it — `pipx` keeps the hub in its own isolated
environment. See **pipx inject**, below.

</details>

### Then open the dashboard

```
http://localhost:47200
```

Paste the API key the hub printed on startup, press **Scan**, and the hub
searches every transport it can reach — WiFi broadcast, mDNS, SSDP, Bluetooth —
and shows what answered. Name anything you want to keep and it is adopted.

**Start here.** Discovering and adopting a device needs no terminal and no
JSON: a 3D printer, a television and a Bluetooth sensor were adopted this way
on the reference deployment without a line being typed. The API calls below are
for building against DoSync, not for setting it up — they came first on this
page for a long time, which told people who do not write code that the project
was not for them, while a button that did the same job sat one section lower.

<details>
<summary><b>"error: externally-managed-environment"?</b> — Raspberry Pi OS, Debian 12+, Ubuntu 23.04+</summary>

Those systems refuse system-wide `pip install` (PEP 668) to stop Python packages
from breaking the OS. This hits the Raspberry Pi first, which is the most likely
machine to be running a hub, so it is worth getting right rather than working
around.

**`pipx` is the correct tool here** and not a workaround: DoSync is an
application with commands you run, not a library you import into your own code.
pipx gives it a private environment and still puts `dosync-hub`,
`dosync-manage` and `dosync-certify` on your PATH.

```bash
sudo apt install pipx        # once
pipx ensurepath              # once; open a new shell afterwards
pipx install dosync
```

If you are writing Python against DoSync rather than running the hub, a virtual
environment is the right choice instead:

```bash
python3 -m venv ~/dosync-env
~/dosync-env/bin/pip install dosync
~/dosync-env/bin/dosync-hub
```

`pip install --break-system-packages dosync` also works and is the one option we
would not recommend: it installs into the system Python that your OS depends on,
which is the situation PEP 668 exists to prevent.
</details>

That is a working hub on `http://127.0.0.1:47200`. It starts with a simulated
executor, so you can drive the whole protocol — register devices, fire intents,
read the audit chain — before you own a single smart device.

### Or drive it from the API

Everything the dashboard does is an HTTP call, and this is the part to read if
you are building against DoSync rather than setting it up.

The hub prints an API key on that first start and stores only a hash of it, so
it is shown once. Save it — every command below needs it:

```bash
export DOSYNC_TOKEN=<the key printed on first start>
```

```powershell
$env:DOSYNC_TOKEN = "<the key printed on first start>"
```

Register something and give it a goal:

```bash
# 1. A device declares what it CAN DO (not what commands it takes)
curl -X POST http://127.0.0.1:47200/v1/devices/register \
  -H "Authorization: Bearer $DOSYNC_TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "device_id": "siren-hall", "device_name": "Hall Siren",
    "manufacturer": "acme", "model": "S1", "firmware": "1.0",
    "category": "actuator", "tags": ["alarm", "emergency"],
    "emergency_capable": true, "cert_tier": "basic",
    "sensors": [], "actuators": [{"id": "alarm", "type": "alarm",
                                  "description": "audible alarm"}]}'

# 2. An AI expresses a GOAL — not a command, and it names no device
curl -X POST http://127.0.0.1:47200/v1/intent/async \
  -H "Authorization: Bearer $DOSYNC_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"intent": "ensure_safety", "urgency": "emergency", "context": {}}'

# 3. Ask WHY those devices were chosen
curl http://127.0.0.1:47200/v1/intents/ensure_safety/explain \
  -H "Authorization: Bearer $DOSYNC_TOKEN"

# 4. Read the tamper-evident record of what happened
curl "http://127.0.0.1:47200/v1/audit?limit=10" \
  -H "Authorization: Bearer $DOSYNC_TOKEN"
```

<details>
<summary><b>The same calls in PowerShell</b></summary>

`curl` in PowerShell is an alias for `Invoke-WebRequest`, and quoting JSON for
`curl.exe` is impractical — a clean Windows machine following the commands above
verbatim gets `JSON decode error`, because PowerShell passes the escaped quotes
through literally. Build the body as an object instead:

```powershell
$body = @{
  device_id = "siren-hall"; device_name = "Hall Siren"
  manufacturer = "acme"; model = "S1"; firmware = "1.0"
  category = "actuator"; tags = @("alarm","emergency")
  emergency_capable = $true; cert_tier = "basic"
  capabilities = @{ sensors = @(); actuators = @(@{ id="alarm"; type="alarm"; description="sound" }) }
} | ConvertTo-Json -Depth 6

$headers = @{ Authorization = "Bearer $env:DOSYNC_TOKEN" }

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47200/v1/devices/register `
  -Headers $headers -ContentType "application/json" -Body $body

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47200/v1/intent/async `
  -Headers $headers -ContentType "application/json" `
  -Body (@{ intent = "ensure_safety"; urgency = "emergency"; context = @{} } | ConvertTo-Json)

Invoke-RestMethod -Uri http://127.0.0.1:47200/v1/intents/ensure_safety/explain -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:47200/v1/audit?limit=10" -Headers $headers
```

</details>
```

Step 3 is the one worth pausing on: the hub tells you which devices it
evaluated, which it included, and the score breakdown behind each decision.
Step 4 is the other: every action leaves a SHA-256-chained entry, so what the
system did is provable after the fact rather than merely logged.

**Discovery works out of the box.** The library that finds Bluetooth devices
ships in the core install, and the BLE adapter registers itself when it is
available — because discovery is how you learn what you have. Requiring an extra
first would be a circle: nobody installs a Bluetooth library before knowing they
own Bluetooth devices, and nobody can find out without it. A hub with no radio
loses nothing — the scan reports the transport as unsearchable rather than
failing — and `pip uninstall bleak` or `DOSYNC_BLE_ENABLED=false` removes it.

**On the adapters that ship with DoSync.** They come in three kinds, visible at
`GET /v1/adapters`. **Ecosystem** adapters implement open standards — MQTT,
Matter, BLE, MAVLink, and the Home Assistant bridge, which is the widest door of
all: anything HA already integrates, DoSync can reach. **Reference** adapters
(WiZ, Shelly) implement one vendor's product and ship as worked examples of how
an adapter is written — not as endorsement, partnership, or a promise to track
anyone's firmware. **They register only when their vendor library is installed,
and say nothing when it is not.** A hub whose operator owns nothing from that
vendor should never be told to install anything: the protocol does not presume
your hardware, and a reference adapter that nagged for `pip install pywizlight`
on every start was presuming it. If you do register a device that names an
adapter the hub cannot load, the startup check names that device and tells you
its actions will be simulated. **Infrastructure** is notifications.

**If your device is not covered, describe it in a file.** A declarative adapter
is YAML or JSON — no code, no release of DoSync to wait for:

```yaml
device:
  id: light-hallway
  name: Hallway light
  tags: [light, energy]        # how intents find it
  emergency_capable: true      # whether an emergency may use it

transport:
  kind: http
  base_url: http://192.168.1.40

actions:
  turn_on:
    type: turn_on              # what it MEANS to DoSync, not just its name
    request: { method: POST, path: /light/on }
```

Drop it in `declarative/` (or set `DOSYNC_DECLARATIVE_DIR`) and restart.

**You do not have to write the first one.** Six worked examples ship with the
package — a light, an air conditioner, a 3D printer, a television, a floor's
lighting controller and an industrial conveyor over MQTT — chosen so one of them
probably resembles what you have:

```bash
dosync-manage examples        # copies them into declarative/, ready to edit
```

They are also readable in the repository at
[`examples/declarative/`](examples/declarative/).

The `type` on each action is the part that matters. A file that only said "POST
/on turns it on" would let DoSync switch the device and leave it invisible to
everything else: no intent could select it, no policy could name it, an
emergency would pass it by.

**What a declarative adapter cannot do**, stated plainly: it speaks HTTP. It
cannot speak Zigbee, Z-Wave, BLE pairing, an OPC-UA session, or anything needing
a handshake, session state or a vendor SDK. Those need a code adapter — an
ecosystem one here, or a third-party package. This format covers most simple
devices and almost no complex ones.

**If it needs real code — pairing, a session, a vendor SDK — publish a package.**
DoSync discovers adapters advertised by anything installed alongside it:

```toml
# in the vendor's pyproject.toml
[project.entry-points."dosync.adapters"]
daikin = "dosync_adapter_daikin:DaikinAdapter"
```

The operator runs `pip install dosync-adapter-daikin` and the hub finds it. No
pull request here, and no promise from this project to maintain code for
hardware it has never seen — the publisher answers for their own adapter.

**If DoSync itself was installed with `pipx`, a plain `pip install` lands in
the wrong environment.** `pipx` deliberately isolates the hub in its own
virtual environment, separate from the system Python, so a vendor library
installed the ordinary way is invisible to it — the hub stays silent about
this exactly as it does about an uninstalled one, because from its side
there is no difference. Install into the hub's own environment instead:

```bash
pipx inject dosync dosync-adapter-daikin
```

This is not specific to any one vendor's product: it is how you add *any*
optional dependency — a control library, a third-party adapter package, a
protocol SDK — after installing DoSync with `pipx`. Whichever hardware you
own, `pipx inject dosync <package>` is the command, not `pip install`.

A third-party adapter runs inside the hub with the hub's permissions, so the hub
says so: it is logged at WARNING when loaded, recorded in the audit chain, and
reported as `kind: third_party` at `/v1/adapters` regardless of what the plugin
declares about itself. Where code came from is not the code's to assert.

DoSync does not download an adapter for you: the
protocol's whole argument is that nothing actuates hardware without a policy and
a record, and fetching executable code from the internet would put the largest
possible hole exactly there. Instead, an operator writes a declarative adapter
for HTTP/MQTT/Modbus devices, or installs a third-party package deliberately.

Install only the CONTROL adapters you need — those follow the opposite rule,
since you already know which hardware you own:

```bash
pip install 'dosync[wiz]'      # Philips WiZ bulbs
pip install 'dosync[ha]'       # Home Assistant bridge
pip install 'dosync[mqtt]'     # MQTT devices
pip install 'dosync[all]'      # everything
```

### Access: a password you choose, or none at all

The hub prints a token on first start and stores only a hash of it, so it cannot
show you that one again. You are not stuck with it.

**From the dashboard** (the ⚙ button, once connected): set a password of your
choosing, or turn the token requirement off entirely. No shell, no unit file.

**From a terminal**, if you prefer:

```bash
# Choose your own — for a person who has to type it
dosync-manage keys create --token "my-house-2026-kitchen" --label dashboard

# Let it generate one — for a program that will store it
dosync-manage keys create --label my-integration

# Start with no authentication at all
DOSYNC_AUTH=false dosync-hub
```

**Running without a token is a legitimate choice, not a trap door.** On a home
network, behind a router, with no port forwarding, a token protects against
nobody who is not already inside your house. It is the wrong default for a
clinic and an unnecessary obstacle for a workshop, so DoSync offers it plainly
rather than assuming everyone shares one threat model. It is not suitable for
any hub reachable from outside its own network.

Two things worth knowing:

- **`DOSYNC_AUTH` in the environment wins.** If it is set in your service
  configuration, the dashboard will tell you so and refuse to override it — a
  click in a browser should not quietly undo what the machine was told to do.
- **Changing access is recorded.** Setting a password or turning authentication
  off appends to the audit chain, so "when did this hub become open, and who did
  it" has an answer. The token value itself is never written there.

A chosen password must be at least 12 characters, and a passphrase of several
words is better than a short clever string: a bearer token is checked with no
rate limiting and no lockout, so it is guessed offline at full speed.

Existing keys: `dosync-manage keys list` (previews only — they are hashed),
`keys revoke <preview>`, `keys reset`.

### Hardware that cannot do TLS

A sensor running a year on a coin cell cannot perform a TLS handshake — it costs
more battery than a month of operation. Such a device can still report liveness,
signed rather than encrypted:

```bash
DOSYNC_LIGHTWEIGHT_HEARTBEAT=true dosync-hub
```

`POST /v1/heartbeat/signed` accepts a heartbeat authenticated by HMAC over the
device's provisioning token. It is **off by default**, and it is worth knowing
exactly what it trades before turning it on: the channel provides message
authenticity and replay resistance, and **no confidentiality** — the device id,
timestamp and report travel readable. Devices using it are marked
`report_channel: signed_plaintext` so they are distinguishable from ones on mTLS.

That trade is defensible for a heartbeat and would not be for an action: a
heartbeat is positive signal only, so a forged one cannot switch anything on.
The attack it invites is replay — repeating a captured message to keep a failed
device reporting healthy — and that is closed. See spec §7.10 and
[the threat model](docs/AUDIT-THREAT-MODEL.md).

### TLS, and why your browser says "Not secure"

`bash setup_pki.sh` creates a private certificate authority in `certs/` and
issues the hub a certificate from it. Start the hub with those files and traffic
is encrypted:

```bash
dosync-hub --host 0.0.0.0 &      # or with uvicorn's --ssl-keyfile / --ssl-certfile
```

Your browser will then show **"Not secure"** with `https` struck through. This
is expected and it does **not** mean the connection is unencrypted. It means the
browser does not recognise the authority that signed the certificate — which is
you. A public CA cannot issue a certificate for `192.168.x.x`, so a hub on a
private network is always in this position.

Two honest options:

**Accept the warning.** Click through it. The connection is encrypted; what is
missing is a third party vouching that the server is who it claims. On your own
LAN, where you set up the hub yourself, that is a much smaller gap than it looks.

**Trust your own CA, and the warning goes away** — on the machines you choose:

```bash
# macOS
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain certs/ca.crt

# Linux (Debian/Ubuntu)
sudo cp certs/ca.crt /usr/local/share/ca-certificates/dosync-ca.crt
sudo update-ca-certificates

# Windows (PowerShell, as Administrator)
Import-Certificate -FilePath ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```

Copy `certs/ca.crt` from the hub first — it is the only file you need, and it
contains no secret. The hub's private key (`certs/hub.key`) never leaves the
hub.

**What the warning does mean.** If you see it on a hub you did not set up, or on
a network you do not control, do not click through — that is exactly the case
the warning exists for.

### Docker

```bash
docker run -p 47200:47200 dosync/hub        # published image
# or, from a clone:
docker compose up
```

### From source (development)

```bash
git clone https://github.com/giulianireg-spec/dosync-protocol
cd dosync-protocol
python3 -m venv venv && source venv/bin/activate
pip install -e '.[dev]'
pytest                                  # runs the full suite
dosync-hub --reload
```

The hub stops when the terminal closes. To keep it running across reboots, see
[Keeping the hub running](#keeping-the-hub-running).

---

## Keeping the hub running

Everything above starts a hub in a terminal, and it stops when that terminal
closes. Nothing in this project has explained how to change that — while the
repository has shipped a systemd unit at its root for months and this page
refers to *"your service"* as though you already had one. That gap is the
reason this section exists.

DoSync does not implement supervision on any platform. It delegates: to systemd
on Linux, to the task scheduler on Windows. What differs between them is how
much you have to write, not whether it works.

**Say once what is at stake:** a hub that governs physical actions and does not
come back after a power cut is a deployment decision with consequences. If a
lock, an alarm or a machine depends on it, this is not optional.

### Linux — systemd

`dosync.service` in the repository root is a template. Adjust the paths, then:

```bash
sudo cp dosync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dosync
systemctl status dosync
```

**Keep secrets out of the unit.** It is a file people copy, and this one carried
a real Home Assistant token in a public repository until August 2026. Put them
in a drop-in that is not tracked:

```bash
sudo install -d -m 700 /etc/systemd/system/dosync.service.d
sudo tee /etc/systemd/system/dosync.service.d/local.conf >/dev/null <<'EOF'
[Service]
Environment="HA_TOKEN=your-token-here"
EOF
sudo chmod 600 /etc/systemd/system/dosync.service.d/local.conf
sudo systemctl daemon-reload && sudo systemctl restart dosync
```

systemd merges drop-ins over the unit, so the template never needs editing.

### Windows — Task Scheduler

*Verified on Windows 11 ARM64 in a virtual machine, Python 3.14, installed with
`pipx`.*

Run PowerShell **as administrator**:

```powershell
$exe = (Get-Command dosync-hub).Source
$db  = "$env:USERPROFILE\.local\state\dosync\dosync.db"

$action = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c set DOSYNC_DB=$db && `"$exe`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
  -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "DoSync Hub" -Action $action `
  -Trigger $trigger -Principal $principal -Settings $settings
```

`-AtStartup` with `SYSTEM` means the hub comes back after a reboot without
anyone logging in — the closest equivalent to systemd. `-RestartCount` covers a
process that dies.

**`DOSYNC_DB` is not optional, and leaving it out fails silently.** A scheduled
task does not inherit your environment, so a hub started this way writes to
`C:\Windows\System32\config\systemprofile\` — a different database from the
one you used by hand. It starts perfectly, reports `devices: 0`, and looks like
your inventory was lost. Setting `DOSYNC_DB` points both at the same file;
running it by hand afterwards reads what the scheduled task wrote, with no
permission conflict.

Check it:

```powershell
curl.exe -s http://localhost:47200/v1/status | findstr db_path
Get-ScheduledTask -TaskName "DoSync Hub" | Get-ScheduledTaskInfo
```

`LastTaskResult: 267009` is `0x41301` — *the task is currently running*. That is
the healthy value here; `0` would mean it exited.

**NSSM** installs the hub as a real Windows service and is what a server
deployment would use. It is third-party software this project does not ship or
test, so it is named rather than recommended.

---

## What's built today

| Component | Status |
|---|---|
| REST API (40+ endpoints) | ✅ |
| WebSocket real-time events | ✅ |
| Web dashboard | ✅ |
| API key authentication + SHA-256 audit log | ✅ |
| Capability-based resolver | ✅ |
| Certification CLI — Standard 33/33 · Emergency 44/44 (signed reports) | ✅ |
| Philips WiZ adapter (UDP local) | ✅ |
| Home Assistant bridge (10 domains) | ✅ |
| Native MCP server (Claude, ChatGPT, any LLM) | ✅ |
| GPIO adapter — Raspberry Pi 5 (PIR + DHT22) | ✅ |
| SMS notifications via Twilio | ✅ code (requires an active Twilio plan) |
| MQTT transport adapter (Mosquitto) | ✅ |
| Shelly adapter (HTTP local, Gen1 + Gen2) | ✅ code, not hardware-tested |
| Matter adapter (via HA bridge / python-matter-server) | ✅ code, not hardware-tested |
| External Resolver Protocol (HTTP wire format) | ✅ |
| SQLite persistence (survives restarts) | ✅ |
| CI pipeline (GitHub Actions) | ✅ |
| Multi-hub assisted failover (Phase A — operator-in-the-loop) | ✅ |
| Long-running operations + telemetry reconciliation (state machine) | ✅ |
| Drone / MAVLink adapter — full AI→intent→mission loop in ArduPilot SITL | ✅ software (physical flight pending) |

---

## MQTT transport

DoSync supports MQTT as a Layer 1 transport for devices that can't use HTTP. Requires Mosquitto and proper authentication. See [config/mosquitto-secure.conf](config/mosquitto-secure.conf) for secure setup.

```bash
# Enable MQTT in the hub service
Environment="DOSYNC_MQTT_BROKER=localhost"
Environment="DOSYNC_MQTT_USER=dosync-hub"
Environment="DOSYNC_MQTT_PASSWORD=<password>"
Environment="DOSYNC_MQTT_SECRET=<registration-secret>"
```

---

## Certification

Self-certifiable with the CLI:

```bash
python3 certify.py --host <hub-ip> --port 47200 --tier standard
# Output: dosync-cert-standard-*.json
```

| Tier | Tests | What it validates |
|---|---|---|
| **Basic** | 10 | Connectivity, auth, device manifest |
| **Standard** | 33 | Protocol conformance, events, health, version headers |
| **Emergency** | 44 | Everything in Standard + emergency override, policy engine, audit log integrity |

---

## Implementations

| Language | Location | Author | Certification |
|---|---|---|---|
| Python (reference) | `server.py` | this project | Standard 33/33 · Emergency 44/44 ✅ |
| Node.js (companion) | [giulianireg-spec/dosync-node](https://github.com/giulianireg-spec/dosync-node) | this project | Standard 33/33, against the v0.3 suite — re-validation against the current 56-test suite pending |

The Node.js implementation is a **companion** port that validates the protocol
is implementable in a second language against the same certification suite —
both share the same author. A genuinely **independent** implementation
(different author or organization) is a tracked milestone for v1.0: a protocol
needs multiple independent implementations to become a standard. See the
[vision and scope](docs/VISION.md).

---

## Works with Home Assistant — a layer on top, not a replacement

Home Assistant already solved the hardest problem: talking to thousands of devices, and since 2025 it ships an MCP server so an AI can control them directly. DoSync doesn't reinvent that — it reads devices from HA through a bridge already in the repo and adds **one thing**: it turns a semantic goal (`ensure_safety`, `away_mode`) into a coordinated, **auditable** set of actions across *any* source (HA, WiZ, GPIO, MQTT, BLE).

The honest version: for everyday automation ("porch light when I get home") you **don't need DoSync** — HA's automations and its MCP cover that completely. DoSync earns its place only when **coordination and traceability matter at once** — e.g. a fall-response that unlocks the door, lights the house, and messages family, with a tamper-evident record of exactly what fired and when. Full reasoning: [Home Assistant Already Talks to Your Devices. So What Would DoSync Add?](https://dev.to/giulianiregspec/home-assistant-already-talks-to-your-devices-so-what-would-dosync-add-1iei)

---

## Beyond the home

Nothing in DoSync assumes a house — the same 5-layer stack coordinates physical systems anywhere an AI needs to act: retail cold-chain, hotels, factory peripherals (alongside certified safety systems, never replacing them).

The proof: we took it to the hardest device, an **autonomous drone**. From a single plain-language sentence, an AI model (Claude Haiku, via DoSync's MCP server) fired an `inspect_area` intent and the drone flew the full mission in ArduPilot SITL — every step confirmed by real telemetry. When the AI guessed coordinates 11,000 km away, the supervisor didn't fake success: it waited for a confirmed arrival, none came, and it aborted with a clear diagnosis. **The AI can be wrong; the protocol doesn't have to be.** [Full build log](https://dev.to/giulianiregspec/i-gave-an-ai-one-sentence-a-drone-flew-the-mission-and-when-the-ai-guessed-wrong-the-system-2h3m) · *(validated in SITL; physical-hardware flight is the next step, not a claim made today.)*

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow, including the CI pipeline that runs on every push.

---

## Specification

- [spec/DoSync-SPEC-v0.1.md](spec/DoSync-SPEC-v0.1.md) — full protocol specification
- [spec/RESOLVER-SPEC-v0.3.md](spec/RESOLVER-SPEC-v0.3.md) — resolver interface + external resolver protocol
- [DESIGN-PRINCIPLES.md](DESIGN-PRINCIPLES.md) — architectural decisions and rationale
- [COMPATIBILITY.md](docs/COMPATIBILITY.md) — backward compatibility guarantees

---

## License

Apache 2.0 — free to implement, free to extend, no royalties.

---

*DoSync Protocol · © 2026 Rodrigo Giuliani · rgiuliani@dosync.dev*
