"""DoSync devices as W3C WoT Thing Descriptions (TD 1.1), served by the hub.

The other half of `wot_import.py`. Importing lets a Thing described by a TD be
governed; exporting lets any WoT consumer -- one that knows nothing of DoSync,
such as Eclipse Thingweb node-wot -- discover the hub's devices and use them.

What makes the export DoSync's and not a copy of the device: the *forms* address
the hub, never the device. Invoking an action goes through the hub's governed
binding (``POST /v1/things/{id}/actions/{name}``), which fires it as
``operate_device`` with that single proposal -- so the device must declare it
(G1), the class must grant it (G2), the operator's policies apply (G7), an action
the operator marked waits for a person (G9, answered 202), and everything is on
the record (G6). The TD is the map of the body; the forms run through the nervous
system. The device's own address never appears.

Governance terms TD 1.1 does not define -- the capability an action realises, the
operator-set place, emergency participation and actions, a sensor's kind -- travel
in the ``dosync:`` vocabulary (https://dosync.dev/ns/governance#, ns/), declared
in the JSON-LD @context.

Moved into the package from tools/export_thing_descriptions.py on 2026-10-12;
the tool now imports from here.
"""
from __future__ import annotations

from typing import Any

from .models import (ActuatorSpec, CapabilityManifest, DeviceCategory, EventSpec,
                     SensorSpec, Severity)

TD_CONTEXT = "https://www.w3.org/2022/wot/td/v1.1"
DOSYNC_NS = "https://dosync.dev/ns/governance#"
TD_MEDIA_TYPE = "application/td+json"


# What every governed invocation answers -- the governance is visible to the
# consumer: it ran, it waits for a person, or it is still running.
GOVERNED_OUTPUT = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "held_for_confirmation", "running"],
                   "description": "done: it ran. held_for_confirmation: the operator marked "
                                  "this action; a person releases it (HTTP 202). running: "
                                  "long-running, follow the intent (HTTP 202)."},
        "intent_id": {"type": "string"},
        "response": {"description": "the device's own answer, when it ran"},
        "simulated": {"type": "boolean"},
    },
    "required": ["status", "intent_id"],
}


def thing_id(device_id: str) -> str:
    return f"urn:dosync:device:{device_id}"


def _security(auth_required: bool) -> dict:
    if auth_required:
        return {"securityDefinitions": {"bearer_sc": {"scheme": "bearer", "in": "header",
                                                      "name": "Authorization"}},
                "security": "bearer_sc"}
    return {"securityDefinitions": {"nosec_sc": {"scheme": "nosec"}}, "security": "nosec_sc"}


def action_names(m: CapabilityManifest) -> dict[str, ActuatorSpec]:
    """TD action name -> actuator. The actuator's id, which is unique per device."""
    return {a.id: a for a in m.actuators}


def manifest_to_td(m: CapabilityManifest, base: str = "https://dosync-hub.local:47200/",
                   auth_required: bool = True) -> dict:
    """The TD 1.1 a consumer reads for this device, with governed forms."""
    base = base if base.endswith("/") else base + "/"
    ws = base.replace("https://", "wss://").replace("http://", "ws://") + "ws"
    path = f"v1/things/{m.device_id}"
    td: dict[str, Any] = {
        "@context": [TD_CONTEXT, {"dosync": DOSYNC_NS}],
        "id": thing_id(m.device_id),
        "title": m.device_name,
        "base": base,
        **_security(auth_required),
        "dosync:category": m.category.value,
        "dosync:tags": list(m.tags),
        "dosync:emergencyCapable": bool(m.emergency_capable),
        "properties": {}, "actions": {}, "events": {},
        "links": [{"rel": "collection", "href": "v1/things", "type": "application/json"}],
    }
    if m.location:
        td["dosync:location"] = m.location
    if m.emergency_actions:
        td["dosync:emergencyActions"] = [dict(a) for a in m.emergency_actions]
    for name, a in action_names(m).items():
        action: dict[str, Any] = {
            "title": a.description or a.id,
            "dosync:capability": a.type,
            # long-running actions answer 202 with where to follow them
            "synchronous": a.execution_model == "instant",
            "forms": [{"href": f"{path}/actions/{name}", "op": "invokeaction",
                       "htv:methodName": "POST", "contentType": "application/json"}],
        }
        if a.params_schema:
            # The action's input IS its parameters -- exactly as declared, so a
            # round trip returns the same description.
            action["input"] = dict(a.params_schema)
        action["output"] = GOVERNED_OUTPUT
        td["actions"][name] = action
    for s in m.sensors:
        prop: dict[str, Any] = {
            "title": s.description or s.id, "readOnly": True, "observable": True,
            "dosync:capability": s.type, "dosync:sensorKind": s.kind,
            "forms": [{"href": f"{path}/properties/{s.id}", "op": "readproperty",
                       "htv:methodName": "GET", "contentType": "application/json"},
                      {"href": ws, "op": "observeproperty", "subprotocol": "dosync-ws"}],
        }
        if s.unit or s.range:
            # A quantity with a unit or bounds is a number; without either the
            # hub does not know the value's type and does not invent one.
            prop["type"] = "number"
        if s.unit:
            prop["unit"] = s.unit
        if s.range and len(s.range) == 2:
            prop["minimum"], prop["maximum"] = s.range
        td["properties"][s.id] = prop
    for e in m.events:
        td["events"][e.id] = {"title": e.description or e.id,
                              "dosync:severity": e.severity.value,
                              "forms": [{"href": ws, "op": "subscribeevent",
                                         "subprotocol": "dosync-ws"}]}
    return td


def td_to_manifest(td: dict) -> CapabilityManifest:
    """Rebuild, from an exported TD alone, everything governance reads."""
    device_id = td["id"].rsplit(":", 1)[-1]
    actuators = [ActuatorSpec(k, v["dosync:capability"], v.get("title", ""),
                              params_schema=dict(v.get("input") or {}),
                              execution_model="instant" if v.get("synchronous", True) else "long_running")
                 for k, v in (td.get("actions") or {}).items()]
    sensors = [SensorSpec(id=k, type=v["dosync:capability"], description=v.get("title", ""),
                          unit=v.get("unit"),
                          range=([v["minimum"], v["maximum"]] if "minimum" in v and "maximum" in v else None),
                          kind=v.get("dosync:sensorKind", "environment"))
               for k, v in (td.get("properties") or {}).items()]
    events = [EventSpec(k, Severity(v.get("dosync:severity", "info")), v.get("title", ""))
              for k, v in (td.get("events") or {}).items()]
    return CapabilityManifest(
        device_id=device_id, device_name=td["title"], manufacturer="td", model="td", firmware="td",
        category=DeviceCategory(td["dosync:category"]), tags=list(td.get("dosync:tags") or []),
        actuators=actuators, sensors=sensors, events=events,
        emergency_capable=bool(td.get("dosync:emergencyCapable")),
        emergency_actions=[dict(a) for a in (td.get("dosync:emergencyActions") or [])],
        location=td.get("dosync:location", ""))


def directory_td(base: str, auth_required: bool = True) -> dict:
    """The hub as a WoT Thing Description Directory, for ``/.well-known/wot``
    (WoT Discovery 1.0, introduction phase). Public, and says nothing about any
    device: reading the directory itself needs the hub credential."""
    base = base if base.endswith("/") else base + "/"
    return {
        "@context": [TD_CONTEXT, "https://www.w3.org/2022/wot/discovery",
                     {"dosync": DOSYNC_NS}],
        "@type": "ThingDirectory",
        "id": "urn:dosync:hub:directory",
        "title": "DoSync hub — governed Thing directory",
        "description": "Every device this hub governs, as a Thing Description whose forms "
                       "run through SAMUEL: declared, authorized, policed, held for a person "
                       "when the operator marked it, recorded.",
        "base": base,
        **_security(auth_required),
        "properties": {
            "things": {
                "title": "Thing Descriptions of every governed device",
                "readOnly": True,
                "type": "array", "items": {"type": "object"},
                "forms": [{"href": "v1/things", "op": "readproperty",
                           "htv:methodName": "GET", "contentType": "application/json"}],
            },
        },
        "actions": {
            "retrieveThing": {
                "title": "One Thing Description, by device id",
                "uriVariables": {"id": {"type": "string"}},
                "safe": True, "idempotent": True,
                "output": {"type": "object"},
                "forms": [{"href": "v1/things/{id}", "op": "invokeaction",
                           "htv:methodName": "GET", "contentType": TD_MEDIA_TYPE}],
            },
        },
        "links": [{"rel": "describedby",
                   "href": "https://github.com/giulianireg-spec/dosync-protocol/blob/main/spec/SAMUEL-SPEC-v0.2.md",
                   "type": "text/markdown"}],
    }
