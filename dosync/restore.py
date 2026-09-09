"""Rebuilding a hub from what the database holds.

Extracted from `hub.py` on 8 September 2026. Restoration reads five things —
devices, audit chain, occupancy, family profile and device state — and calls
nothing back: 140 lines that only ever wrote onto the hub it was handed.

`register_device` was the other candidate the inventory named for this phase.
Reading it first showed why it is not one: it fires an `alert_anomaly` intent
when a device's capabilities change without a firmware bump, which makes it a
security path rather than registration logic. Its own comments record that this
path was dead for months — it called `execute_intent` with no executor, raised
TypeError on every anomaly, and a bare `except` swallowed it whole. Moving that
into a registry would bury a security decision inside a data structure.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("dosync.hub")


class HubRestorer:
    """Puts a hub back the way the database says it was.

    Holds a reference rather than being a method on the hub: restoration runs
    exactly once, at startup, and the hub does not need to carry the code for
    the rest of its life.
    """

    def __init__(self, hub):
        self._hub = hub


    def restore(self) -> None:
        """
        Restore state from SQLite when the hub starts.
        Los dispositivos, perfil y audit log sobreviven reinicios.
        """
        from .models import (
            ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
            ContextSignalType, DeviceCategory, EventSpec, SensorSpec, Severity,
        )

        # Restore devices
        for manifest_dict in self._hub.db.load_devices():
            try:
                # Rebuild CapabilityManifest from persisted dict
                caps = manifest_dict.get("capabilities", {})

                sensors = [
                    SensorSpec(
                        id=s["id"], type=s["type"],
                        description=s.get("description", ""),
                        unit=s.get("unit"),
                        poll_interval_ms=s.get("poll_interval_ms", 30000),
                        kind=s.get("kind", "environment"),   # legacy manifests default
                    )
                    for s in caps.get("sensors", [])
                ]
                actuators = [
                    ActuatorSpec(
                        id=a["id"], type=a["type"],
                        description=a.get("description", ""),
                    )
                    for a in caps.get("actuators", [])
                ]
                events = [
                    EventSpec(
                        id=e["id"],
                        severity=Severity(e.get("severity", "info")),
                        description=e.get("description", ""),
                    )
                    for e in caps.get("events", [])
                ]
                context_signals = [
                    ContextSignal(
                        type=ContextSignalType(c["type"]),
                        description=c.get("description", ""),
                        confidence_weight=c.get("confidence_weight", 1.0),
                    )
                    for c in caps.get("context_signals", [])
                ]

                manifest = CapabilityManifest(
                    device_id=manifest_dict["device_id"],
                    device_name=manifest_dict["device_name"],
                    manufacturer=manifest_dict["manufacturer"],
                    model=manifest_dict["model"],
                    firmware=manifest_dict["firmware"],
                    category=DeviceCategory(manifest_dict["category"]),
                    tags=manifest_dict["tags"],
                    sensors=sensors,
                    actuators=actuators,
                    events=events,
                    context_signals=context_signals,
                    emergency_capable=manifest_dict.get("emergency_capable", False),
                    cert_tier=CertTier(manifest_dict["cert_tier"]) if manifest_dict.get("cert_tier") else None,
                )
                # Restore adapter fields — critical for physical device control.
                #
                # `adapter_config` is restored unconditionally, not only
                # alongside an adapter. This is the mirror of a defect fixed in
                # `to_dict` on 29 August, which dropped the same field for the
                # same reason: adapter_config carries more than adapter settings
                # — quarantine lives there, and since 5 September so does
                # absence. A device with no adapter lost both on every restart,
                # silently, and the next import cycle would re-mark it with a
                # fresh date. "Absent since Thursday" would have read as "absent
                # for ten minutes", forever.
                if manifest_dict.get("adapter"):
                    manifest.adapter = manifest_dict["adapter"]
                manifest.adapter_config = manifest_dict.get("adapter_config", {})
                self._hub.registry.register(manifest)
            except Exception as e:
                log.warning("Could not restore device %s: %s",
                            manifest_dict.get("device_id", "?"), e)

        # Restore audit log. If older entries were archived to a segment file,
        # the chain starts at the stored anchor, not at genesis — both the
        # append continuity (_prev_hash) and verification (anchor_prev_hash)
        # must honor it.
        _anchor = self._hub.db.get_audit_anchor()
        if _anchor:
            self._hub.audit_log.anchor_prev_hash = _anchor.get("anchor_prev_hash", "0" * 64)
            self._hub.audit_log._prev_hash = self._hub.audit_log.anchor_prev_hash
        for entry in self._hub.db.load_audit_log():
            self._hub.audit_log._entries.append(entry)
            self._hub.audit_log._prev_hash = entry.get("hash", "0" * 64)
        # Continue the sequence rather than restart it: a restart must not make
        # two entries share a number, nor hand out a number BELOW one already
        # used. Take the highest `seq` present, not the row count — after
        # archiving, the surviving entries keep their original high numbers
        # while the row count is small, and counting rows would wind the series
        # backwards. Chains written before sequence numbers have none at all, so
        # the row count is the right starting point only in that case.
        _seqs = [e["seq"] for e in self._hub.audit_log._entries if e.get("seq") is not None]
        self._hub.audit_log._next_seq = (max(_seqs) + 1) if _seqs \
            else len(self._hub.audit_log._entries)
        self._hub.audit_log._checkpoint_cb = self._hub.db.set_audit_head

        # Restore family profile. Until 2026-07-14 this was MISSING: the profile
        # was persisted by set_family_profile() and db.load_family_profile()
        # existed, but nothing ever called it — so every restart silently dropped
        # the profile while this method's docstring promised it survives.
        try:
            profile_dict = self._hub.db.load_family_profile()
            if profile_dict:
                from .models import FamilyProfile
                self._hub.family_profile = FamilyProfile.from_dict(profile_dict)
                log.info("Restored family profile: %s", self._hub.family_profile.family_name)
        except Exception as e:
            log.warning("Could not restore family profile: %s", e)

        # Restore presence signals
        from .models import PresenceSignal
        for signal_dict in self._hub.db.load_presence_signals():
            try:
                signal = PresenceSignal(
                    device_id=signal_dict["device_id"],
                    signal_type=ContextSignalType(signal_dict["signal_type"]),
                    present=signal_dict["present"],
                    confidence=signal_dict["confidence"],
                    member_id=signal_dict.get("member_id"),
                    timestamp=signal_dict.get("timestamp", time.time()),
                )
                self._hub.occupancy._signals.append(signal)
            except Exception as e:
                log.warning("Could not restore presence signal: %s", e)

        log.info(
            "Hub restored: %d device(s), %d audit entries",
            len(self._hub.registry.all()),
            len(self._hub.audit_log.entries()),
        )
