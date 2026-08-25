"""Describing a discovered device: where the text comes from, and who sees it.

A scan reports an address and a service type; DoSync resolves over declared
capabilities and nobody has declared any. So a discovered device sits in the
inventory, visible and inert, until someone learns the format, finds the
device's API documentation and writes YAML. On the reference deployment, with
the protocol's own author as operator, that did not happen for days.

The first version of this shipped as a terminal command with the prompt buried
in a Python module — two days after the README was reordered so that adopting a
device would need no terminal. It worked and nobody would ever have found it.

What this file pins is where the text lives and who can reach it:

- the prompt is a text file, because it is the most reviewable artefact of the
  feature and someone who distrusts a model describing their hardware wants to
  read what it is asked before deciding;
- assembling it is the product and sending it is a convenience, because in a
  plant or a hospital sending network topology outside is often prohibited and
  frequently impossible;
- the dashboard offers it where the hub already detects the problem;
- and an agent connected over MCP can reach it, since that is the case this
  protocol exists for and it could not scan, adopt or describe anything.
"""
from pathlib import Path

import pytest

from dosync.adapter_drafting import (GROUNDING_EXAMPLES, SAFE_METHODS,
                                     TEMPLATE_PATH, build_prompt,
                                     device_evidence, provenance_header,
                                     strip_fences, verifiable_requests)

REPO = Path(__file__).resolve().parent.parent

DISCOVERED = {
    "device_id": "printer-01",
    "device_name": "workshop printer",
    "ip": "192.0.2.91",
    "service_type": "3dprinter",
    "tags": [],
    "extra": {"transport": "ssdp",
              "headers": {"devmodel.example.com": "N1"},
              "description": {"friendlyName": "Printer"}},
}


# ── Q1: the prompt is a file, not a string in a module ───────────────────────

def test_the_prompt_is_a_text_file_anyone_can_read():
    assert TEMPLATE_PATH.exists(), "the prompt is not a file"
    assert TEMPLATE_PATH.suffix == ".md", "the prompt is not readable as text"
    body = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert len(body) > 500, "the template is a stub"


def test_the_template_ships_with_the_package():
    """A manufacturer adapting it for their products should not need a clone."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "templates/*" in pyproject, \
        "the prompt template is not packaged, so an installed hub cannot read it"


def test_every_placeholder_is_filled():
    prompt = build_prompt(DISCOVERED, REPO, "/tmp/x.yaml")
    assert "%%" not in prompt, "a placeholder reached the model unsubstituted"


# ── The prompt's content ─────────────────────────────────────────────────────

def test_the_prompt_carries_what_the_scan_actually_found():
    prompt = build_prompt(DISCOVERED, REPO)
    for fact in ("printer-01", "192.0.2.91", "3dprinter", "ssdp"):
        assert fact in prompt, f"the prompt does not mention {fact}"


def test_the_prompt_grounds_the_model_in_shipped_examples():
    """A model asked to invent a schema invents one. These are the same files
    the manual path documents, so tool and instructions cannot drift apart."""
    prompt = build_prompt(DISCOVERED, REPO)
    for name in GROUNDING_EXAMPLES:
        assert (REPO / "dosync" / "examples" / "declarative" / name).exists()
        assert name in prompt
    assert "transport:" in prompt and "actions:" in prompt


def test_the_prompt_carries_the_normative_tag_vocabulary():
    """Guessing tag names has a measured cost: an industrial door tagged
    `access` + `security` — both standard — scores F1 0.00 on `control_access`,
    which resolves on `lock`."""
    prompt = build_prompt(DISCOVERED, REPO)
    assert "lock" in prompt and "emergency" in prompt
    assert "Vendor names are never tags" in prompt


def test_the_prompt_asks_for_honesty_over_completeness():
    prompt = build_prompt(DISCOVERED, REPO)
    assert "Plausible is not grounds" in prompt
    assert "UNVERIFIED" in prompt
    assert "incomplete honest file" in prompt


def test_the_prompt_never_asks_for_credentials():
    prompt = build_prompt(DISCOVERED, REPO)
    assert "Never include credentials" in prompt
    assert "REPLACE_WITH_YOUR_API_KEY" in prompt


# ── Q6: agnostic, and measured rather than asserted ──────────────────────────

def test_the_prompt_presumes_no_domain():
    """Asked explicitly whether this is agnostic. This project does not take a
    property on trust when it can be measured."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    for domestic in ("your home", "smart home", "household", "living room",
                     "in the house"):
        assert domestic not in template, \
            f"the prompt presumes a domestic setting: {domestic!r}"
    # Collapsed: the sentence wraps, and a test that depends on where a line
    # breaks measures formatting rather than meaning.
    flat = " ".join(template.split())
    assert "a house, a factory, a hospital or a vehicle" in flat, \
        "the prompt does not tell the model the setting is unknown"


def test_the_grounding_examples_are_not_all_domestic():
    """Two of three are not a home: what anchors the format also anchors the
    assumption about where devices live."""
    non_domestic = [n for n in GROUNDING_EXAMPLES
                    if any(w in n for w in ("industrial", "printer", "conveyor"))]
    assert len(non_domestic) >= 2, \
        f"the format is anchored mostly on domestic examples: {GROUNDING_EXAMPLES}"


# ── Verification ─────────────────────────────────────────────────────────────

def test_only_harmless_requests_are_verified():
    """Nobody tests `cancel_job` against a printer that is printing."""
    draft = {"transport": {"base_url": "http://192.0.2.91"},
             "actions": {
                 "status": {"request": {"method": "GET", "path": "/api/version"}},
                 "cancel": {"request": {"method": "POST", "path": "/api/job"}},
                 "wipe":   {"request": {"method": "DELETE", "path": "/api/all"}}}}
    assert [r["action"] for r in verifiable_requests(draft)] == ["status"]
    assert set(SAFE_METHODS) == {"GET", "HEAD", "OPTIONS"}


def test_evidence_is_what_was_observed_not_what_was_inferred():
    evidence = device_evidence(DISCOVERED)
    assert evidence["announced_as"] == "3dprinter"
    assert evidence["address"] == "192.0.2.91"
    assert "capabilities" not in evidence


# ── Q5: the result goes through a change review ──────────────────────────────

def test_provenance_says_who_wrote_it_and_what_was_checked():
    header = provenance_header("llama3", ["status"], ["cancel_job"], "printer-01")
    assert "DRAFTED BY A LANGUAGE MODEL" in header
    assert "llama3" in header and "printer-01" in header
    assert "status" in header and "cancel_job" in header
    assert "NOT checked" in header


def test_provenance_is_explicit_when_nothing_was_checked():
    assert "Nothing in this file was checked" in provenance_header("m", [], [], "d")


def test_markdown_fences_are_stripped():
    assert strip_fences("```yaml\ndevice:\n  id: x\n```") == "device:\n  id: x"
    assert strip_fences("device:\n  id: x") == "device:\n  id: x"


# ── Q2, Q3, Q4: who can reach this ───────────────────────────────────────────

def test_assembling_is_the_default_and_sending_is_opt_in():
    """In a plant or a hospital, sending topology outside is often prohibited
    and frequently impossible. The assembled prompt is the product."""
    manage = (REPO / "dosync" / "manage.py").read_text(encoding="utf-8")
    assert '"--send"' in manage, "there is no opt-in for sending"
    assert '"--print-prompt"' not in manage, \
        "printing is still a flag, which makes sending the default path"


def test_the_dashboard_offers_it_where_the_hub_detects_the_problem():
    """The hub says at every start that this device cannot act. Knowing the
    problem and not offering the step that fixes it wastes what it knows."""
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    assert "describeDevice" in dashboard, "the dashboard offers no next step"
    assert "/describe" in dashboard, "the dashboard does not ask the hub for it"


def test_the_hub_exposes_it_as_text_and_sends_nothing():
    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    assert "/v1/devices/{device_id}/describe" in server
    assert "llm" not in server.lower().split("describe_device")[1][:2000], \
        "the hub reaches out to a model; it must only hand over text"


def test_an_agent_over_mcp_can_scan_adopt_and_describe():
    """The protocol's central case is an agent connected to the hub, and the
    first version asked the operator to paste a prompt into that same agent."""
    mcp = (REPO / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    for tool in ("dosync_discover_devices", "dosync_adopt_device",
                 "dosync_describe_device"):
        assert f'name="{tool}"' in mcp, f"{tool} is not declared"
        assert f'elif name == "{tool}"' in mcp, f"{tool} has no handler"


def test_the_agent_and_the_terminal_are_told_the_same_thing():
    """One prompt. A person at a terminal and an agent over MCP must not be
    given different instructions for the same job."""
    mcp = (REPO / "dosync" / "mcp_server.py").read_text(encoding="utf-8")
    assert "from dosync.adapter_drafting import build_prompt" in mcp, \
        "MCP assembles its own instructions instead of using the template"


def test_the_tool_adds_nothing_to_the_protocol():
    models = (REPO / "dosync" / "models.py").read_text(encoding="utf-8")
    assert "drafted_by" not in models, \
        "a manifest field was added; provenance belongs in the file"


def test_the_manual_path_stays_first_class():
    examples = list((REPO / "dosync" / "examples" / "declarative").glob("*"))
    assert len(examples) >= 5, "the shipped examples thinned out"
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "declarative" in readme.lower()


# ── Found by pressing the button, after the endpoint had been tested by curl ──

def test_discovery_evidence_survives_a_device_with_no_adapter():
    """The one case this endpoint exists for was the one that lost its evidence.

    `CapabilityManifest.to_dict()` used to include `adapter_config` only when
    an adapter was declared — and a device with no adapter is precisely what
    needs describing. The address and service type recorded at adoption
    vanished, and the reference deployment's printer produced a prompt whose
    evidence block was entirely empty. The endpoint patched around it by
    reading the object rather than the dict; the dict still lied to every other
    caller, so `to_dict` itself was fixed and this now asserts the corrected
    behaviour.
    """
    from dosync.models import CapabilityManifest, DeviceCategory

    m = CapabilityManifest(
        device_id="printer-01", device_name="printer", manufacturer="unknown",
        model="unknown", firmware="unknown", category=DeviceCategory.ACTUATOR,
        tags=[], emergency_capable=False, sensors=[], actuators=[],
        adapter_config={"discovered_as": "3dprinter", "address": "192.0.2.91"})
    assert m.adapter is None
    assert "adapter_config" in m.to_dict(), (
        "a device with no adapter serialises without the address and service "
        "type it was adopted with — the one case that still needs describing")

    # Straight from to_dict, with no caller having to repair it first.
    evidence = device_evidence(m.to_dict())
    assert evidence["address"] == "192.0.2.91"
    assert evidence["announced_as"] == "3dprinter"


def test_the_dashboard_reads_the_description_as_text():
    """It is text. `api()` always parses JSON, and asking it not to by passing
    a fourth argument it does not take produced "Unexpected token 'Y'" — the
    first word of the description — on the first press of the button."""
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    describe = dashboard[dashboard.index("async function describeDevice"):]
    describe = describe[:describe.index("\n}")]
    assert "await r.text()" in describe, \
        "the dashboard still parses the description as JSON"
    assert "api('GET'" not in describe, \
        "the dashboard uses the JSON helper for a plain-text endpoint"


def test_the_vocabulary_excludes_the_deprecated_tags():
    """The drafting tool was teaching the antipattern it exists to prevent.

    `TAG-VOCABULARY.md` has a "Deprecated tags" table laid out identically to
    the category tables, and scanning the whole file swept it in — so the prompt
    offered `climate`, `door-lock` and `smart-plug` as vocabulary. Two of those
    three are tags this project removed from its own deployment, and documents
    as the mistakes everyone makes; one of them, `smart-plug` on a bulb, is the
    example used in the Concepts article to explain why the antipattern is
    costly.
    """
    prompt = build_prompt(DISCOVERED, REPO)
    vocabulary = prompt[prompt.index("TAGS —"):prompt.index("THE FORMAT")]
    for deprecated in ("climate ", "door-lock", "smart-plug"):
        assert deprecated not in vocabulary, \
            f"the prompt offers the deprecated tag {deprecated.strip()!r}"
    for standard in ("light ", "lock ", "plug ", "emergency "):
        assert standard in vocabulary, f"the standard tag {standard!r} is missing"


def test_the_device_comes_before_the_format():
    """249 lines that open with the generic bury the specific.

    Whoever reads this — a model or a person — should see what was actually
    found before the schema and three example files.
    """
    prompt = build_prompt(DISCOVERED, REPO)
    assert prompt.index("THE DEVICE YOU ARE DESCRIBING") < prompt.index("THE FORMAT"), \
        "the format comes before the device this is about"
    assert prompt.index("printer-01") < prompt.index("light-generic.yaml")


def test_the_examples_cover_both_transports():
    """The format speaks HTTP and MQTT, and a device that speaks MQTT should
    see an example of its own transport."""
    prompt = build_prompt(DISCOVERED, REPO)
    assert "kind: http" in prompt and "kind: mqtt" in prompt, \
        "the grounding examples do not cover both transports the format speaks"


def test_a_failed_clipboard_copy_is_not_reported_as_success():
    """The API needs a secure context — HTTPS or localhost — and a hub reached
    at http://<lan-address> is neither. It worked on the reference deployment;
    where it does not, an empty catch followed by "Copied to your clipboard"
    would be the system asserting something that did not happen."""
    dashboard = (REPO / "dosync" / "dashboard.html").read_text(encoding="utf-8")
    describe = dashboard[dashboard.index("async function describeDevice"):]
    describe = describe[:describe.index("\n}")]
    assert "catch (e) {}" not in describe, \
        "an empty catch still swallows a failed copy"
    assert "copied" in describe, \
        "the message does not depend on whether the copy succeeded"


# ── Adoption must not discard what the device announced (2026-08-24) ────────

def test_adoption_keeps_what_the_device_announced_about_itself():
    """A model wrote a fully confident adapter for the wrong protocol, and the
    datum that would have prevented it had been captured and thrown away.

    A real 3D printer announced `NT: urn:bambulab-com:device:3dprinter:1` over
    SSDP — the vendor naming itself, the most authoritative statement a device
    makes about its own identity. The discoverer captured it in `extra`;
    adoption persisted only the address and the service type; `extra` is not a
    field `CapabilityManifest` has. So the description handed to a model read
    `"announcement": {}`, and it filled the gap with the OctoPrint example
    shipped alongside as a format reference. Every endpoint it produced
    returned HTTP 000: the printer has no HTTP server at all.
    """
    from dosync.models import CapabilityManifest, DeviceCategory

    m = CapabilityManifest(
        device_id="printer-1", device_name="a printer",
        manufacturer="unknown", model="unknown", firmware="unknown",
        category=DeviceCategory.ACTUATOR, tags=[],
        adapter_config={"discovered_as": "3dprinter", "address": "192.0.2.91"},
        discovery_evidence={
            "transport": "ssdp",
            "headers": {"nt": "urn:example-vendor:device:3dprinter:1",
                        "devmodel.example.com": "X1"},
        })

    stored = m.to_dict()
    assert "discovery_evidence" in stored, \
        "the announcement does not survive serialisation, so it is lost the " \
        "moment the device is written to the registry"

    evidence = device_evidence(stored)
    assert evidence["announcement"], \
        "whoever describes this device still receives an empty announcement"
    assert "example-vendor" in evidence["announcement"].get("nt", ""), \
        "the vendor's own identity header did not reach the description"
    assert evidence["discovered_by"] == "ssdp", \
        "which transport heard the device is part of what the announcement means"


def test_adapter_config_survives_a_device_with_no_adapter():
    """`to_dict` emitted `adapter_config` only alongside an adapter — and a
    device with no adapter is exactly the one that still needs describing, so
    its address and service type vanished from every serialised form."""
    from dosync.models import CapabilityManifest, DeviceCategory

    m = CapabilityManifest(
        device_id="d", device_name="d", manufacturer="u", model="u",
        firmware="u", category=DeviceCategory.ACTUATOR, tags=[],
        adapter_config={"discovered_as": "3dprinter", "address": "192.0.2.91"})
    assert m.adapter is None
    stored = m.to_dict()
    assert stored.get("adapter_config", {}).get("address") == "192.0.2.91"

    evidence = device_evidence(stored)
    assert evidence["address"] == "192.0.2.91"
    assert evidence["announced_as"] == "3dprinter"


def test_the_hub_never_interprets_the_announcement():
    """Storing the raw datum must not become the first step towards a
    catalogue of vendors.

    The panel that asked for this evidence to be persisted set the limit in the
    same breath: keep what the device said, never grow branches per brand. A
    hub that starts reading `urn:bambulab-com` to decide behaviour has become
    the thing this project decided not to be — and the decision is easy to
    erode one convenient special case at a time.
    """
    import re

    for module in ("server.py", "adapter_drafting.py", "hub.py"):
        source = (REPO / "dosync" / module).read_text(encoding="utf-8")
        # Comments and docstrings may name a vendor to explain WHY; executable
        # lines may not branch on one.
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        )
        for vendor in ("bambulab", "octoprint", "prusa", "creality"):
            offending = re.findall(
                rf'(?:if|elif|match|==|!=|\.startswith|\.endswith|in )[^\n]*'
                rf'["\'][^"\']*{vendor}', code, re.IGNORECASE)
            assert not offending, (
                f"{module} branches on the vendor name {vendor!r}: {offending[:2]}. "
                "The announcement is stored verbatim and never interpreted — "
                "per-brand logic is how a protocol becomes a product catalogue."
            )


# ── Verification is the only barrier that moves certainties (2026-08-24) ────

def test_read_only_requests_are_found_wherever_they_sit():
    """Checking only `actions` verified nothing at all on a real draft.

    Asked to prefer read-only endpoints, a model put its single GET under
    `sensors` — where a sensor belongs. `verifiable_requests` looked only at
    `actions`, found nothing to test, and the draft reached the operator with
    no objection raised. What makes a request safe to try is its method, not
    the section of the file it happens to occupy.
    """
    draft = {
        "transport": {"kind": "http", "base_url": "http://192.0.2.9"},
        "actions": {"stop_it": {"request": {"method": "POST", "path": "/stop"}}},
        "sensors": {"state": {"request": {"method": "GET", "path": "/state"}}},
    }
    found = verifiable_requests(draft)
    assert [r["action"] for r in found] == ["state"], \
        "a read-only request under `sensors` is still invisible to verification"
    assert found[0]["section"] == "sensors"


def test_what_cannot_be_tested_is_reported_rather_than_omitted():
    """`cancel_job` on a printer that may be printing is precisely what must
    NOT be executed to find out whether it exists. It stays untested by
    design — so it has to be named, not quietly left out of the report."""
    from dosync.adapter_drafting import unverifiable_entries

    draft = {
        "transport": {"kind": "http", "base_url": "http://192.0.2.9"},
        "actions": {
            "cancel_job": {"request": {"method": "POST", "path": "/cancel"}},
            "estop": {"publish": {"topic": "line/cmd", "payload": "stop"}},
        },
    }
    reported = {e["action"]: e["reason"] for e in unverifiable_entries(draft)}
    assert set(reported) == {"cancel_job", "estop"}
    assert "changes something" in reported["cancel_job"]
    assert "broker" in reported["estop"], \
        "an MQTT publish is untestable for a different reason and should say so"


def test_a_draft_nothing_answers_is_called_unreachable_not_merely_empty():
    """The verdict an operator needs is not "0 of 1 passed".

    A real printer returned HTTP 000 on every path a model had written for it —
    not 404, which would mean a server exists and the route is wrong, but no
    server at all. `transport_unreachable` says the whole transport is wrong;
    reporting it as a failed check would have buried that.
    """
    import asyncio

    from dosync.adapter_drafting import verify_draft

    draft = {
        # TEST-NET-1: guaranteed not to route anywhere.
        "transport": {"kind": "http", "base_url": "http://192.0.2.91"},
        "actions": {"cancel": {"request": {"method": "POST", "path": "/cancel"}}},
        "sensors": {"state": {"request": {"method": "GET", "path": "/state"}}},
    }
    result = asyncio.run(verify_draft(draft, timeout=1.0))
    assert result["verdict"] == "transport_unreachable"
    assert result["answered_count"] == 0
    assert result["checked_count"] == 1
    assert len(result["unverifiable"]) == 1, \
        "the POST that could not be tried is missing from the accounting"


def test_verification_never_proposes_a_replacement():
    """It reports whether what was written matches what answers.

    A hub that probed for a device's real API and rewrote the draft would be
    guessing on the operator's behalf, one convenient special case at a time,
    and would need to know about vendors to do it — the catalogue this project
    decided not to become.
    """
    source = (REPO / "dosync" / "adapter_drafting.py").read_text(encoding="utf-8")
    fn = source[source.index("async def verify_draft"):]
    # Executable lines only. The first version of this scanned the whole
    # function and tripped on the word "guess" in the docstring that argues
    # against guessing — a test failing on the prose that justifies it.
    body = "\n".join(line for line in fn.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))
    body = body.split('"""')[2] if body.count('"""') >= 2 else body
    for inventing in ("/api/", "well_known", "candidates = ["):
        assert inventing not in body, \
            f"verification contains {inventing!r} — it must try only what the " \
            "draft declares, never look for endpoints of its own"


# ── An MQTT draft was probed as HTTP (2026-08-25) ───────────────────────────

MQTT_DRAFT = {
    "device": {"id": "printer", "name": "printer", "category": "actuator"},
    "transport": {"kind": "mqtt", "broker": "REPLACE_ME", "port": 8883,
                  "tls": True},
    "actions": {
        "pause_job":  {"type": "pause", "publish": {"topic": "d/x/pause", "payload": ""}},
        "cancel_job": {"type": "stop",  "publish": {"topic": "d/x/stop",  "payload": ""}},
    },
    "sensors": {
        "print_status": {"request": {"method": "subscribe", "topic": "d/x/report"}},
    },
}


def test_an_mqtt_draft_is_not_probed_over_http():
    """The first real MQTT draft put through the dashboard was tried as HTTP.

    Its `publish` actions carry no `request`, so the method defaulted to GET;
    `base_url` is absent on an MQTT transport, so the URL was the empty string.
    The hub issued three requests to `''` and reported `ValueError: unknown url
    type`. What a draft can be tried with follows from its transport, not from
    a default that only makes sense for one of them.
    """
    assert verifiable_requests(MQTT_DRAFT) == [], \
        "an MQTT draft still yields HTTP requests to attempt"


def test_an_entry_is_never_both_tried_and_untried():
    """The same three actions appeared in both lists at once — counted as
    verifiable HTTP requests and, one line later, as untestable publishes."""
    from dosync.adapter_drafting import unverifiable_entries

    tried = {r["action"] for r in verifiable_requests(MQTT_DRAFT)}
    untried = {e["action"] for e in unverifiable_entries(MQTT_DRAFT)}
    assert not (tried & untried), \
        f"these appear in both lists: {sorted(tried & untried)}"
    # And every declared entry is accounted for in one of them.
    declared = set(MQTT_DRAFT["actions"]) | set(MQTT_DRAFT["sensors"])
    assert declared == (tried | untried), \
        f"unaccounted for: {sorted(declared - (tried | untried))}"


def test_a_transport_is_never_called_unreachable_without_being_tried():
    """The verdict said the device was not speaking MQTT. The hub had not sent
    a single MQTT message — it had sent three HTTP requests to the empty
    string. Reporting a transport as unreachable without having spoken it is
    the same failure this project fixed in the scan, in the audit and in
    simulation reporting.
    """
    import asyncio

    from dosync.adapter_drafting import verify_draft

    result = asyncio.run(verify_draft(MQTT_DRAFT, timeout=1.0))
    assert result["verdict"] == "nothing_testable", \
        "an untried transport is still being reported as unreachable"
    assert result["checked_count"] == 0
    assert len(result["unverifiable"]) == 3, \
        "the entries the hub could not try are not all accounted for"
    assert any("only try HTTP" in e["reason"] for e in result["unverifiable"]), \
        "nothing explains that the hub cannot speak this draft's transport"


def test_an_http_draft_with_no_base_url_is_not_probed_either():
    """`base + path` with an empty base produced a request to a relative path
    and a ValueError, reported to the operator as the device not answering."""
    draft = {"transport": {"kind": "http"},
             "sensors": {"state": {"request": {"method": "GET", "path": "/state"}}}}
    assert verifiable_requests(draft) == [], \
        "a draft with no base_url still yields a request the hub will attempt"
