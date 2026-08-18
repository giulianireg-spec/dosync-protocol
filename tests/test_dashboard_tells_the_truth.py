"""What the dashboard says must match what the hub did.

Three things a from-scratch Windows install exposed, all of them the interface
describing the system inaccurately rather than the system misbehaving:

- The header read `disconnected` while devices loaded, scans ran and every call
  returned 200 — because the word described the WebSocket, which had no library
  to speak it, and a reader took it to mean the hub was unreachable.
- The token field said `API token…` and nothing else. Help existed behind a `?`
  button and the project's own author asked where to get a key without finding
  it, which settles how discoverable it was.
- The scan endpoint reports which transports it searched and which it skipped,
  and the page discarded both — so a scan that never searched WiZ, because
  pywizlight was missing, told the operator that no device had answered. The
  bulbs were powered on. Nothing answered because nobody asked.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "dosync" / "dashboard.html"


def _dashboard() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_the_indicator_does_not_call_a_working_hub_disconnected():
    page = _dashboard()
    assert ">disconnected<" not in page, (
        "the header still says 'disconnected', which was read as 'the hub is "
        "unreachable' while every call to it returned 200")
    assert "live events unavailable" in page, \
        "there is no wording for a reachable hub whose live events are not"


def test_the_token_field_says_where_a_token_comes_from():
    page = _dashboard()
    assert 'placeholder="API token…"' not in page, \
        "the field still offers no hint about where a token comes from"
    assert "printed on first start" in page, \
        "the field does not mention where the first token appears"


def test_the_scan_reports_which_transports_it_covered():
    page = _dashboard()
    assert "function describeCoverage" in page, \
        "the page has no way to report transport coverage"
    assert "res.searched" in page and "res.not_searchable" in page, \
        "the page does not read the coverage the endpoint reports"
    assert "let found, res;" in page, \
        "the scan response is discarded again, taking the coverage with it"


def test_the_empty_scan_message_includes_the_coverage():
    """"Nothing answered" is only true if something was asked."""
    page = _dashboard()
    empty = page[page.index("No devices answered on this network"):]
    empty = empty[:empty.index("return;")]
    assert "describeCoverage" in empty, \
        "an empty scan still reports silence without saying what was searched"


def test_the_dashboard_javascript_parses():
    """A stray docstring once made it in; a broken page has no visible tests."""
    page = _dashboard()
    assert '"""' not in page, "a Python docstring reached the dashboard script"
    assert page.count("<script>") == page.count("</script>"), \
        "unbalanced script tags"
