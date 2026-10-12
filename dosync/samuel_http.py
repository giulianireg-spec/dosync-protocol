"""DoSync's implementation of the SAMUEL HTTP binding (SAMUEL-SPEC-v0.2 §11).

A small surface, separate from DoSync's own /v1 API, on which any hub -- DoSync or
not -- can be certified against SAMUEL with `samuel-certify`. It speaks the
SAMUEL model (§3), not DoSync's Capability Manifest, and has no `explain`: how a
hub chooses devices is its own business; what it lets through is SAMUEL's.

    GET  /samuel/v0                          what this hub implements (any credential)
    PUT  /samuel/v0/devices/{id}             a device in the SAMUEL model     (operator)
    PUT  /samuel/v0/classes/{name}           an intent class                  (operator)
    POST /samuel/v0/intents                  fire an intent; the decision     (agent)
    POST /samuel/v0/intents/{id}/confirm     release what was held            (operator)
    POST /samuel/v0/intents/{id}/deny        drop what was held               (operator)
    GET  /samuel/v0/audit                    the record, and whether it verifies (operator)
    PUT  /samuel/v0/policies                 exclusions and confirmation marks
                                             (operator; conformance mode only)

Two credentials, never the same: the agent's (``Authorization: Bearer``, as on
/v1) and the operator's (``X-Operator-Token``; DoSync also reads
``X-DoSync-Operator-Token``). Where a device is and what it does in an emergency
are the operator's decisions (SAMUEL §3), so registering a device needs the
operator credential here.

The kernel decides (dosync/samuel/); this module only translates.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Header, HTTPException

from .auth_fastapi import require_auth
from .samuel import INTENT_REFUSALS, admit

BINDING = "samuel/v0"
SPEC_VERSION = "0.2"

router = APIRouter(prefix="/samuel/v0", tags=["SAMUEL"])

#: Policies this binding added (conformance mode), so a new PUT replaces exactly
#: those and never the operator's own.
_binding_policies: list = []


def _srv():
    import dosync.server as srv          # late: the server builds the hub
    return srv


def _operator(x_operator_token, x_dosync_operator_token):
    srv = _srv()
    token = x_operator_token or x_dosync_operator_token
    if not srv._operator_credential_ok(token):
        raise HTTPException(status_code=403, detail={
            "reason": "operator_credential_required",
            "detail": "This operation is the operator's: send X-Operator-Token. An agent's "
                      "credential never suffices."})


def _direct_control_setting() -> str:
    """DoSync's DOSYNC_DIRECT_CONTROL (off | operator | on) in SAMUEL's terms."""
    return {"off": "closed", "operator": "operator", "on": "open"}[_srv().direct_control_mode()]


@router.get("")
def samuel_info(auth: str = Depends(require_auth)):
    """What this hub implements of SAMUEL. ``direct_control`` is G8's report:
    ``closed`` or ``operator`` conform; ``open`` is a development setting."""
    srv = _srv()
    from .samuel import OscillationGuard
    window, max_rev = OscillationGuard().settings()
    return {
        "binding": BINDING, "samuel": SPEC_VERSION,
        "implementation": f"DoSync {srv.__version__}",
        "resolves": True,
        "direct_control": _direct_control_setting(),
        # Who may place devices, set emergency actions and define classes on the
        # hub's other interfaces: "operator" conforms; "hub" means the hub
        # credential -- which an agent's tools hold -- can (SAMUEL §3).
        "configuration": srv.configuration_credential_mode(),
        "operator_credential": srv.operator_credential_state(),
        "oscillation": {"window_seconds": window, "max_reversals": max_rev},
        "confirmation_timeout_seconds": srv.hub._confirmation_timeout(),
        "conformance_mode": bool(srv._certify_mode),
        "intent_refusals": list(INTENT_REFUSALS),
    }


@router.put("/devices/{device_id}")
def samuel_put_device(device_id: str, body: dict,
                      x_operator_token: str | None = Header(default=None),
                      x_dosync_operator_token: str | None = Header(default=None),
                      auth: str = Depends(require_auth)):
    """A device in the SAMUEL model: ``actions`` [{type, params_schema?}],
    ``sensors`` [{type, kind?}], ``place``, ``emergency_capable``,
    ``emergency_actions`` [{action, params?}]."""
    _operator(x_operator_token, x_dosync_operator_token)
    srv = _srv()
    from .models import (ActuatorSpec, CapabilityManifest, DeviceCategory, SensorSpec,
                         normalize_emergency_actions, normalize_location)
    try:
        actions = body.get("actions") or []
        sensors = body.get("sensors") or []
        if not isinstance(actions, list) or not isinstance(sensors, list):
            raise ValueError("actions and sensors are lists")
        actuators = [ActuatorSpec(a["type"], a["type"], str(a.get("description", "")),
                                  dict(a.get("params_schema") or {})) for a in actions]
        sensor_specs = [SensorSpec(s["type"], s["type"], str(s.get("description", "")),
                                   **({"kind": s["kind"]} if s.get("kind") else {}))
                        for s in sensors]
        place = normalize_location(body["place"]) if body.get("place") else ""
        manifest = CapabilityManifest(
            device_id=device_id, device_name=str(body.get("name") or device_id),
            manufacturer="SAMUEL binding", model="samuel-device", firmware="",
            category=DeviceCategory.ACTUATOR if actuators else DeviceCategory.SENSOR,
            tags=[], sensors=sensor_specs, events=[], actuators=actuators,
            emergency_capable=bool(body.get("emergency_capable", False)),
            location=place)
        if body.get("emergency_actions"):
            manifest.emergency_actions = normalize_emergency_actions(
                body["emergency_actions"], {a.type for a in actuators})
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid SAMUEL device: {e}")
    previous = srv.hub.registry.get(device_id)
    srv.hub.register_device(manifest)
    # Registration never moves a device (DoSync keeps the operator's location on
    # re-registration); here the operator IS speaking, so the place is set and,
    # if it changed, recorded.
    current = srv.hub.registry.get(device_id)
    if current is not None and current.location != place:
        before = current.location
        current.location = place
        srv.hub.db.save_device(device_id, current.to_dict())
        srv.hub.audit_log.append({"type": "device_relocated", "device_id": device_id,
                                  "previous_location": before, "location": place})
    if current is not None and body.get("emergency_actions") is not None:
        current.emergency_actions = manifest.emergency_actions
        current.emergency_capable = manifest.emergency_capable
        srv.hub.db.save_device(device_id, current.to_dict())
    return {"device_id": device_id, "registered": previous is None, "place": place}


@router.put("/classes/{name}")
def samuel_put_class(name: str, body: dict,
                     x_operator_token: str | None = Header(default=None),
                     x_dosync_operator_token: str | None = Header(default=None),
                     auth: str = Depends(require_auth)):
    """An intent class: ``grants`` (action types, or ["*"]), ``sensors``,
    ``urgency`` (default), ``location_role`` (restricts | informs). A universal
    class cannot be redefined."""
    _operator(x_operator_token, x_dosync_operator_token)
    srv = _srv()
    from .models import IntentClass, Urgency
    try:
        IntentClass(name)
        Urgency(body.get("urgency", "info"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    existing = srv.hub.db.get_intent_class(name)
    if existing and existing.get("domain") == "universal":
        raise HTTPException(status_code=409, detail=f"'{name}' is a universal class")
    role = body.get("location_role", "restricts")
    if role not in ("restricts", "informs"):
        raise HTTPException(status_code=422, detail="location_role is restricts or informs")
    srv.hub.db.save_intent_class(
        name=name, urgency=body.get("urgency", "info"), resolution_tags=[],
        resolution_actuators=list(body.get("grants") or []),
        resolution_sensors=list(body.get("sensors") or []),
        description=str(body.get("description") or "SAMUEL class"), domain="samuel",
        location_role=role)
    return {"class": name, "grants": list(body.get("grants") or []), "location_role": role}


def _decision(result, location_not_found=None) -> dict:
    out = {
        "intent_id": result.intent_id,
        "status": result.status,
        "ran": [{"device_id": r.device_id, "action": r.action} for r in result.results],
        "refused": [{"device_id": x.get("device_id"), "action": x.get("action"),
                     "reason": x.get("reason")} for x in (result.refused_proposals or [])],
        "held": [{"device_id": h.get("device_id"), "action": h.get("action"),
                  "reason": h.get("reason")} for h in (result.held_for_confirmation or [])],
    }
    if location_not_found:
        out["location_not_found"] = location_not_found
    return out


@router.post("/intents")
async def samuel_fire_intent(body: dict, auth: str = Depends(require_auth)):
    """Fire an intent: ``class``, ``urgency``, ``context`` (location,
    action_types, proposed_actions). Answers with the decision: what ran, what
    was refused and why, what is held for a person. A refusal of the intent as a
    whole is 422 with its reason."""
    srv = _srv()
    from .models import Intent, IntentClass, Urgency
    name = body.get("class")
    try:
        cls = IntentClass(str(name))
        urgency = Urgency(body.get("urgency", "info"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"reason": "invalid_request", "detail": str(e)})
    context = body.get("context") or {}
    if not isinstance(context, dict):
        raise HTTPException(status_code=422, detail={"reason": "invalid_request",
                                                     "detail": "context is an object"})
    adm = admit(str(cls), srv.hub.db.get_intent_class(str(cls)), urgency, context,
                srv.hub.registry.active())
    if not adm.admitted:
        srv._count_rejection(adm.refused, str(cls), urgency.value)
        srv._record_refusal(adm.refused, str(cls), urgency.value, adm.detail)
        raise HTTPException(status_code=422, detail={"reason": adm.refused, "detail": adm.detail})
    intent = Intent(intent=cls, urgency=urgency, source="samuel", context=adm.context)
    if adm.location_not_found:
        srv.hub.audit_log.append({"type": "emergency_location_not_found",
                                  "intent_id": intent.intent_id, "intent": str(cls),
                                  "location": adm.location_not_found,
                                  "note": "no device at this location; acting on every capable device"})
    result = await srv.hub.execute_intent(intent, srv.executor)
    return _decision(result, adm.location_not_found)


@router.post("/intents/{intent_id}/confirm")
async def samuel_confirm(intent_id: str,
                         x_operator_token: str | None = Header(default=None),
                         x_dosync_operator_token: str | None = Header(default=None),
                         auth: str = Depends(require_auth)):
    _operator(x_operator_token, x_dosync_operator_token)
    srv = _srv()
    res = await srv.hub.confirm_intent(intent_id, srv.executor, operator="samuel-operator")
    if res["status"] == "not_found":
        raise HTTPException(status_code=404, detail="nothing held for this intent")
    return res


@router.post("/intents/{intent_id}/deny")
def samuel_deny(intent_id: str,
                x_operator_token: str | None = Header(default=None),
                x_dosync_operator_token: str | None = Header(default=None),
                auth: str = Depends(require_auth)):
    _operator(x_operator_token, x_dosync_operator_token)
    res = _srv().hub.deny_intent(intent_id, operator="samuel-operator")
    if res["status"] == "not_found":
        raise HTTPException(status_code=404, detail="nothing held for this intent")
    return res


@router.get("/audit")
def samuel_audit(since: int = 0,
                 x_operator_token: str | None = Header(default=None),
                 x_dosync_operator_token: str | None = Header(default=None),
                 auth: str = Depends(require_auth)):
    """The record from position ``since``, and whether the whole chain verifies."""
    _operator(x_operator_token, x_dosync_operator_token)
    log = _srv().hub.audit_log
    entries = log.entries()
    return {"verified": bool(log.verify()), "length": len(entries),
            "entries": entries[max(0, since):]}


@router.put("/policies")
def samuel_put_policies(body: dict,
                        x_operator_token: str | None = Header(default=None),
                        x_dosync_operator_token: str | None = Header(default=None),
                        auth: str = Depends(require_auth)):
    """Conformance mode only: replace the policies THIS binding set with
    ``exclusions`` [{classes, devices}] (G7) and ``confirmations``
    [{actions, devices?, even_in_emergency?}] (G9). A deployment's own policies
    are never touched, and outside conformance mode policies are configured the
    deployment's own way -- never over an API an agent might reach."""
    _operator(x_operator_token, x_dosync_operator_token)
    srv = _srv()
    if not srv._certify_mode:
        raise HTTPException(status_code=403, detail={
            "reason": "conformance_mode_only",
            "detail": "Policies are set over this API only on a hub in conformance mode."})
    from .policies import DeviceExclusionPolicy, PolicyEngine, RequireConfirmationPolicy
    engine = srv.hub.policy_engine
    if engine is None:
        engine = srv.hub.policy_engine = PolicyEngine()
    engine._policies = [p for p in engine._policies if not any(p is b for b in _binding_policies)]
    _binding_policies.clear()
    try:
        for e in body.get("exclusions") or []:
            _binding_policies.append(DeviceExclusionPolicy(
                intent_classes=list(e["classes"]), excluded_device_ids=list(e["devices"]),
                reason="SAMUEL conformance exclusion", bypass_on_emergency=False))
        for c in body.get("confirmations") or []:
            _binding_policies.append(RequireConfirmationPolicy(
                list(c["actions"]), device_ids=c.get("devices"),
                even_in_emergency=bool(c.get("even_in_emergency", False)),
                reason="SAMUEL conformance confirmation"))
    except (KeyError, TypeError) as e:
        raise HTTPException(status_code=422, detail=f"Invalid policies: {e}")
    for p in _binding_policies:
        engine.add(p)
    return {"exclusions": len(body.get("exclusions") or []),
            "confirmations": len(body.get("confirmations") or []), "at": time.time()}
