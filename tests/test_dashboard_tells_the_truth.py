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


def test_the_status_indicator_does_not_flicker_between_stale_and_fresh_sockets():
    """A fourth thing the same reinstallation found: the header flickered
    between "live" and "live events unavailable" seconds apart, on a hub that
    was never actually dropping the connection.

    The page auto-connects on load using a saved token, and the operator also
    pressed Connect by hand. Two WebSocket objects existed briefly, and each
    one's onclose scheduled its own reconnect — so an old socket closing after
    a new one had already opened "live" would flip the status back down and
    queue a redundant retry. Not a real disconnection: two generations of
    connect() disagreeing about which of them was current.

    The fix is a generation counter: every call to connect() bumps it, and a
    socket's callbacks check it against the current value before touching
    global status or scheduling a reconnect. A superseded socket's events are
    inert once a newer one exists.
    """
    page = _dashboard()
    assert "wsGeneration" in page, \
        "no generation guard exists, so a stale socket can still overwrite " \
        "a newer connection's status"
    assert "myGeneration !== wsGeneration" in page or \
           "myGeneration === wsGeneration" in page, \
        "the guard is declared but never checked before acting on a socket event"
    # The three callbacks that mutate shared state or schedule work must each
    # be guarded, not just onclose — an open or message event from a stale
    # socket is exactly as capable of corrupting the displayed status.
    for handler in ("socket.onopen", "socket.onmessage", "socket.onclose"):
        section = page[page.index(handler):page.index(handler) + 400]
        assert "myGeneration" in section, \
            f"{handler} does not check the generation guard"


def test_a_draft_can_be_checked_before_it_is_saved():
    """Until now the only way to find out whether a drafted adapter matched the
    device was to install it and watch.

    A model given an empty announcement wrote a confident adapter for an
    unrelated protocol. Given the vendor's own announcement it wrote an honest
    one — correct manufacturer, `# UNVERIFIED` markers, candid comments — whose
    endpoints were still invented. Both files read as plausible. The printer
    refused the connection on every path in both.

    So the check has to sit between getting the file back and saving it, which
    is where the operator actually is at that moment.
    """
    page = _dashboard()
    assert "runDraftCheck" in page, "there is no way to check a draft from the dashboard"
    assert "/v1/adapters/verify" in page, "the dashboard does not ask the hub to verify"
    assert "<textarea" in page, \
        "a YAML draft cannot be pasted into an alert(); it needs somewhere to go"


def test_the_three_verdicts_are_not_collapsed_into_pass_or_fail():
    """"Nothing answered" is not "some checks failed".

    A printer with no HTTP server at all returns nothing on every path — that
    says the transport is wrong, not that a route is. And a draft where every
    request changes something cannot be judged either way, which is a third
    thing again and must not be reported as success.
    """
    page = _dashboard()
    for verdict in ("transport_unreachable", "nothing_testable", "ok"):
        assert verdict in page, f"the dashboard does not distinguish {verdict}"
    assert "it is the transport" in page, \
        "an unreachable transport is not explained as different from a failed route"
    assert "cannot tell you either way" in page, \
        "a draft with nothing testable is not reported honestly"


def test_what_could_not_be_tested_is_shown_not_omitted():
    """`cancel_job` on a printer that may be printing is exactly what must NOT
    be executed to find out whether it exists. It stays unverified by design —
    so the operator has to see it, or a green verdict reads as coverage it
    never had."""
    page = _dashboard()
    assert "NOT TRIED — AND WHY" in page, \
        "the untested half of a draft is invisible in the result"
    assert "r.unverifiable" in page, \
        "the dashboard discards the accounting of what the hub could not try"
    assert "whatever the verdict above says" in page, \
        "nothing warns that the untested part is untested regardless of the verdict"


def test_describing_a_device_leads_to_checking_the_result():
    """The check existing and the operator knowing it exists at the moment they
    need it are different things."""
    page = _dashboard()
    describe = page[page.index("async function describeDevice"):]
    describe = describe[:describe.index("\nasync function") if "\nasync function" in describe else len(describe)]
    assert "openDraftCheck()" in describe, \
        "after handing over the description, nothing points at the verification step"
    assert "BEFORE saving it" in describe, \
        "the operator is not told to check the draft before saving it"


def test_the_result_escapes_what_it_renders():
    """The reason a request failed vanished from the panel entirely.

    `URLError: <urlopen error [Errno 111] Connection refused>` was rendered
    into innerHTML unescaped, so the browser read `<urlopen error ...>` as an
    unknown element and dropped it. The operator saw `no answer — URLError:`
    with the cause silently removed — the panel built to stop a draft asserting
    unmeasured things was itself hiding what it had measured.

    Everything shown here comes from a pasted file or a device's own reply.
    Neither is ours to trust, and the same path would have run an injected
    script element out of a draft just as willingly.
    """
    page = _dashboard()
    assert "function esc(" in page, "there is no escaping helper"
    for field in ("esc(c.error", "esc(c.url)", "esc(u.reason)", "esc(u.action)"):
        assert field in page, f"{field} is still interpolated unescaped"
    assert "${c.error ||" not in page, "the raw error is still rendered directly"


def test_a_failure_with_no_message_still_says_something():
    """An empty reason rendered as a dangling em dash and nothing after it."""
    page = _dashboard()
    assert "no reason reported" in page, \
        "a failure whose exception carries no text leaves the operator with a " \
        "dash and no explanation"
