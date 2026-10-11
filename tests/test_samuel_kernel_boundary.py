"""The SAMUEL kernel stands alone: its modules import nothing of DoSync but the
data models, and nothing outside the standard library.

That boundary is what lets SAMUEL be implemented without DoSync -- in another
language, from these files -- and what lets the kernel move to its own package
the day a second implementation needs it. A module that reaches for the
database, the REST server, an adapter, the resolver or the policy engine here
is the kernel growing a dependency on one implementation of the body.
"""
import ast
import sys
from pathlib import Path

KERNEL = Path(__file__).resolve().parent.parent / "dosync" / "samuel"
ALLOWED_DOSYNC = {"models"}             # relative imports: from ..models import ...
ALLOWED_SIBLINGS = {p.stem for p in KERNEL.glob("*.py")}


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield 0, a.name
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or ""


def test_the_kernel_imports_only_models_and_the_standard_library():
    bad = []
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for path in sorted(KERNEL.glob("*.py")):
        for level, module in _imports(path):
            top = module.split(".")[0]
            if level == 1:          # from .x import  -- a sibling in the kernel
                if top and top not in ALLOWED_SIBLINGS:
                    bad.append(f"{path.name}: from .{module}")
            elif level == 2:        # from ..x import -- DoSync
                if top not in ALLOWED_DOSYNC:
                    bad.append(f"{path.name}: from ..{module}")
            elif level > 2 or (level == 0 and top not in stdlib):
                bad.append(f"{path.name}: import {module}")
    assert not bad, "the SAMUEL kernel must not depend on the hub: " + ", ".join(bad)


def test_the_hub_and_the_rest_surface_use_the_kernel():
    """The rules are not copied back: both doors call the kernel."""
    root = KERNEL.parent
    hub = (root / "hub.py").read_text(encoding="utf-8")
    server = (root / "server.py").read_text(encoding="utf-8")
    assert "from .samuel import admit" in hub
    assert "from dosync.samuel import admit" in server
    for refusal in ("ambiguous_actions", "proposals_required", "invalid_proposals"):
        assert f'_count_rejection("{refusal}"' not in server, \
            f"{refusal} is decided by the kernel, not re-decided in server.py"
