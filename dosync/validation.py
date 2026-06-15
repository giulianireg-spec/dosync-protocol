"""
DoSync — Parameter Schema Validation
====================================
Validates actuator parameter schemas (and, optionally, incoming action params)
against JSON Schema draft 2020-12.

Two distinct validations, per the design panel:

1. MANIFEST validation (integrity of the standard): when a device registers, is
   each actuator's `params_schema` itself a well-formed JSON Schema? This protects
   the standard — a manifest claiming JSON Schema must actually be valid JSON Schema.

2. PARAMS validation (execution policy, optional): when an action is about to run,
   do the supplied params satisfy the actuator's schema? This is a per-request
   policy — it can be disabled on the emergency path where latency matters.

Graceful degradation (panel condition): the `jsonschema` library is an OPTIONAL
dependency. If it is not installed, the hub still runs — validation is skipped with
a visible warning, never a hard failure. The dumb device never needs this library;
validation is the hub's responsibility.

The protocol commits to JSON Schema draft 2020-12 in the spec. The library is an
implementation choice of this reference implementation, not part of the standard.
"""

from __future__ import annotations
import logging

log = logging.getLogger("dosync.validation")

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError, ValidationError
    _JSONSCHEMA_AVAILABLE = True
except Exception:  # pragma: no cover - depends on host
    Draft202012Validator = None  # type: ignore
    SchemaError = ValidationError = Exception  # type: ignore
    _JSONSCHEMA_AVAILABLE = False


def jsonschema_available() -> bool:
    """Whether schema validation is active on this host."""
    return _JSONSCHEMA_AVAILABLE


def is_valid_json_schema(schema: dict) -> tuple[bool, str | None]:
    """Check that `schema` is itself a well-formed JSON Schema (draft 2020-12).

    An empty dict {} means 'no parameters' and is treated as valid.
    Returns (ok, error_message). If jsonschema is unavailable, returns (True, None)
    with a logged warning — degradation, not failure.
    """
    if not schema:
        return True, None
    if not _JSONSCHEMA_AVAILABLE:
        log.warning("Schema validation skipped — jsonschema not installed")
        return True, None
    try:
        Draft202012Validator.check_schema(schema)
        return True, None
    except SchemaError as e:
        return False, f"invalid JSON Schema: {e.message}"


def validate_manifest_schemas(manifest) -> list[str]:
    """Validate every actuator's params_schema in a manifest.

    Returns a list of human-readable problems (empty list = all valid). Used at
    device registration to protect the integrity of the standard.
    """
    problems: list[str] = []
    for actuator in getattr(manifest, "actuators", []):
        schema = getattr(actuator, "params_schema", None) or {}
        ok, err = is_valid_json_schema(schema)
        if not ok:
            problems.append(f"actuator '{actuator.type}': {err}")
    return problems


def validate_params(schema: dict, params: dict) -> tuple[bool, str | None]:
    """Validate action params against an actuator's JSON Schema.

    Returns (ok, error_message). Empty schema accepts anything. If jsonschema is
    unavailable, returns (True, None) — degradation, not failure. This is the
    OPTIONAL per-request validation; callers may skip it on the emergency path.
    """
    if not schema:
        return True, None
    if not _JSONSCHEMA_AVAILABLE:
        return True, None
    try:
        Draft202012Validator(schema).validate(params or {})
        return True, None
    except ValidationError as e:
        return False, f"parameter validation failed: {e.message}"
    except SchemaError as e:
        # The schema itself is broken — report distinctly from a params problem.
        return False, f"actuator schema is invalid: {e.message}"
