"""The public site may not claim more than the repository can show.

dosync.dev advertises numbers — a test count, a published version, a
conformance figure — and a project whose central argument is that its claims are
auditable cannot let them drift. They did: on 2026-08-13 the site said 894 tests
when there were 930, offered `pip install dosync` at 0.4.2 when 0.4.3 was on
PyPI, listed a Node.js implementation as Done that the README had requalified
the day before, and its roadmap still read "IEEE WF-IoT 2026 — submitted,
decision pending" a month after the decision arrived.

The test count had been corrected twenty-four hours earlier — 866 to 894 — and
was stale again by the next morning. An exact number in a static page is a
promise to update it every week, and nobody keeps that promise. So the site
states a floor ("900+"), which cannot become false by writing more tests, and
this file checks the floor is still true.

The rule is asymmetric on purpose: understating is honest, overstating is not.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "index.html"


def _site() -> str:
    return SITE.read_text(encoding="utf-8")


def _collected_tests() -> int:
    """How many tests the suite actually has, counted from the files."""
    total = 0
    for path in (REPO / "tests").rglob("test_*.py"):
        total += len(re.findall(r"^\s*(?:async\s+)?def test_\w+",
                                path.read_text(encoding="utf-8"), re.M))
    return total


def test_the_advertised_test_count_is_a_floor_that_still_holds():
    m = re.search(r'stat-num">(\d+)\+?</span><span class="stat-label">automated tests',
                  _site())
    assert m, "the site no longer states a test count in the expected shape"
    claimed, actual = int(m.group(1)), _collected_tests()
    assert claimed <= actual, (
        f"the site advertises {claimed} tests and the suite has {actual} — "
        "a public number may understate, never overstate")


def test_the_advertised_version_matches_the_package():
    """`pip install dosync` gives what the site says it gives."""
    version = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']',
                        (REPO / "dosync" / "__init__.py").read_text(encoding="utf-8"))
    assert version, "could not read dosync.__version__"
    version = version.group(1)
    site = _site()
    for claimed in re.findall(r"dosync — (\d+\.\d+\.\d+) on PyPI", site):
        assert claimed == version, (
            f"the site offers {claimed} on PyPI; the package is {version}")


def test_the_conformance_figure_matches_the_certification_suite():
    site = _site()
    m = re.search(r'stat-num">(\d+)/(\d+)</span><span class="stat-label">conformance',
                  site)
    assert m, "the site no longer states a conformance figure"
    passed, total = int(m.group(1)), int(m.group(2))
    assert passed == total, "the site advertises a conformance figure that is not complete"


def test_the_site_does_not_advertise_a_decision_as_pending():
    """A decision that has arrived may be reported; it may not stay 'pending'.

    This is the one that mattered. The page carries a section titled 'We audited
    the five properties we advertise. Two of them were false.' A roadmap line
    announcing a pending decision that resolved a month earlier is precisely
    what that section promises does not happen here.
    """
    site = _site().lower()
    for phrase in ("decision pending", "pending decision", "awaiting decision"):
        assert phrase not in site, (
            f"the site says '{phrase}' — if the decision has arrived, say what it was")


def test_the_node_implementation_is_described_the_same_way_everywhere():
    """One fact, two surfaces: the README and the site must not disagree."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    site = _site()
    if "dosync-node" not in site:
        pytest.skip("the site no longer mentions the Node.js implementation")
    readme_qualified = "re-validation" in readme.lower()
    site_qualified = "re-validation" in site.lower()
    assert readme_qualified == site_qualified, (
        "the README and the site describe dosync-node's certification "
        "differently — the same claim in two places, drifting again")
