"""Extract every `DOSYNC_*` setting the code actually reads.

Written because a hand-maintained table would be the fifth thing in this project
to hold one fact in two places and drift — after the version in four files,
DOSYNC_DB vs DOSYNC_DB_PATH, the auth setting, and requirements.txt against
pyproject.toml. The backlog entry that ASKED for this document mistyped a
variable name while being written, which is the argument in miniature: if the
author gets one wrong summarising his own work, an operator reading prose has no
chance.

So the reference is derived, and a test fails when the checked-in file no longer
matches what the code reads.

    python3 -m dosync.config_reference --write   # regenerate docs/CONFIGURATION.md
    python3 -m dosync.config_reference --check   # exit 1 if it is stale
"""
import argparse
import re
import sys
from pathlib import Path

#: `os.environ.get("DOSYNC_X", "default")` and `os.environ["DOSYNC_X"]`.
_GET = re.compile(
    r'os\.environ\.get\(\s*"(DOSYNC_[A-Z0-9_]+)"\s*(?:,\s*("[^"]*"|\'[^\']*\'|[^)]+?))?\s*\)')
_INDEX = re.compile(r'os\.environ\[\s*"(DOSYNC_[A-Z0-9_]+)"\s*\]')

#: Grouping is by prefix, which is not cosmetic: an operator arrives with a
#: question ("how do I stop it warning about checkpoints?"), not with a variable
#: name, and an alphabetical list of 48 answers no question at all.
GROUPS = [
    ("Running the hub", ("DOSYNC_HOST", "DOSYNC_PORT", "DOSYNC_DB", "DOSYNC_DB_PATH",
                         "DOSYNC_CERTIFY", "DOSYNC_HUB_ROLE", "DOSYNC_HUB_URL",
                         "DOSYNC_PRIMARY_URL", "DOSYNC_STATUS_SCOPE")),
    ("Access", ("DOSYNC_AUTH", "DOSYNC_TOKEN", "DOSYNC_DEVICE_AUTH",
                "DOSYNC_DEMO_TOKEN", "DOSYNC_CERTS_DIR", "DOSYNC_CA_CERT",
                "DOSYNC_CERT_KEY")),
    ("Audit and evidence", ("DOSYNC_ASSURANCE", "DOSYNC_CHECKPOINT_INTERVAL",
                            "DOSYNC_CHECKPOINT_DIR", "DOSYNC_CHECKPOINT_EXPORT_DIR",
                            "DOSYNC_CHECKPOINT_EXPORT_EXTERNAL",
                            "DOSYNC_AUDIT_HEAD_EVERY", "DOSYNC_AUDIT_MAX_LIVE",
                            "DOSYNC_ARCHIVE_DIR")),
    ("Devices and adapters", ("DOSYNC_DECLARATIVE_DIR", "DOSYNC_BLE_ENABLED",
                              "DOSYNC_MAVLINK_ENABLED", "DOSYNC_HA_EXCLUDE_ENTITIES",
                              "DOSYNC_HA_IMPORT_HOUSEKEEPING", "DOSYNC_MQTT_BROKER",
                              "DOSYNC_MQTT_PORT", "DOSYNC_MQTT_USER",
                              "DOSYNC_MQTT_PASSWORD", "DOSYNC_MQTT_PREFIX",
                              "DOSYNC_MQTT_QOS", "DOSYNC_MQTT_SECRET")),
    ("Behaviour under load and failure",
     ("DOSYNC_INTENT_TIMEOUT", "DOSYNC_UNREACHABLE_TTL", "DOSYNC_FAILURE_THRESHOLD",
      "DOSYNC_STATE_REFRESH_INTERVAL", "DOSYNC_CLAIM_MIN_URGENCY",
      "DOSYNC_EMERGENCY_CLAIM_GRACE", "DOSYNC_EMERGENCY_CLAIM_MAX_HOLD",
      "DOSYNC_VALIDATE_PARAMS", "DOSYNC_POLICIES", "DOSYNC_EMERGENCY_CONTACT",
      "DOSYNC_RESOLVER_URL", "DOSYNC_RESOLVER_CA_CERT")),
]

HEADER = """# Configuration reference

Every `DOSYNC_*` setting the hub reads, with the default it uses when unset.

**Generated from the source.** Do not edit by hand — run
`python3 -m dosync.config_reference --write`. A test fails if this file and the
code disagree, because a hand-maintained table is how a project ends up with one
fact in two places, and this one has done that four times already.

**Nothing here is required.** A hub with no configuration at all starts, requires
a token, keeps an audit chain, checkpoints daily, and scans for devices. These
settings exist for deployments whose needs differ from that, not as a checklist.
"""


def scan(root: Path = None) -> dict[str, str]:
    """Every setting the package reads, mapped to its literal default."""
    root = root or Path(__file__).resolve().parent
    found: dict[str, str] = {}
    for f in sorted(root.rglob("*.py")):
        # This module's own docstring shows the pattern it looks for, so it
        # scanned itself and reported DOSYNC_X as a real setting. A generator
        # that hallucinates a variable is worse than a hand-written table.
        if f.name == "config_reference.py":
            continue
        src = f.read_text()
        for m in _GET.finditer(src):
            name = m.group(1)
            default = (m.group(2) or "").strip()
            if default.startswith(("'", '"')):
                default = default[1:-1]
            elif default:
                default = f"`{default}`"
            found.setdefault(name, default)
        for m in _INDEX.finditer(src):
            found.setdefault(m.group(1), "(no default — read directly)")
    return found


def render(found: dict[str, str]) -> str:
    out = [HEADER]
    placed = set()
    for title, names in GROUPS:
        rows = [(n, found[n]) for n in names if n in found]
        if not rows:
            continue
        placed.update(n for n, _ in rows)
        out.append(f"\n## {title}\n")
        out.append("| Setting | Default |")
        out.append("|---|---|")
        for name, default in rows:
            shown = f"`{default}`" if default and not default.startswith("`") \
                else (default or "_unset_")
            out.append(f"| `{name}` | {shown} |")

    # Anything the grouping missed still appears: a setting absent from this
    # file because nobody categorised it is exactly the one an operator cannot
    # find.
    rest = sorted(set(found) - placed)
    if rest:
        out.append("\n## Other\n")
        out.append("| Setting | Default |")
        out.append("|---|---|")
        for name in rest:
            d = found[name]
            out.append(f"| `{name}` | {f'`{d}`' if d else '_unset_'} |")
    return "\n".join(out) + "\n"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="dosync.config_reference")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    target = Path(__file__).resolve().parent.parent / "docs" / "CONFIGURATION.md"
    content = render(scan())

    if args.write:
        target.write_text(content)
        print(f"Wrote {target} ({len(scan())} settings)")
        return
    if args.check:
        if not target.exists() or target.read_text() != content:
            print("docs/CONFIGURATION.md is stale — run with --write")
            sys.exit(1)
        print("docs/CONFIGURATION.md is current")
        return
    print(content)


if __name__ == "__main__":
    main()
