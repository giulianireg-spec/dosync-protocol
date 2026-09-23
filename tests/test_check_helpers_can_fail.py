"""Every check() helper in the suite fails its test when the condition is false.

Thirteen test files, plus the MCP dynamic-intent tests, defined a check() that
printed a mark and bumped a counter. Pytest fails a test on an exception, not
on a print, so none of those ~155 checks could ever fail: forced false, the test
still passed. One of them was false for days -- the 10th extraction stopped a
geofence from blocking composite steps -- and the suite stayed green. Each
helper now raises; this keeps a new one from being written the old way.
"""
import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _silent_check_helpers():
    found = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "check":
                if not any(isinstance(n, (ast.Raise, ast.Assert)) for n in ast.walk(node)):
                    found.append(f"{path.name}:{node.lineno}")
    return found


def test_no_check_helper_only_prints():
    found = _silent_check_helpers()
    assert not found, (
        "check() helpers that cannot fail a test (no raise/assert):\n  "
        + "\n  ".join(found))
