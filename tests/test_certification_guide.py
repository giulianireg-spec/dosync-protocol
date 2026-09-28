"""The certification guide describes the suite that certify.py runs.

spec/CERTIFICATION-GUIDE.md is what a third party reads to prepare a hub for
certification, and nothing compared it with the tool. It drifted until it could
not be followed: it required `POST /v1/intent`, which answers 410; it named
`POST /v1/devices`, `POST /v1/events`, `/v1/devices/:id/action` and
`/v1/intent/explain` where the suite calls `/v1/devices/register`, `/v1/event`,
`/v1/device/action` and `/v1/intents/{class}/explain`; it said Standard adds 22
checks and Emergency 3 (they add 23 and 11), and it did not mention the
endpoints the conformance tier calls. A hub built from it could not certify.
"""
import re
from pathlib import Path

from dosync.certify import CertReport

REPO = Path(__file__).resolve().parent.parent
GUIDE = (REPO / "spec" / "CERTIFICATION-GUIDE.md").read_text(encoding="utf-8")


def test_the_tier_table_states_the_counts_the_tool_expects():
    for tier, count in CertReport.EXPECTED_COUNTS.items():
        row = re.search(rf"^\| \*\*{tier.capitalize()}\*\* \| (\d+)", GUIDE, re.M)
        assert row and int(row.group(1)) == count, \
            f"the guide gives {tier} {row.group(1) if row else 'no row'}; certify.py expects {count}"


def test_every_endpoint_the_suite_calls_is_in_the_guide():
    """Each call certify.py makes must fit an endpoint the guide documents,
    `/v1/devices/{id}` covering `/v1/devices/cert-schema-test-01`."""
    documented = set(re.findall(r"/v1/[A-Za-z0-9_./{}-]+", GUIDE))
    templates = [re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(p.rstrip(".`"))) + "$")
                 for p in documented]
    src = (REPO / "dosync" / "certify.py").read_text(encoding="utf-8")
    calls = {(m, re.sub(r"\{[^}]+\}", "x", p))
             for m, p in re.findall(r'request\(\s*"(GET|POST|PUT|PATCH|DELETE)",\s*f?"\{base\}(/[^"?]*)', src)}
    missing = sorted(f"{m} {p}" for m, p in calls if not any(t.match(p) for t in templates))
    assert not missing, "certify.py calls endpoints the guide does not mention:\n  " + "\n  ".join(missing)


def test_the_guide_does_not_require_the_removed_endpoint():
    required = [l for l in GUIDE.splitlines()
                if re.search(r"`POST /v1/intent`", l) and "removed" not in l]
    assert not required, f"the guide still requires POST /v1/intent: {required}"
