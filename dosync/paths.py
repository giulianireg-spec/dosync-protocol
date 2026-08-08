"""Where a DoSync deployment keeps its configuration and its state.

Found while preparing to reflash the reference deployment: its configuration
lived in nine places, four of which the author did not remember existed, and
three of those were **inside a git clone**. A `git clean -fdx` — a command people
run to tidy a repository — would have destroyed a 42,000-entry audit chain and
the CA's private key.

That is worth stating plainly, because it is a contradiction: this protocol
argues that its evidence survives someone with root access, and until now it did
not survive someone tidying up. The sophisticated threat was covered and the
trivial one was not.

Two modes, because there are two ways to install
------------------------------------------------
`pipx install dosync` runs as an ordinary user, who cannot write to `/etc` or
`/var/lib`. A systemd service running as root can and should. Rather than pick
one and break the other, paths cascade: the user location first, the system
location second. The same binary serves both, and neither needs configuration to
work.

    Configuration   ~/.config/dosync/            →  /etc/dosync/
    State           ~/.local/state/dosync/       →  /var/lib/dosync/
    PKI             <state>/certs/  (mode 0700)

The split follows the XDG categories, and the deciding question is the one that
specification asks: *is this datum unique to this machine?* An audit chain and a
private CA are — they are state. Policies and declarative device files are not;
they are edited by hand and can be copied between machines, so they are
configuration.

Compatibility is not optional here
----------------------------------
A deployment exists with 42,000 entries and certificates in use. If the hub
stopped looking where that deployment keeps its data, it would come up tomorrow
with an empty chain and a fresh CA — and the history this project spent weeks
protecting would be orphaned in a directory nobody looks at.

So: an explicit variable always wins, an existing database in the working
directory keeps being used with a warning explaining how to move it, and finding
data in two places is an error rather than a choice. Choosing wrong there would
mean writing to one chain while auditing another.
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("dosync.paths")

#: Set by the packaging or the operator to force system mode. Without it, the
#: mode is inferred: root implies a system service.
MODE_ENV = "DOSYNC_INSTALL_MODE"


def _running_as_root() -> bool:
    try:
        return os.geteuid() == 0
    except AttributeError:      # pragma: no cover - non-POSIX
        return False


def install_mode() -> str:
    """`"system"` or `"user"`.

    Inferred from the effective user rather than declared, because the common
    cases decide themselves: a systemd unit runs as root, `pipx` does not.
    `DOSYNC_INSTALL_MODE` overrides for the cases that do not — a service
    running under a dedicated unprivileged account, for instance.
    """
    declared = os.environ.get(MODE_ENV, "").strip().lower()
    if declared in ("system", "user"):
        return declared
    return "system" if _running_as_root() else "user"


def config_dirs() -> list[Path]:
    """Where configuration is looked for, in order of preference."""
    if install_mode() == "system":
        return [Path("/etc/dosync")]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    home = Path(xdg) if xdg else Path.home() / ".config"
    return [home / "dosync", Path("/etc/dosync")]


def state_dir() -> Path:
    """Where this deployment's own data lives — chain, PKI, evidence."""
    if install_mode() == "system":
        return Path("/var/lib/dosync")
    xdg = os.environ.get("XDG_STATE_HOME")
    home = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return home / "dosync"


def _legacy(name: str) -> Path:
    """The pre-0.4.3 location: relative to the working directory."""
    return Path.cwd() / name


def resolve_state(name: str, env_var: str = None, create: bool = False) -> Path:
    """Resolve a state path, honouring an existing deployment above all else.

    Order:
      1. `env_var`, if set — an operator who said where wins, always.
      2. The legacy path, **if it already has data**. A running deployment does
         not lose its chain to an upgrade.
      3. The state directory.

    Finding data in both the legacy path and the state directory raises, rather
    than picking. Picking wrong means writing to one chain and auditing another,
    and the operator is the only one who knows which is current.
    """
    if env_var:
        explicit = os.environ.get(env_var)
        if explicit:
            return Path(explicit)

    legacy, modern = _legacy(name), state_dir() / name
    legacy_has = legacy.exists() and (legacy.is_file() or any(legacy.iterdir()))
    modern_has = modern.exists() and (modern.is_file() or any(modern.iterdir()))

    if legacy_has and modern_has:
        raise RuntimeError(
            f"'{name}' exists in two places and DoSync will not guess which is "
            f"current:\n    {legacy}\n    {modern}\n"
            f"Keep one, or set {env_var or 'the matching DOSYNC_* variable'} to "
            f"say which. Choosing wrong here would mean writing to one and "
            f"auditing the other.")

    if legacy_has:
        log.warning(
            "Using %s from the working directory. Since 0.4.3 this belongs in "
            "%s — data inside a source tree is one `git clean` from being gone. "
            "Move it there, or set %s to keep it where it is.",
            name, modern, env_var or "the matching variable")
        return legacy

    if create:
        modern.parent.mkdir(parents=True, exist_ok=True)
    return modern


def resolve_config(name: str, env_var: str = None) -> Path | None:
    """Find a configuration file, or None if there is none.

    Returns None rather than a non-existent path: absent configuration is a
    normal state for this protocol — a hub with no deployment policies is
    conforming — and a caller should not have to distinguish "missing file" from
    "file that happens not to exist yet".
    """
    if env_var:
        explicit = os.environ.get(env_var)
        if explicit:
            return Path(explicit)

    legacy = _legacy(name)
    if legacy.exists():
        return legacy
    for d in config_dirs():
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def resolve_config_dir(name: str, env_var: str = None) -> Path:
    """A configuration DIRECTORY — declarative device files, for instance.

    Unlike `resolve_config`, always returns a path: the caller iterates it and
    an empty or absent directory is a legitimate answer meaning "no declarative
    devices", not an error.
    """
    if env_var:
        explicit = os.environ.get(env_var)
        if explicit:
            return Path(explicit)

    legacy = _legacy(name)
    if legacy.is_dir() and any(legacy.iterdir()):
        return legacy
    for d in config_dirs():
        candidate = d / name
        if candidate.is_dir():
            return candidate
    return config_dirs()[0] / name


def certs_dir() -> Path:
    """The PKI directory, created 0700 when this call creates it.

    A private key in a directory with whatever permissions it inherited is the
    kind of detail that is invisible until an audit asks about it.
    """
    explicit = os.environ.get("DOSYNC_CERTS_DIR")
    if explicit:
        return Path(explicit)

    legacy = _legacy("certs")
    if legacy.exists() and any(legacy.iterdir()):
        return legacy

    d = state_dir() / "certs"
    if not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)
        except OSError as e:        # pragma: no cover - exotic filesystems
            log.warning("Could not restrict permissions on %s: %s", d, e)
    return d


def describe() -> dict:
    """Every path this deployment resolved, for the startup log and /v1/status.

    Reported because an operator editing `/etc/dosync/policies.json` while the
    hub reads `~/.config/dosync/policies.json` believes they are protected by a
    policy the hub never loaded. Silent divergence between what someone edits
    and what runs is exactly the failure this project keeps finding.
    """
    return {
        "mode": install_mode(),
        "config_dirs": [str(d) for d in config_dirs()],
        "state_dir": str(state_dir()),
        "certs_dir": str(certs_dir()),
    }
