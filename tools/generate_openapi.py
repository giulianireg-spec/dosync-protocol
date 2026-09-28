#!/usr/bin/env python3
"""Write spec/openapi.json -- the protocol's HTTP surface -- from the reference hub.

The REST API a conforming hub exposes is defined by what this file says: every
route, its parameters, and the body it accepts. It is generated rather than
written, because every hand-maintained description of this API drifted from it
(the BNF grammar, the certification guide, the JSON schemas). Regenerate it
whenever a route changes; tests/test_openapi_contract.py fails until you do.

    python3 tools/generate_openapi.py
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("DOSYNC_DB", ":memory:")

from dosync.server import app  # noqa: E402

target = REPO / "spec" / "openapi.json"
target.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {target.relative_to(REPO)}")
