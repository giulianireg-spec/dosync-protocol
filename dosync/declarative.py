"""Declarative adapters — describe a device in a file instead of writing code.

Most of what a hub needs to reach a device is not interesting code. It is "send
this HTTP request, read this field of the response". Requiring Python for that
put a wall in front of anyone whose device is not among the eight adapters this
project happens to ship, and made "domain-agnostic" mean "agnostic across the
domains we already wrote".

A declarative adapter is a YAML or JSON file describing one device. The hub
loads it at startup and can then reach that device with no code written.

**It describes CAPABILITIES, not commands.** This is the design decision that
makes the difference between a working adapter and an HTTP client with extra
steps. A file that only said "POST /on turns it on" would let DoSync switch the
device and leave it invisible to everything that matters: the resolver would not
know it is a light, an emergency would not reach it, and policies could not name
it. So every action declares what it MEANS in DoSync's vocabulary — an actuator
type — and the device declares its tags and whether it is emergency-capable.

**What this cannot do**, stated plainly because promising otherwise would be the
kind of claim this project avoids: it speaks HTTP and MQTT. It cannot speak
Zigbee, Z-Wave, or any protocol needing pairing, session state, a binary
handshake, or a vendor SDK. An OPC-UA session is not a request. Those need a
real adapter, written in code — either an ecosystem adapter here or a
third-party package. A declarative file covers most simple devices and almost no
complex ones.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("dosync.declarative")

#: Extensions searched in a declarative adapter directory.
SUPPORTED_SUFFIXES = (".yaml", ".yml", ".json")

#: Transports a declarative file may name. Deliberately short: anything needing
#: pairing, session state or a vendor SDK is not describable in a file and is
#: refused at load rather than at the moment an intent needs it.
SUPPORTED_TRANSPORTS = {"http"}

#: Text left in a copied example that the operator forgot to replace.
PLACEHOLDER_MARKER = "REPLACE_WITH"


def _find_placeholders(node, found=None) -> list[str]:
    found = [] if found is None else found
    if isinstance(node, str):
        if PLACEHOLDER_MARKER in node:
            found.append(node[:40])
    elif isinstance(node, dict):
        for v in node.values():
            _find_placeholders(v, found)
    elif isinstance(node, list):
        for v in node:
            _find_placeholders(v, found)
    return found


class DeclarativeError(ValueError):
    """A declarative file that cannot be used, with the reason a human needs.

    Deliberately verbose: the audience for these messages is someone editing
    YAML who has never read the specification, and "KeyError: 'type'" tells them
    nothing about what to write instead.
    """


def _load_file(path: Path) -> dict:
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as e:      # pragma: no cover - install-time condition
            raise DeclarativeError(
                f"{path.name} is YAML but PyYAML is not installed. Either install "
                f"it (pip install pyyaml) or write the file as JSON — the two "
                f"formats are interchangeable here.") from e
        try:
            data = yaml.safe_load(text)
        except Exception as e:
            raise DeclarativeError(f"{path.name} is not valid YAML: {e}") from e
    else:
        try:
            data = json.loads(text)
        except Exception as e:
            raise DeclarativeError(f"{path.name} is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise DeclarativeError(
            f"{path.name} must contain a mapping at the top level "
            f"(device:, transport:, actions:), not a {type(data).__name__}.")
    return data


def build_manifest(data: dict, source: str = "<declarative>"):
    """Turn a declarative description into a CapabilityManifest.

    Every check here answers a question the operator can act on, because the
    person writing this file is the one who will read the error.
    """
    from .models import (
        ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory, SensorSpec,
    )

    device = data.get("device")
    if not isinstance(device, dict):
        raise DeclarativeError(
            f"{source}: missing a `device:` section. It needs at least an `id:` "
            f"and a `name:`, and should declare `tags:` so intents can find it.")

    device_id = str(device.get("id") or "").strip()
    if not device_id:
        raise DeclarativeError(f"{source}: device.id is required and must not be empty.")
    name = str(device.get("name") or device_id).strip()

    raw_category = str(device.get("category", "actuator")).lower()
    try:
        category = DeviceCategory(raw_category)
    except ValueError:
        valid = ", ".join(c.value for c in DeviceCategory)
        raise DeclarativeError(
            f"{source}: device.category '{raw_category}' is not one of: {valid}")

    tags = [str(t) for t in (device.get("tags") or [])]
    room = str(device.get("room", "")).strip()
    if room and room not in tags:
        # Accepted for readability — "room: kitchen" is what someone writing a
        # device file reaches for — and folded into tags, because that is how
        # every other device in DoSync expresses location and how the resolver
        # matches it.
        tags.append(room)
    if not tags:
        # Not fatal, but worth saying: a device with no tags is reachable by
        # direct action and invisible to every intent, which is almost never
        # what someone writing this file wants.
        log.warning("%s: device '%s' declares no tags, so no intent will ever "
                    "select it. Add tags like [light, emergency] to make it "
                    "participate in goals.", source, device_id)

    actions = data.get("actions") or {}
    if not isinstance(actions, dict) or not actions:
        raise DeclarativeError(
            f"{source}: at least one entry under `actions:` is required — a "
            f"device the hub cannot act on has nothing to contribute.")

    # A transport the hub cannot use must be refused HERE, not discovered when
    # an intent tries to act. The project already draws this line for scanning —
    # "searched" is reported separately from "not searchable" so that "found
    # nothing" cannot be confused with "did not look". A device that loads,
    # appears in the list with its actions, and fails only when an emergency
    # reaches it is the same confusion with worse timing.
    transport = data.get("transport") or {}
    kind = str(transport.get("kind", "http")).lower()
    if kind not in SUPPORTED_TRANSPORTS:
        raise DeclarativeError(
            f"{source}: transport.kind '{kind}' is not supported. Declarative "
            f"adapters speak: {', '.join(sorted(SUPPORTED_TRANSPORTS))}. A device "
            f"needing {kind} needs a code adapter — see the adapter guide.")
    if kind == "http" and not str(transport.get("base_url", "")).strip():
        raise DeclarativeError(
            f"{source}: transport.base_url is required for http devices, e.g. "
            f"base_url: http://192.168.1.40")

    # R2: an example copied without editing is the most likely first mistake.
    _unedited = _find_placeholders(data)
    if _unedited:
        log.warning("%s: still contains unedited example values (%s). The device "
                    "will load, but requests will carry those literal strings.",
                    source, ", ".join(_unedited[:3]))

    actuators = []
    for action_name, spec in actions.items():
        if not isinstance(spec, dict):
            raise DeclarativeError(
                f"{source}: action '{action_name}' must be a mapping with a "
                f"`type:` and a `request:`.")
        actuator_type = spec.get("type")
        if not actuator_type:
            raise DeclarativeError(
                f"{source}: action '{action_name}' has no `type:`. The type is "
                f"what the action MEANS to DoSync (turn_on, set_temperature, "
                f"lock, alarm…). Without it the hub can perform the action but "
                f"no intent can ever decide to.")
        actuators.append(ActuatorSpec(
            id=str(action_name), type=str(actuator_type),
            description=str(spec.get("description", ""))))

    sensors = []
    for sensor_name, spec in (data.get("sensors") or {}).items():
        if not isinstance(spec, dict):
            raise DeclarativeError(
                f"{source}: sensor '{sensor_name}' must be a mapping.")
        sensors.append(SensorSpec(
            id=str(sensor_name),
            type=str(spec.get("type", "number")),
            description=str(spec.get("description", "")),
            kind=str(spec.get("kind", "environment")),
        ))

    return CapabilityManifest(
        device_id=device_id,
        device_name=name,
        manufacturer=str(device.get("manufacturer", "declarative")),
        model=str(device.get("model", "declarative")),
        firmware=str(device.get("firmware", "n/a")),
        category=category,
        tags=tags,
        sensors=sensors,
        events=[],
        actuators=actuators,
        emergency_capable=bool(device.get("emergency_capable", False)),
        cert_tier=CertTier.BASIC,
        adapter="declarative",
        # The transport definition rides in adapter_config, which is what that
        # field is for — the generic adapter reads it back to know where to send
        # the request. A room is expressed as a tag (`living-room`), like every
        # other device in DoSync, rather than as a field only these devices have.
        adapter_config={
            "transport": data.get("transport") or {},
            "actions": actions,
            "sensors": data.get("sensors") or {},
        },
    )


def load_directory(directory: str = None) -> list[tuple[Any, dict]]:
    """Load every declarative adapter in a directory.

    Returns `(manifest, definition)` pairs. A file that cannot be parsed is
    logged and SKIPPED rather than stopping the hub: one malformed device
    description should not prevent a house from starting, and the operator needs
    the hub running to fix it.
    """
    if directory is None:
        directory = os.environ.get("DOSYNC_DECLARATIVE_DIR", "declarative")
    path = Path(directory)
    if not path.is_dir():
        return []

    loaded = []
    seen: dict[str, str] = {}
    for f in sorted(path.iterdir()):
        if f.suffix.lower() not in SUPPORTED_SUFFIXES or f.name.startswith("."):
            continue
        try:
            data = _load_file(f)
            manifest = build_manifest(data, source=f.name)
        except DeclarativeError as e:
            log.error("Declarative adapter %s NOT loaded: %s", f.name, e)
            continue
        except Exception as e:      # pragma: no cover - defensive
            log.error("Declarative adapter %s NOT loaded: %s", f.name, e)
            continue
        # R1: two files claiming the same device. Silently, the later one wins
        # by alphabetical accident — and the operator edits the file that has no
        # effect, indefinitely.
        clash = seen.get(manifest.device_id)
        if clash:
            log.error("Declarative adapter %s NOT loaded: device id '%s' is already "
                      "declared by %s. Two files describing one device means edits "
                      "to one of them silently do nothing — give them distinct ids.",
                      f.name, manifest.device_id, clash)
            continue
        seen[manifest.device_id] = f.name

        loaded.append((manifest, data))
        log.info("Declarative adapter loaded: %s (%s, %d action(s))",
                 f.name, manifest.device_id, len(manifest.actuators))
    return loaded
