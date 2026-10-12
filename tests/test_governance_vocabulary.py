"""Every `dosync:` term an exported Thing Description uses is published.

The exported TDs declare the namespace https://dosync.dev/ns/governance#, and
dosync.dev is served from this repository: ns/governance.html (what a person
reaches by following a term's IRI) and ns/governance.jsonld (the JSON-LD
context). A reviewer who followed the namespace before this existed reached
nothing -- a vocabulary that is not published is only a URL.
"""
import json
import re
from pathlib import Path

from dosync.wot_export import DOSYNC_NS

REPO = Path(__file__).resolve().parent.parent
# The exporter lives in the package since 2026-10-12 (the hub serves the TDs).
EMITTED = set(re.findall(r'"dosync:([A-Za-z]+)"',
                         (REPO / "dosync" / "wot_export.py").read_text(encoding="utf-8")))


def test_the_exporter_uses_the_published_namespace():
    assert DOSYNC_NS == "https://dosync.dev/ns/governance#"


def test_every_emitted_term_is_defined_in_the_html_page():
    html = (REPO / "ns" / "governance.html").read_text(encoding="utf-8")
    missing = sorted(t for t in EMITTED if f'id="{t}"' not in html)
    assert EMITTED and not missing, f"terms the exporter emits with no definition: {missing}"


def test_every_emitted_term_is_in_the_json_ld_context():
    ctx = json.loads((REPO / "ns" / "governance.jsonld").read_text(encoding="utf-8"))["@context"]
    missing = sorted(t for t in EMITTED if ctx.get(f"dosync:{t}", {}).get("@id") != DOSYNC_NS + t)
    assert not missing, f"terms missing from the JSON-LD context: {missing}"
