"""spec/openapi.json is the HTTP surface the reference hub actually exposes.

The REST API was described by hand in a BNF grammar that claimed precedence over
the specification, applied to protocol 0.1, and had drifted in every section --
thirteen household intent classes as a closed enum, a removed endpoint, events
with a field the API does not take. The HTTP surface is now generated from the
hub (tools/generate_openapi.py), and this compares the contract in the file --
every operation, its parameters, the body it accepts, and each model's fields --
with the one the hub generates. The text itself is not compared: FastAPI
versions render the same contract differently, and the floor job runs an older
one. FastAPI's own validation-error models are left out for the same reason.
"""
import json
from pathlib import Path

import dosync

SPEC = Path(__file__).resolve().parent.parent / "spec" / "openapi.json"
FRAMEWORK_MODELS = {"ValidationError", "HTTPValidationError"}


def _contract(spec):
    operations = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            body = (((op.get("requestBody") or {}).get("content") or {})
                    .get("application/json") or {}).get("schema") or {}
            operations[f"{method.upper()} {path}"] = {
                "parameters": sorted((p["in"], p["name"], bool(p.get("required")))
                                     for p in op.get("parameters", [])),
                "body": body.get("$ref", "").split("/")[-1] or ("object" if body else None),
            }
    models = {name: {"properties": sorted((m.get("properties") or {}).keys()),
                     "required": sorted(m.get("required") or [])}
              for name, m in (spec.get("components") or {}).get("schemas", {}).items()
              if name not in FRAMEWORK_MODELS}
    return operations, models


def test_the_published_surface_is_the_one_the_hub_exposes():
    import dosync.server as srv
    published_ops, published_models = _contract(json.loads(SPEC.read_text(encoding="utf-8")))
    live_ops, live_models = _contract(srv.app.openapi())
    assert sorted(published_ops) == sorted(live_ops), (
        "routes differ -- run tools/generate_openapi.py\n  only in the file: "
        f"{sorted(set(published_ops) - set(live_ops))}\n  only in the hub: "
        f"{sorted(set(live_ops) - set(published_ops))}")
    for op in live_ops:
        assert published_ops[op] == live_ops[op], f"{op}: {published_ops[op]} vs {live_ops[op]}"
    assert published_models == live_models, "request models differ -- run tools/generate_openapi.py"


def test_the_file_states_the_package_version():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert spec["info"]["version"] == dosync.__version__, \
        "spec/openapi.json was generated for another release -- run tools/generate_openapi.py"


def test_the_removed_endpoint_is_not_part_of_the_surface():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert "post" not in spec["paths"].get("/v1/intent", {}), \
        "POST /v1/intent answers 410; it is not part of the protocol's surface"
