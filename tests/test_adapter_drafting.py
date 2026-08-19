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

    `CapabilityManifest.to_dict()` includes `adapter_config` only when an
    adapter is declared — and a device with no adapter is precisely what needs
    describing. The address and service type recorded at adoption vanished, and
    the reference deployment's printer produced a prompt whose evidence block
    was entirely empty: no address, no service type, nothing announced. A model
    reading it could not have found the device, let alone described it.
    """
    from dosync.models import CapabilityManifest, DeviceCategory

    m = CapabilityManifest(
        device_id="printer-01", device_name="printer", manufacturer="unknown",
        model="unknown", firmware="unknown", category=DeviceCategory.ACTUATOR,
        tags=[], emergency_capable=False, sensors=[], actuators=[],
        adapter_config={"discovered_as": "3dprinter", "address": "192.0.2.91"})
    assert "adapter_config" not in m.to_dict(), (
        "this test is asserting against behaviour that changed — check whether "
        "to_dict now carries adapter_config for adapter-less devices")

    server = (REPO / "dosync" / "server.py").read_text(encoding="utf-8")
    describe = server[server.index("async def describe_device"):]
    describe = describe[:describe.index("\n@app.")]
    assert 'getattr(device, "adapter_config"' in describe, \
        "the endpoint reads the manifest dict, which drops the evidence"

    # And with the config restored, the evidence is there.
    manifest = m.to_dict()
    manifest["adapter_config"] = m.adapter_config
    evidence = device_evidence(manifest)
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
