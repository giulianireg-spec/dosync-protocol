"""What the MCP server tells an agent is in English.

The protocol's core is English by decision (see test_no_operator_data): an open
protocol whose agent-facing text carries one contributor's language cannot be
audited by most of the people it asks to adopt it. The general guard looks for
Spanish function words, and states its own limit: short Spanish with no accents
and no function words passes -- and so does this file's description check:
"Efecto Ambilight. Valores", a parameter description here, had neither. The MCP's
replies were exactly that kind of text --
"Acciones completadas:", "Severidad:", "WS clientes:" -- plus parameter
descriptions an agent reads to use a tool ("Tipo de evento", "Datos adicionales
del evento"), and a status header that opened with a house (🏠) and a family.

So this checks the agent-facing text two ways. Every label in a reply must come
from a closed English list: a new label, in any language, fails until someone
adds it on purpose -- a list of allowed words cannot be defeated the way the
guard's lists of forbidden ones were, five times. And every parameter
description must be free of accents and of Spanish function words, including
the `de`, `del` and `ej` the general guard does not list.
"""
import re
from pathlib import Path

MCP = Path(__file__).resolve().parent.parent / "dosync" / "mcp_server.py"

ALLOWED_LABELS = {
    "Actions", "Actions completed", "Adapter", "Already completed", "Audit log",
    "Cancelled by FailurePolicy", "Could not adopt", "Could not read devices",
    "Critical actions executed", "DB", "Device", "Devices",
    "Emergencies at an unknown location since start", "Error",
    "Error listing devices", "Event", "Event received by the hub", "Integrity",
    "Intent ID", "Intents refused since start", "Not searched", "Occupancy",
    "Protocol", "Scan failed", "Searched", "Severity",
    "Still pending / unreachable", "Tags", "Total", "Unknown tool", "WS clients",
    "address", "announced as", "id", "status",
}

_SPANISH = re.compile(
    r"\b(?:de|del|el|la|los|las|un|una|con|sin|por|para|que|como|cuando|su|sus|"
    r"este|esta|es|son|hay|al|ej|entre|desde|hasta|segun)\b", re.I)


def _reply_lines():
    """Source lines that build text returned to the agent: `text = ...`,
    `text += ...`, `text=...` in a TextContent, and the continuation lines of a
    `text = (` still open -- not tool descriptions or docstrings."""
    open_paren = False
    for n, line in enumerate(MCP.read_text(encoding="utf-8").splitlines(), 1):
        starts = re.match(r"^\s*text\s*\+?=", line) or "text=f\"" in line or "text=\"" in line
        if starts or (open_paren and re.match(r'^\s*f?"', line)):
            yield n, line
            if starts:
                open_paren = line.count("(") > line.count(")")
            elif line.rstrip().endswith(")"):
                open_paren = False
        else:
            open_paren = False


def _reply_labels():
    """The label before ':' in each string a reply is built from."""
    labels = []
    for n, line in _reply_lines():
        for literal in re.findall(r'f?"((?:[^"\\]|\\.)*)"', line):
            # Every segment between newlines: "\\nCritical actions executed:\\n"
            # ends in a newline, and reading only the last segment missed it.
            for segment in literal.replace("\\n", "\n").split("\n"):
                head = re.sub(r"^[^A-Za-z]+", "", segment.split("{", 1)[0])  # emoji, spaces
                if ":" in head:
                    labels.append((n, head.split(":", 1)[0].strip()))
    return labels


def test_every_reply_label_is_on_the_english_list():
    unknown = [f"line {n}: {label!r}" for n, label in _reply_labels()
               if label and label not in ALLOWED_LABELS]
    assert not unknown, (
        "labels the MCP shows an agent that are not on the English list "
        "(translate them, or add an English one on purpose):\n  " + "\n  ".join(unknown))


def test_every_parameter_description_is_english():
    src = MCP.read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r'"description"\s*:\s*\(?\s*((?:"[^"]*"\s*)+)', src):
        text = "".join(re.findall(r'"([^"]*)"', m.group(1)))
        if _SPANISH.search(text) or re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡]", text):
            bad.append(f"line {src[:m.start()].count(chr(10)) + 1}: {text[:80]}")
    assert not bad, "descriptions an agent reads are in Spanish:\n  " + "\n  ".join(bad)


def test_the_status_reply_does_not_open_with_a_house():
    src = MCP.read_text(encoding="utf-8")
    assert "🏠" not in src and "Familia" not in src, \
        "the agent's first view of a hub is a household"
