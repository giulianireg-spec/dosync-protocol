"""Draft a declarative adapter for a device the hub discovered.

A scan reports an address and a service type. DoSync resolves over declared
capabilities, and nobody has declared any — so a discovered device sits in the
inventory, visible and inert, until its owner learns the format, finds their
device's API documentation and writes YAML. On the reference deployment, with
the protocol's own author as operator, that did not happen: a 3D printer stayed
adopted and unusable for days.

The alternative is not "a person writes a correct one". It is what happens now,
which is that nobody writes one.

**This is a tool, and the hub never learns that a language model exists.** It
produces a file, and a file is reviewable, versionable and reversible. No
endpoint, no manifest field, no change to the specification: an independent
implementation has nothing new to replicate.

**The prompt is a text file, not a string in this module.** It is the most
reviewable artefact of the whole feature — plain text, no logic — and someone
who does not trust a model to describe their hardware wants to read what the
model is asked, before deciding. A manufacturer can adapt it for their products
without forking anything. It lives at `templates/adapter-draft-prompt.md`.

**Building the prompt is the product; sending it is a convenience.** In a plant,
a hospital or a managed building, sending the topology of a network to an
outside model is often prohibited outright, and many such networks have no route
to the internet at all. The assembled prompt — with the device evidence, the
normative tag vocabulary and the format already in it — is what an integrator
takes to whatever model their organisation permits, or to an engineer. That path
is the primary one, not a debugging flag.

**What keeps the result from being guesswork** is that a drafted adapter
describes concrete requests: the read-only ones are executed against the real
device before anyone sees the file, and what did not answer is not presented as
though it worked. What cannot be tested — `cancel_job` against a printer that is
printing — is marked, not hidden.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("dosync.drafting")

TEMPLATE_PATH = Path(__file__).parent / "templates" / "adapter-draft-prompt.md"

#: Shipped examples used as grounding. A model asked to invent a schema invents
#: one; shown real files in the format, it writes the format. These are the same
#: files the manual path documents, so the tool and the instructions cannot
#: drift into describing different things. Chosen to span the FORMAT rather than
#: a domain: a minimal HTTP device, one with a job that can fail, one
#: industrial.
EXAMPLE_DIR = Path(__file__).parent / "examples" / "declarative"
GROUNDING_EXAMPLES = ("light-generic.yaml", "3d-printer.yaml",
                      "industrial-conveyor.yaml")


def _examples(limit_chars: int = 6000) -> str:
    out, budget = [], limit_chars
    for name in GROUNDING_EXAMPLES:
        path = EXAMPLE_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")[:budget]
        budget -= len(text)
        out.append(f"--- {name} ---\n{text}")
        if budget <= 0:
            break
    return "\n\n".join(out)


def _tag_vocabulary(repo_root: Path) -> str:
    """The standard tags, so a draft does not invent its own vocabulary.

    Guessing tag names has a measured cost: an industrial door tagged `access`
    and `security` — both standard — scored F1 0.00 on `control_access`, because
    the universal intent resolves on `lock`. A model left to guess would
    reproduce that on every device it describes.
    """
    for candidate in (repo_root / "spec" / "TAG-VOCABULARY.md",
                      Path(__file__).parent.parent / "spec" / "TAG-VOCABULARY.md"):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            tags = re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([^|]+)\|", text, re.M)
            if tags:
                return "\n".join(f"  {t:16} {d.strip()}" for t, d in tags)
    return ""


def device_evidence(device: dict) -> dict:
    """What the hub observed, and nothing it inferred."""
    extra = device.get("extra") or {}
    config = device.get("adapter_config") or {}
    return {
        "device_id":     device.get("device_id", ""),
        "device_name":   device.get("device_name", ""),
        "address":       device.get("ip") or config.get("address", ""),
        "announced_as":  device.get("service_type") or config.get("discovered_as", ""),
        "discovered_by": extra.get("transport", ""),
        "announcement":  extra.get("headers", {}),
        "description":   extra.get("description", {}),
        "tags_now":      device.get("tags", []),
    }


def build_prompt(device: dict, repo_root: Path,
                 output_path: str = "<your declarative directory>") -> str:
    """Fill the template with what this device announced.

    Each substitution closes a failure already seen: without the examples a
    model invents a schema, without the vocabulary it invents tag names, without
    the evidence it invents an address, and without being told where the file
    goes the operator is left to work it out.
    """
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    vocabulary = _tag_vocabulary(repo_root)
    return (template
            .replace("%%OUTPUT_PATH%%", output_path)
            .replace("%%DEVICE_EVIDENCE%%",
                     json.dumps(device_evidence(device), indent=2,
                                ensure_ascii=False))
            .replace("%%TAG_VOCABULARY%%",
                     vocabulary or "  (vocabulary unavailable — use conservative,"
                                   " generic tags)")
            .replace("%%EXAMPLES%%", _examples()))


#: HTTP methods with no side effects. Only these are executed during
#: verification: nobody tests `cancel_job` against a printer that is printing,
#: and a check that could break something would be worse than the gap it closes.
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def verifiable_requests(adapter: dict) -> list[dict]:
    """The requests in a draft that can be tried without consequences."""
    out = []
    transport = adapter.get("transport") or {}
    base = (transport.get("base_url") or "").rstrip("/")
    for name, action in (adapter.get("actions") or {}).items():
        request = (action or {}).get("request") or {}
        method = str(request.get("method", "GET")).upper()
        if method not in SAFE_METHODS:
            continue
        out.append({"action": name, "method": method,
                    "url": base + str(request.get("path", "")),
                    "headers": transport.get("headers") or {}})
    return out


def provenance_header(model: str, verified: list[str], unverified: list[str],
                      device_id: str) -> str:
    """Who wrote this, from what, and which parts were actually checked.

    A manifest declares what a device can do. When a model drafted it from an
    announcement it interpreted, that is part of the datum — and whoever decides
    whether to trust `cancel_job` needs to know nobody tried it.

    It is also what makes the result fit a change-review process: a YAML file
    with this header goes into version control and through an approval like any
    other change, which is the first thing asked in a professional deployment.
    """
    lines = [
        "# DRAFTED BY A LANGUAGE MODEL — review before use.",
        f"# Device: {device_id}",
        f"# Model:  {model}",
        "#",
        "# A model read what this device announced about itself and wrote the",
        "# file below. It was not written by anyone who read the device's",
        "# documentation, and it may be wrong in ways that look right.",
        "#",
    ]
    if verified:
        lines.append("# Checked against the device, and answered:")
        lines += [f"#   - {a}" for a in verified]
    if unverified:
        lines.append("# NOT checked — the device did not answer, or the action")
        lines.append("# changes something and testing it was not safe:")
        lines += [f"#   - {a}" for a in unverified]
    if not verified and not unverified:
        lines.append("# Nothing in this file was checked against the device.")
    lines += ["#",
              "# Edit it, delete what is wrong, keep what you can confirm.", ""]
    return "\n".join(lines)


def strip_fences(text: str) -> str:
    """Models wrap YAML in markdown fences however firmly you ask them not to."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()
