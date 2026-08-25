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
        if not candidate.exists():
            continue
        text = candidate.read_text(encoding="utf-8")
        # Scoped to the CATEGORY sections. Scanning the whole file swept in the
        # "Deprecated tags" table, which is laid out identically — so the prompt
        # handed a model `climate`, `door-lock` and `smart-plug` as vocabulary.
        # Two of those three are tags this project removed from its own
        # deployment and documents as the mistake everyone makes; the drafting
        # tool was teaching them.
        start = text.find("## Tag categories")
        end = text.find("## Intent-to-tag mapping")
        section = text[start:end] if start != -1 and end > start else ""
        tags = re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*([^|]+)\|", section, re.M)
        if tags:
            return "\n".join(f"  {t:16} {d.strip()}" for t, d in tags)
    return ""


def device_evidence(device: dict) -> dict:
    """What the hub observed, and nothing it inferred.

    Reads `discovery_evidence` from the stored manifest, falling back to the
    live `extra` of a DiscoveredDevice. Both are needed: this is called with a
    finding during a scan and with a persisted manifest afterwards.

    It used to read `extra` only — a field `CapabilityManifest` does not have —
    so every adopted device produced `"announcement": {}`. A model asked to
    describe a 3D printer got its address and the word "3dprinter", nothing
    about the vendor that the hub had captured and discarded, and wrote an
    adapter for an unrelated protocol with no hesitation.
    """
    evidence = device.get("discovery_evidence") or device.get("extra") or {}
    config = device.get("adapter_config") or {}
    return {
        "device_id":     device.get("device_id", ""),
        "device_name":   device.get("device_name", ""),
        "address":       device.get("ip") or config.get("address", ""),
        "announced_as":  (device.get("service_type")
                          or evidence.get("announced_as")
                          or config.get("discovered_as", "")),
        "discovered_by": evidence.get("transport", ""),
        "announcement":  evidence.get("headers", {}),
        "description":   evidence.get("description", {}),
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
    """The requests in a draft that can be tried without consequences.

    Both `actions` and `sensors`. Reading only `actions` made this return
    nothing at all for a real draft: a model asked to prefer read-only
    endpoints put its single GET under `sensors`, where a sensor belongs — so
    the verification step found nothing to check and the draft reached the
    operator with no objection raised. The rule is the HTTP method, not which
    section of the file a request happens to sit in.
    """
    out = []
    transport = adapter.get("transport") or {}
    # Only HTTP requests can be tried this way. An MQTT draft was probed as
    # HTTP: its `publish` actions carry no `request`, the method defaulted to
    # GET, `base_url` was absent, and the hub issued three requests to the
    # empty string. It then reported the transport as unreachable — a claim
    # about MQTT, which was never tried at all.
    if str(transport.get("kind", "http")).lower() not in ("http", "https"):
        return out
    base = (transport.get("base_url") or "").rstrip("/")
    if not base:
        return out
    for section in ("actions", "sensors"):
        for name, entry in (adapter.get(section) or {}).items():
            entry = entry or {}
            # A publish is not a request, whatever else the entry contains.
            if entry.get("publish"):
                continue
            request = entry.get("request") or {}
            if not request:
                continue
            method = str(request.get("method", "GET")).upper()
            if method not in SAFE_METHODS:
                continue
            path = str(request.get("path", ""))
            if not path:
                continue
            out.append({"action": name, "section": section, "method": method,
                        "url": base + path,
                        "headers": transport.get("headers") or {}})
    return out


def unverifiable_entries(adapter: dict) -> list[dict]:
    """Everything the hub cannot try, and why.

    A draft is not just its testable half. `cancel_job` on a printer that may
    be printing is exactly what must NOT be executed to find out whether it
    exists — so it stays unverified by design, and the operator has to be told
    that plainly rather than shown a file whose untested parts look identical
    to its tested ones.
    """
    out = []
    transport = adapter.get("transport") or {}
    kind = str(transport.get("kind", "http")).lower()
    testable = {r["action"] for r in verifiable_requests(adapter)}
    for section in ("actions", "sensors"):
        for name, entry in (adapter.get(section) or {}).items():
            entry = entry or {}
            # Never both lists. Listing an entry as tried AND untried was the
            # first thing the panel surfaced when a real MQTT draft went
            # through it.
            if name in testable:
                continue
            if entry.get("publish"):
                out.append({"action": name, "section": section,
                            "reason": "publishes to a broker — sending it is the "
                                      "side effect, so it cannot be tested"})
                continue
            if kind not in ("http", "https"):
                out.append({"action": name, "section": section,
                            "reason": f"the hub can only try HTTP requests, and "
                                      f"this draft speaks {kind}"})
                continue
            request = entry.get("request") or {}
            if not request:
                out.append({"action": name, "section": section,
                            "reason": "declares no request the hub could send"})
                continue
            method = str(request.get("method", "GET")).upper()
            if method not in SAFE_METHODS:
                out.append({"action": name, "section": section,
                            "reason": f"{method} changes something on the device"})
            elif not str(request.get("path", "")):
                out.append({"action": name, "section": section,
                            "reason": "declares no path to request"})
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


async def verify_draft(adapter: dict, timeout: float = 4.0) -> dict:
    """Try a draft's harmless requests against the real device.

    This is the step that separates a description from a guess. A model given
    an empty announcement wrote a confident adapter for an unrelated protocol;
    given the vendor's announcement it wrote an honest one with invented
    endpoints. Neither could be told apart from a correct file by reading it.
    Four HTTP requests could.

    Only `GET`, `HEAD` and `OPTIONS`, and only what the draft itself declares.
    Nothing here tries to discover the device's real API: this reports whether
    what was written matches what answers, and never guesses a replacement.
    """
    import asyncio

    checked: list[dict] = []
    for request in verifiable_requests(adapter):
        entry = {"action": request["action"], "section": request["section"],
                 "method": request["method"], "url": request["url"]}
        try:
            def _probe(req=request):
                import urllib.request
                r = urllib.request.Request(req["url"], method=req["method"])
                for k, v in (req["headers"] or {}).items():
                    r.add_header(k, str(v))
                with urllib.request.urlopen(r, timeout=timeout) as resp:
                    return resp.status
            status = await asyncio.wait_for(
                asyncio.to_thread(_probe), timeout=timeout + 1)
            entry["status"] = status
            entry["answered"] = 200 <= status < 400
        except Exception as exc:
            # A refused connection and a 404 mean different things: one says
            # nothing is listening, the other says something is listening and
            # this is not a route it has.
            entry["status"] = None
            entry["answered"] = False
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        checked.append(entry)

    unverifiable = unverifiable_entries(adapter)
    answered = [c for c in checked if c["answered"]]

    # The distinction the operator needs: "some of this is untested" is normal,
    # "nothing in this file answered" means the transport itself is wrong.
    verdict = "ok"
    if checked and not answered:
        # Only claimable when something WAS tried. An MQTT draft produced this
        # verdict about MQTT after the hub sent three HTTP requests to the
        # empty string — asserting a transport unreachable without having
        # spoken it once.
        verdict = "transport_unreachable"
    elif not checked:
        verdict = "nothing_testable"

    return {
        "verdict": verdict,
        "transport": (adapter.get("transport") or {}).get("kind", ""),
        "checked": checked,
        "unverifiable": unverifiable,
        "answered_count": len(answered),
        "checked_count": len(checked),
    }
