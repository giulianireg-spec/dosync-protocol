"""Check that the specification describes what the implementation actually does.

Two lists that must not drift, kept honest by derivation rather than by care:

  * every event type appended to the audit chain, against the table in the spec
  * every `/v1/...` endpoint the server exposes, against the endpoints named there

Written after an audit found 32 audit event types in the code and a
specification with no table of event types at all — not seven rows missing, the
table absent. An operator whose hub records `device_quarantined` has to be able
to look it up, and a second implementation has to know what to emit. The audit
chain is only as useful as its legibility to somebody who did not write the
code, and that legibility is the product.

    python3 -m dosync.spec_coverage            # report
    python3 -m dosync.spec_coverage --check    # exit 1 if the spec is behind
"""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "spec" / "DoSync-SPEC-v0.1.md"

#: Emitted by tests or fixtures rather than by the hub, so a specification has
#: no reason to describe them.
NOT_PROTOCOL = {"audit_log_reset"}


def emitted_event_types(root: Path = None) -> set[str]:
    """Every `type` written to the audit chain by the package."""
    root = root or REPO / "dosync"
    found: set[str] = set()
    for f in sorted(root.rglob("*.py")):
        if f.name == "spec_coverage.py":
            continue
        src = f.read_text()
        for pattern in (r"audit_log\.append\(\s*\{(.*?)\}\s*\)",
                        r"_emit\(\s*\{(.*?)\}\s*\)"):
            for m in re.finditer(pattern, src, re.S):
                t = re.search(r'"type":\s*"([a-z_]+)"', m.group(1))
                if t:
                    found.add(t.group(1))
    return found - NOT_PROTOCOL


def exposed_endpoints(root: Path = None) -> set[str]:
    """Every HTTP route the server declares, as `METHOD /path`."""
    root = root or REPO / "dosync"
    found: set[str] = set()
    src = (root / "server.py").read_text()
    for m in re.finditer(r'@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"', src):
        method, path = m.group(1).upper(), m.group(2)
        if path.startswith("/v1/"):
            found.add(f"{method} {path}")
    return found


def documented_event_types(spec: Path = None) -> set[str]:
    """Event types named in the spec's audit event table."""
    text = (spec or SPEC).read_text()
    return set(re.findall(r"^\|\s*`([a-z_]+)`\s*\|", text, re.M))


def documented_endpoints(spec: Path = None) -> set[str]:
    """Endpoints named anywhere in the spec, in any of the shapes it uses."""
    text = (spec or SPEC).read_text()
    found = set()
    # Prose form: "POST /v1/thing"
    for m in re.finditer(r"(GET|POST|PUT|PATCH|DELETE)\s+`?(/v1/[A-Za-z0-9_{}/-]+)", text):
        found.add(f"{m.group(1)} {m.group(2)}")
    # Table form: "| POST | `/v1/thing` | ... |" — the endpoint summary uses this,
    # and a checker that only understood prose would report the very table
    # written to satisfy it as missing.
    for m in re.finditer(
            r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", text, re.M):
        found.add(f"{m.group(1)} {m.group(2)}")
    return found


def _normalise(path: str) -> str:
    """`{device_id}` and `{id}` name the same thing to a reader."""
    return re.sub(r"\{[a-z_]+\}", "{}", path)


def report() -> tuple[set[str], set[str]]:
    """Returns (undocumented events, undocumented endpoints)."""
    missing_events = emitted_event_types() - documented_event_types()

    documented = {_normalise(e) for e in documented_endpoints()}
    missing_endpoints = {e for e in exposed_endpoints()
                         if _normalise(e) not in documented}
    return missing_events, missing_endpoints


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="dosync.spec_coverage")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    events, endpoints = report()
    print(f"Audit event types emitted: {len(emitted_event_types())}, "
          f"documented: {len(documented_event_types())}")
    if events:
        print("  NOT in the spec:")
        for e in sorted(events):
            print(f"    {e}")
    print(f"Endpoints exposed: {len(exposed_endpoints())}, "
          f"documented: {len(documented_endpoints())}")
    if endpoints:
        print("  NOT in the spec:")
        for e in sorted(endpoints):
            print(f"    {e}")

    if args.check and (events or endpoints):
        sys.exit(1)


if __name__ == "__main__":
    main()
