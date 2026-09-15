"""The capability registry.

The sixth of the eleven responsibilities extracted from `hub.py`: the store of
device manifests and the queries over it -- by tag (via an inverted index), by
actuator, and for emergency-capable devices. It is a self-contained thing, a
dictionary with two indexes, and it had been sharing a file with the
orchestrator that consults it.

`is_quarantined` stays in `resolvers` and is imported; the sensor-type warning
moves here because `register` is its only caller. Re-exported from `hub` so
every existing import keeps working unchanged -- an extraction that forces
callers to move at the same time is a rewrite wearing an extraction's clothes.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .models import CapabilityManifest
from .resolvers import is_quarantined

log = logging.getLogger("dosync.hub")


def _warn_if_sensor_type_is_an_event(manifest) -> None:
    """A sensor type that matches one of this device's own event ids.

    The deployment's PIR declared its sensor type as `motion_detected`. That
    name exists in this project — the policy engine weights
    `motion_detected at night` as a possible intrusion — but as an EVENT, not a
    measurement. The device was excluded from every alert asking for `motion`,
    and nothing flagged it, because nothing knew the two vocabularies were
    distinct.

    A warning and not a rejection. Unknown sensor types are allowed by design:
    the HA bridge reads `device_class` verbatim so that a class DoSync has never
    heard of arrives as itself. Refusing here would close that door to catch a
    naming slip.

    The signal is narrow on purpose — the type matches an event id the SAME
    device declares. Two devices in a deployment using each other's vocabulary
    is not evidence of anything.
    """
    event_ids = {e.id for e in getattr(manifest, "events", []) or []}
    if not event_ids:
        return
    for spec in getattr(manifest, "sensors", []) or []:
        if spec.type in event_ids:
            log.warning(
                "Device %s declares sensor '%s' with type '%s', which is also "
                "one of its own event ids. A sensor type says what is measured "
                "(`motion`); an event id says what happened (`motion_detected`). "
                "If that was the intent, ignore this — otherwise the device will "
                "not match intents asking for the measurement. See "
                "spec/CAPABILITY-TYPES.md",
                manifest.device_id, spec.id, spec.type)


class CapabilityRegistry:
    """
    Stores device manifests and answers capability queries.
    In production this would persist to disk / SQLite.
    """

    def __init__(self):
        self._devices: dict[str, CapabilityManifest] = {}
        self._listeners: list[Callable] = []
        # Inverted tag index for O(1) device lookup by tag.
        # Maps tag -> set of device_ids that declare that tag.
        self._tag_index: dict[str, set[str]] = {}
        # Emergency-capable device ids for O(1) emergency lookup
        self._emergency_ids: set[str] = set()

    def register(self, manifest: CapabilityManifest) -> None:
        _warn_if_sensor_type_is_an_event(manifest)
        old = self._devices.get(manifest.device_id)
        if old:
            for tag in old.tags:
                self._tag_index.get(tag, set()).discard(manifest.device_id)
            self._emergency_ids.discard(manifest.device_id)
        self._devices[manifest.device_id] = manifest
        for tag in manifest.tags:
            if tag not in self._tag_index:
                self._tag_index[tag] = set()
            self._tag_index[tag].add(manifest.device_id)
        if manifest.emergency_capable:
            self._emergency_ids.add(manifest.device_id)
        log.info("Registered device: %s (%s)", manifest.device_id, manifest.device_name)
        for cb in self._listeners:
            cb(manifest)

    def unregister(self, device_id: str) -> None:
        manifest = self._devices.pop(device_id, None)
        if manifest:
            for tag in manifest.tags:
                self._tag_index.get(tag, set()).discard(device_id)
            self._emergency_ids.discard(device_id)
        log.info("Unregistered device: %s", device_id)

    def get(self, device_id: str) -> Optional[CapabilityManifest]:
        return self._devices.get(device_id)

    def all(self) -> list[CapabilityManifest]:
        """Every registered device, including quarantined ones.

        This is INVENTORY: what the hub knows about. Status pages, exports and
        audits want this — a device the operator can no longer act on is still a
        thing they need to see, and hiding it is how it gets forgotten.
        """
        return list(self._devices.values())

    def active(self) -> list[CapabilityManifest]:
        """Devices eligible to PARTICIPATE in an intent.

        Separate from `all()` because the two questions are different and were
        being answered by one method. A device whose declarative file was
        deleted is still in the inventory — the operator must see it to decide —
        but it must not be planned into an emergency, because the operator
        already believes it is gone.

        Quarantine is deliberately not deletion: a directory that failed to
        mount looks exactly like a directory whose files were removed, and a hub
        that reacts to the first by deregistering a building is worse than one
        that asks.
        """
        return [m for m in self._devices.values() if not is_quarantined(m)]

    def find_by_tags(self, tags: list[str]) -> list[CapabilityManifest]:
        """Return devices that have at least one of the given tags.
        O(|tags| + |candidates|) with the inverted index, not O(n).
        """
        candidate_ids: set[str] = set()
        for tag in tags:
            candidate_ids |= self._tag_index.get(tag, set())
        return [self._devices[did] for did in candidate_ids if did in self._devices]

    def find_by_required_tags(self, required_tags: set[str]) -> list[CapabilityManifest]:
        """Return devices that have ALL of the required tags (intersection index).
        O(|result|) — starts with the smallest tag set and intersects progressively.
        Significantly faster than union-based lookup when tags are specific.
        """
        if not required_tags:
            return self.all()
        # Sort by set size ascending — smallest set first minimizes intersection cost
        sets = sorted(
            [self._tag_index.get(t, set()) for t in required_tags],
            key=len,
        )
        result_ids = sets[0].copy()
        for s in sets[1:]:
            result_ids &= s
            if not result_ids:
                break
        return [self._devices[did] for did in result_ids if did in self._devices]

    def find_emergency_capable(self) -> list[CapabilityManifest]:
        """Return emergency-capable devices. O(|emergency_devices|) with index."""
        return [self._devices[did] for did in self._emergency_ids if did in self._devices]

    def find_by_actuator(self, actuator_type: str) -> list[CapabilityManifest]:
        return [
            d for d in self._devices.values()
            if any(a.type == actuator_type for a in d.actuators)
        ]

    def on_register(self, cb: Callable) -> None:
        self._listeners.append(cb)
