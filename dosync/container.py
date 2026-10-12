"""`dosync-container`: how the hub starts inside its image -- plain Docker, or as
a Home Assistant add-on. One image for both, so what a Home Assistant user runs
is what a `docker run` runs.

What it does, before handing over to `dosync-hub`:

- Keeps everything the hub writes under the data volume (`/data`): database,
  certificates, state. A container recreated from a new image keeps its devices,
  its keys and its audit chain.
- As a Home Assistant add-on (`/data/options.json` exists): turns the add-on's
  options into the hub's settings, and, when the bridge is on, connects to Home
  Assistant through the Supervisor with the token the Supervisor gives the add-on
  -- the person does not create a long-lived token.
- Listens on all interfaces (a container must) with authentication ON unless
  told otherwise: on first start the hub prints its token in the log
  (`docker logs dosync`, or the add-on's Log tab).

Environment the person set explicitly always wins over options and defaults.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SUPERVISOR_CORE = "http://supervisor/core"

# add-on option -> (environment variable, how to render the value)
_OPTIONS = {
    "auth_required":            ("DOSYNC_AUTH", lambda v: "true" if v else "false"),
    "operator_token":           ("DOSYNC_OPERATOR_TOKEN", str),
    "configuration_credential": ("DOSYNC_CONFIGURATION_CREDENTIAL", str),
    "direct_control":           ("DOSYNC_DIRECT_CONTROL", str),
    "confirmation_timeout":     ("DOSYNC_CONFIRMATION_TIMEOUT", str),
}


def settings(env: dict, data: Path) -> tuple[dict, list[str]]:
    """The environment the hub should start with, and notes for the log.
    Pure: reads `env` and the files under `data`, returns a new mapping."""
    out = dict(env)
    notes: list[str] = []

    def default(key, value):
        if not out.get(key):
            out[key] = value

    # Root in a container is not a system install: keep state under the volume.
    default("DOSYNC_INSTALL_MODE", "user")
    default("DOSYNC_DB", str(data / "dosync.db"))
    default("DOSYNC_CERTS_DIR", str(data / "certs"))
    default("XDG_STATE_HOME", str(data / "state"))
    default("XDG_CONFIG_HOME", str(data / "config"))
    if (data / "policies.json").is_file():
        default("DOSYNC_POLICIES", str(data / "policies.json"))
        notes.append(f"deployment policies: {data / 'policies.json'}")

    options_file = data / "options.json"
    if options_file.is_file():
        try:
            options = json.loads(options_file.read_text(encoding="utf-8") or "{}")
        except ValueError as e:
            raise SystemExit(f"{options_file} is not valid JSON: {e}")
        notes.append("running as a Home Assistant add-on")
        for name, (var, render) in _OPTIONS.items():
            value = options.get(name)
            if value in (None, ""):
                continue
            if var in env and env[var] != "":
                continue                      # set explicitly: it wins
            out[var] = render(value)
        if options.get("home_assistant_bridge", True) and env.get("SUPERVISOR_TOKEN"):
            default("HA_URL", SUPERVISOR_CORE)
            default("HA_TOKEN", env["SUPERVISOR_TOKEN"])
            notes.append("Home Assistant bridge: through the Supervisor")
        if options.get("log_level"):
            default("DOSYNC_LOG_LEVEL", str(options["log_level"]))
    return out, notes


def main(argv=None) -> None:
    data = Path(os.environ.get("DOSYNC_CONTAINER_DATA", "/data"))
    data.mkdir(parents=True, exist_ok=True)
    env, notes = settings(dict(os.environ), data)
    for n in notes:
        print(f"dosync-container: {n}", flush=True)
    port = env.get("DOSYNC_PORT", "47200")
    args = ["dosync-hub", "--host", "0.0.0.0", "--port", port,
            "--log-level", env.get("DOSYNC_LOG_LEVEL", "info")]
    os.environ.clear()
    os.environ.update(env)
    from dosync.cli import hub
    hub(args[1:] + list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    main()
