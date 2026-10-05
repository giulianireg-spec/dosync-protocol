"""Import W3C WoT Thing Descriptions as DoSync devices, so the hub governs them.

DoSync exports its devices as TD 1.1 Things (tools/export_thing_descriptions.py);
this is the other direction, for Things DoSync did not describe. A survey of the
701 distinct TDs in the W3C plugfest repository (w3c/wot-testing) shaped it: 322
have actions, 1,054 actions in all, and only 5% of those carry a semantic
``@type`` -- the meaning is in the name (``toggle``, ``openValve``,
``stopPump``). So each action is mapped in layers, and every mapping records how
it was made:

  dosync         the ``dosync:capability`` of a TD DoSync exported
  @type          a known semantic annotation (iot:TurnOn, saref:ToggleCommand, ...)
  name-verb      an exact verb pattern in the name (turnOn, openValve, stopPump)
  writable-property  a writable property ``brightness`` becomes ``set_brightness``
  own-name       nothing applied: the action keeps its own name, in snake_case

A mapped type matters for governance: ``openValve`` and ``closeValve`` become
``open`` and ``close``, an opposite pair, so no plan can send a device both. An
action that keeps its own name is governed all the same -- ``operate_device``
grants any declared action, with every other guarantee. When two affordances of
one Thing would map to the same type (``startPump``, ``startHeater``), both keep
their own names: the hub must always know which one to invoke.

What a third-party TD never sets: a device's place and its emergency actions.
Those are the operator's (spec §2), assigned after import. A TD DoSync exported
carries them in the ``dosync:`` vocabulary, and they are imported from there.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from .models import ActuatorSpec, CapabilityManifest, DeviceCategory, SensorSpec

#: Exact single-word or whole-name verbs.
_WHOLE = {"on": "turn_on", "off": "turn_off", "toggle": "toggle", "lock": "lock",
          "unlock": "unlock", "open": "open", "close": "close", "start": "start",
          "stop": "stop", "arm": "arm", "disarm": "disarm", "reset": "reset"}
#: [turn|switch|power] + on/off.
_SWITCH_VERBS = {"turn", "switch", "power"}
#: A leading verb that keeps its meaning whatever object follows it.
_LEADING = {"open", "close", "start", "stop", "lock", "unlock", "arm", "disarm"}
#: Known semantic annotations of actions (the last segment, lower-cased).
_SEMANTIC = {"turnon": "turn_on", "turnonaction": "turn_on", "switchoncommand": "turn_on",
             "oncommand": "turn_on", "turnoff": "turn_off", "turnoffaction": "turn_off",
             "switchoffcommand": "turn_off", "offcommand": "turn_off",
             "togglecommand": "toggle", "toggleaction": "toggle", "lockaction": "lock",
             "unlockaction": "unlock", "opencommand": "open", "closecommand": "close",
             "startcommand": "start", "stopcommand": "stop", "setlevelaction": "set_level",
             "setlevelcommand": "set_level", "fadeaction": "fade"}

_TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokens(name: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(str(name))]


def snake(name: str) -> str:
    return "_".join(tokens(name)) or "action"


def _semantic(annotation) -> str | None:
    for t in (annotation if isinstance(annotation, list) else [annotation]):
        if isinstance(t, str):
            key = re.sub(r"[^a-z]", "", t.split(":")[-1].split("#")[-1].split("/")[-1].lower())
            if key in _SEMANTIC:
                return _SEMANTIC[key]
    return None


def map_action(name: str, affordance: dict) -> tuple[str, str]:
    """The DoSync action type of a TD action, and how it was decided."""
    cap = affordance.get("dosync:capability")
    if isinstance(cap, str) and cap:
        return cap, "dosync"
    sem = _semantic(affordance.get("@type"))
    if sem:
        return sem, "@type"
    tk = tokens(name)
    joined = "".join(tk)
    if joined in _WHOLE:
        return _WHOLE[joined], "name-verb"
    if len(tk) == 2 and tk[0] in _SWITCH_VERBS and tk[1] in ("on", "off"):
        return f"turn_{tk[1]}", "name-verb"
    if len(tk) >= 2 and tk[0] in _LEADING:
        return tk[0], "name-verb"
    if len(tk) >= 2 and tk[0] == "set":
        return "set_" + "_".join(tk[1:]), "name-verb"
    return snake(name), "own-name"


def _forms(affordance: dict, base: str | None, ops: tuple[str, ...]) -> list[dict]:
    out = []
    for f in affordance.get("forms") or []:
        if not isinstance(f, dict) or not f.get("href"):
            continue
        op = f.get("op")
        op_list = op if isinstance(op, list) else ([op] if op else [])
        if op_list and not any(o in ops for o in op_list):
            continue
        href = urljoin(base, f["href"]) if base else f["href"]
        out.append({"href": href, "op": op_list or list(ops[:1]),
                    "method": f.get("htv:methodName"),
                    "contentType": f.get("contentType", "application/json")})
    return out


def executable(forms: list[dict]) -> bool:
    return any(f["href"].startswith(("http://", "https://")) for f in forms)


#: The id DoSync's exporter gives a TD (tools/export_thing_descriptions.py).
_DOSYNC_URN = "urn:dosync:device:"


def device_id_for(td: dict) -> str:
    raw = td.get("id") or td.get("title") or "thing"
    if isinstance(raw, str) and raw.startswith(_DOSYNC_URN) and raw[len(_DOSYNC_URN):]:
        return raw[len(_DOSYNC_URN):]   # a TD DoSync exported: the device keeps its id
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw)).strip("-.").lower() or "thing"
    if len(slug) > 56:
        slug = slug[:47].rstrip("-.") + "-" + hashlib.sha256(str(raw).encode()).hexdigest()[:8]
    return slug


def import_td(td: dict) -> tuple[CapabilityManifest, dict]:
    """A manifest for the hub, and a report of how each affordance was mapped."""
    if not isinstance(td, dict) or not td.get("title"):
        raise ValueError("not a Thing Description: no title")
    base = td.get("base")
    candidates = []        # (type, provenance, kind, name, affordance)
    for name, a in (td.get("actions") or {}).items():
        if isinstance(a, dict):
            t, how = map_action(name, a)
            candidates.append((t, how, "action", name, a))
    sensors, readable = [], []
    for name, p in (td.get("properties") or {}).items():
        if not isinstance(p, dict):
            continue
        writable = not p.get("readOnly", False) and "writeproperty" in _ops_of(p, default_write=True)
        if writable:
            cap = p.get("dosync:capability")
            t, how = (cap, "dosync") if isinstance(cap, str) and cap else ("set_" + snake(name), "writable-property")
            candidates.append((t, how, "property", name, p))
        exported_sensor = p.get("readOnly", False) and isinstance(p.get("dosync:capability"), str)
        if not p.get("writeOnly", False) and (exported_sensor or p.get("type") in ("number", "integer", "boolean")):
            readable.append((name, p))
    # One type, one affordance: colliding mappings keep their own names.
    counts: dict[str, int] = {}
    for t, *_ in candidates:
        counts[t] = counts.get(t, 0) + 1
    affordances, provenance, actuators = {}, {}, []
    for t, how, kind, name, a in candidates:
        if counts[t] > 1 and how != "dosync":
            t, how = snake(name) if kind == "action" else "set_" + snake(name), "own-name (collision)"
            if t in affordances:
                t = f"{t}_{kind}"
        ops = ("invokeaction",) if kind == "action" else ("writeproperty",)
        forms = _forms(a, base, ops)
        affordances[t] = {"kind": kind, "name": name, "forms": forms,
                          "input": a.get("input") if kind == "action" else {"type": a.get("type")}}
        provenance[t] = how
        actuators.append(ActuatorSpec(t, t, str(a.get("description") or a.get("title") or name)[:200],
                                      params_schema=(a.get("input") if kind == "action" and isinstance(a.get("input"), dict) else {})))
    sensor_forms = {}
    for name, p in readable:
        cap = p.get("dosync:capability") if p.get("readOnly", False) else None
        st = cap if isinstance(cap, str) and cap else snake(name)
        sid = name if cap else st
        if sid in sensor_forms:
            continue
        sensor_forms[sid] = {"name": name, "type": st, "forms": _forms(p, base, ("readproperty",))}
        kind = p.get("dosync:sensorKind")
        sensors.append(SensorSpec(id=sid, type=st, description=str(p.get("description") or p.get("title") or name)[:200],
                                  unit=p.get("unit"), **({"kind": kind} if isinstance(kind, str) else {})))
    if isinstance(td.get("dosync:tags"), list):
        # A TD DoSync exported: its tags exactly. Tags rank devices in
        # resolution, so one added here could change a plan.
        tags = [t for t in td["dosync:tags"] if isinstance(t, str)]
    else:
        tags = ["wot"] + sorted({re.sub(r"[^a-z0-9]", "", str(x).split(":")[-1].lower())
                                 for x in (td.get("@type") if isinstance(td.get("@type"), list) else [td.get("@type")])
                                 if x})[:4]
    emergency_actions = []
    for a in td.get("dosync:emergencyActions") or []:
        act, params = (a.get("action"), a.get("params") or {}) if isinstance(a, dict) else (a, {})
        if isinstance(act, str) and act in affordances:
            emergency_actions.append({"action": act, "params": dict(params)})
    manifest = CapabilityManifest(
        device_id=device_id_for(td), device_name=str(td["title"])[:120],
        manufacturer=str(td.get("schema:manufacturer") or td.get("manufacturer") or "WoT Thing")[:80],
        model=str(td.get("schema:model") or td.get("model") or "")[:80] or "Thing Description",
        firmware=str(td.get("version", {}).get("instance", "")) if isinstance(td.get("version"), dict) else "",
        category=(DeviceCategory(td["dosync:category"]) if td.get("dosync:category") in {c.value for c in DeviceCategory}
                  else DeviceCategory.ACTUATOR if actuators else DeviceCategory.SENSOR),
        tags=[t for t in tags if t], sensors=sensors, events=[], actuators=actuators,
        location=str(td.get("dosync:location") or ""),
        emergency_capable=bool(td.get("dosync:emergencyCapable", False)),
        emergency_actions=emergency_actions,
        adapter="wot",
        adapter_config={"td_id": td.get("id"), "base": base, "affordances": affordances,
                        "sensors": sensor_forms, "provenance": provenance,
                        "security": td.get("security")},
    )
    report = {"device_id": manifest.device_id, "title": manifest.device_name,
              "provenance": provenance,
              "executable": {t: executable(a["forms"]) for t, a in affordances.items()},
              "sensors": sorted(sensor_forms)}
    return manifest, report


def _ops_of(p: dict, default_write: bool) -> set[str]:
    ops = set()
    for f in p.get("forms") or []:
        op = f.get("op") if isinstance(f, dict) else None
        ops.update(op if isinstance(op, list) else ([op] if op else []))
    if not ops and default_write and not p.get("readOnly", False):
        ops = {"readproperty", "writeproperty"}
    return ops
