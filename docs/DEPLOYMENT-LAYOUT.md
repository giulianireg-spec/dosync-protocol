# Where a DoSync deployment lives

**This is not part of the protocol specification, and deliberately so.** The spec
defines endpoints, event types and semantics; mandating `/var/lib` would tie an
implementation in Rust on FreeBSD to Linux conventions, and a second independent
implementation is the thing this project most needs. What follows is how the
reference implementation lays out a deployment, and what any implementation
should think about.

## Two modes

`pipx install dosync` runs as an ordinary user who cannot write to `/etc` or
`/var/lib`. A systemd unit running as root can and should. Paths cascade rather
than forcing a choice:

| | User install | System service |
|---|---|---|
| **Configuration** | `~/.config/dosync/` | `/etc/dosync/` |
| **State** | `~/.local/state/dosync/` | `/var/lib/dosync/` |
| **PKI** | `<state>/certs/` (mode 0700) | `/etc/dosync/certs/` (0700) |

The mode is inferred from the effective user; `DOSYNC_INSTALL_MODE=system|user`
overrides it, for a service running under a dedicated unprivileged account.

## What goes where, and why

The split follows the XDG categories, and the deciding question is the one that
specification asks: **is this datum unique to this machine?**

**Configuration** — edited by hand, portable between machines, describes what
*should* happen:

- `policies.json` — what this deployment forbids
- `declarative/` — devices described in YAML or JSON

**State** — generated here, not portable, and its value depends on staying put:

- `dosync.db` — the audit chain
- `certs/` — the CA and its private key
- `checkpoints/` — signed evidence
- `audit-segments/` — archived chain segments

An audit chain is state, not data: copying it to another machine produces a
chain that attests to things that did not happen there.

## Compatibility

Nothing here breaks an existing deployment.

1. **An explicit variable always wins.** `DOSYNC_DB`, `DOSYNC_POLICIES`,
   `DOSYNC_CERTS_DIR` and the rest behave exactly as before.
2. **An existing database in the working directory keeps being used**, with a
   warning saying where it belongs and how to move it. A hub coming up with an
   empty chain after an upgrade would lose precisely the history this protocol
   exists to protect.
3. **Data in two places is an error, not a choice.** The hub stops and names
   both paths. Choosing wrong means writing to one chain while auditing the
   other, and only the operator knows which is current.

## Moving an existing deployment

```bash
sudo systemctl stop dosync
sudo mkdir -p /var/lib/dosync /etc/dosync
sudo mv ~/dosync-protocol/dosync.db        /var/lib/dosync/
sudo mv ~/dosync-protocol/certs            /var/lib/dosync/
sudo mv ~/dosync-protocol/checkpoints      /var/lib/dosync/ 2>/dev/null
sudo mv ~/dosync-protocol/audit-segments   /var/lib/dosync/ 2>/dev/null
sudo mv ~/dosync-protocol/declarative      /etc/dosync/     2>/dev/null
sudo chmod 700 /var/lib/dosync/certs
sudo systemctl start dosync
```

Then check the chain survived the move — the point of doing this at all:

```bash
curl -sk https://localhost:47200/v1/status --cacert /var/lib/dosync/certs/ca.crt \
  -H "Authorization: Bearer $TOKEN" | grep -o '"audit_integrity":[a-z]*'
```

## Backing up a deployment

With the layout above, the answer is two directories:

```bash
tar czf dosync-backup.tgz /etc/dosync /var/lib/dosync
```

That was the whole reason for this change. Before it, backing up the reference
deployment meant knowing about nine locations — four of which its own author had
forgotten, and three of which sat inside a git clone where a routine
`git clean -fdx` would have destroyed a 42,000-entry chain and the CA's private
key.

**Also back up the systemd drop-ins**, which are configuration and live
elsewhere by systemd's own design:

```bash
tar czf dosync-units.tgz /etc/systemd/system/dosync.service.d/
```

## What the hub reports

At startup and at `GET /v1/status`, a hub states which paths it resolved. An
operator editing `/etc/dosync/policies.json` while the hub reads
`~/.config/dosync/policies.json` would otherwise believe they are protected by a
policy the hub never loaded — silent divergence between what is edited and what
runs is the failure mode this project keeps finding in itself.
