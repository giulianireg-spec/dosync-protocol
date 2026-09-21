"""No module in the package refers to a name it never defined or imported.

Four bugs of the same shape reached the reference hub or PyPI while the full
suite stayed green: a module extracted from hub.py left an import behind, or a
shared helper was used without being imported, and the name only failed on the
code path that reached it -- the state refresher's default interval, restoring
presence signals, recording an execution metric, reporting why an SMS failed.
Two of those paths sat inside try/except blocks that swallowed the NameError,
so nothing in the logs of a normal run pointed at them.

Tests only catch what they execute. A static check reads every line. This one
runs pyflakes over the package and fails on any undefined name. pyflakes is a
declared test dependency (requirements-dev.txt): if it is missing this errors
loudly instead of skipping, because a check that silently does not run is how
the first of those bugs got through.
"""
import ast
from pathlib import Path

from pyflakes import checker, messages

PACKAGE = Path(__file__).resolve().parent.parent / "dosync"


def _undefined_names():
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for msg in checker.Checker(tree, filename=str(path)).messages:
            if isinstance(msg, messages.UndefinedName):
                rel = path.relative_to(PACKAGE.parent)
                found.append(f"{rel}:{msg.lineno}: {msg.message_args[0]}")
    return found


def test_no_module_uses_an_undefined_name():
    found = _undefined_names()
    assert not found, (
        "undefined names -- each one raises NameError on the path that reaches "
        "it:\n  " + "\n  ".join(found))
