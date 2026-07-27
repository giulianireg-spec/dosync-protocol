"""
DoSync — Universal BLE Adapter
==============================
A single adapter that controls ANY Bluetooth Low Energy device, by driving the
generic GATT primitives (connect, write characteristic) and reading the per-device
action→characteristic mapping from the device's manifest.

This is the "dumb body, external mind" principle at the transport layer: the BLE
device does not know DoSync exists. It only exposes its native GATT interface. The
adapter — running in the hub — speaks that native BLE and lends the device the
intelligence of being coordinated by an intent.

Why one adapter for all BLE devices:
    BLE has no universal command. Each device exposes services and characteristics
    identified by UUIDs, and which characteristic means "turn on" differs per
    device. So the *code* is generic (GATT writes); the *mapping* lives in each
    device's manifest under adapter_config. Adding a new BLE device requires a
    manifest entry, not new code.

Manifest adapter_config schema (per device):
    {
        "address": "AA:BB:CC:DD:EE:FF",      # BLE MAC (or platform UUID on macOS)
        "actions": {
            "turn_on":  {"char": "0000fff1-0000-1000-8000-00805f9b34fb",
                          "write": "0F0D0300"},        # hex bytes to write
            "turn_off": {"char": "0000fff1-0000-1000-8000-00805f9b34fb",
                          "write": "0F0D0400"}
        }
    }

The device's CapabilityManifest declares adapter="ble" and carries this config.
The hub routes any action on that device here, exactly like wiz/gpio/homeassistant.

Dependencies: bleak (cross-platform BLE; uses BlueZ on the Raspberry Pi).
"""

from __future__ import annotations
import logging
from typing import Optional

from ..models import ActionResult, DeviceAction, Urgency
from . import DoSyncAdapter

log = logging.getLogger("dosync.adapters.ble")

# bleak is imported lazily so the module imports (and the adapter registers /
# unit-tests) on a host without a Bluetooth stack.
try:
    from bleak import BleakClient
    _BLEAK_AVAILABLE = True
except Exception:  # pragma: no cover - depends on host
    BleakClient = None  # type: ignore
    _BLEAK_AVAILABLE = False


def ble_manifest(
    device_id: str,
    device_name: str,
    address: str,
    actions: dict,
    tags: Optional[list] = None,
    emergency_capable: bool = False,
):
    """Helper to build a CapabilityManifest for a generic BLE device.

    `actions` maps a DoSync action name to {"char": <uuid>, "write": <hex>}.
    The actuator list is derived from the action keys, so the resolver sees
    exactly the actions this device supports.
    """
    from ..models import (
        ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
    )

    # Derive one actuator per supported action (id == type, like wiz_manifest).
    actuators = [
        ActuatorSpec(name, name, f"BLE action {name}")
        for name in actions.keys()
    ]

    manifest = CapabilityManifest(
        device_id=device_id,
        device_name=device_name,
        manufacturer="Generic BLE",
        model="BLE GATT device",
        firmware="auto",
        category=DeviceCategory.ACTUATOR,
        tags=list(set(tags or [])),
        sensors=[],
        actuators=actuators,
        events=[],
        emergency_capable=emergency_capable,
        cert_tier=CertTier.BASIC,
    )

    # Attach adapter config (address + action→characteristic map) to the manifest.
    manifest.adapter = "ble"
    manifest.adapter_config = {"address": address, "actions": actions}

    return manifest


class BLEAdapter(DoSyncAdapter):
    """Universal Bluetooth Low Energy adapter.

    One instance handles every device whose manifest declares adapter="ble".
    The per-device address and action→characteristic map come from the manifest's
    adapter_config, read from the hub registry (same pattern as WiZAdapter).
    """

    def __init__(self, hub=None, connect_timeout: float = 10.0):
        """
        Args:
            hub: reference to the DoSyncHub to read adapter_config from the
                 manifest. Optional — if absent, config must come in action.params.
            connect_timeout: seconds to wait for a BLE connection.
        """
        self._hub = hub
        self._connect_timeout = connect_timeout

    async def discover(self, timeout: float = 5.0) -> list:
        """Scan for BLE advertisements.

        Proof that discovery is not an IP-only idea. Bluetooth devices announce
        themselves on a radio channel, not a network — nothing about DoSync's
        model required a broadcast address, only the previous implementation
        did, and it lived in a central module that knew about WiZ specifically.

        What comes back is a CANDIDATE, deliberately incomplete: an
        advertisement carries a name and an address, not what the device can do.
        BLE has no equivalent of "this is a dimmable lamp" — GATT characteristics
        say how to write bytes, not what the bytes mean. So a BLE candidate is
        offered for adoption with no actions, and the operator supplies them.
        Presenting a guess as a capability would be worse than admitting the
        transport cannot tell us.
        """
        from ..discovery import DiscoveredDevice

        try:
            from bleak import BleakScanner
        except ImportError:
            log.debug("BLE discovery unavailable: bleak is not installed")
            return []

        try:
            found = await BleakScanner.discover(timeout=timeout)
        except Exception as e:
            # A missing or disabled adapter is ordinary on a machine with no
            # Bluetooth; it is not an error worth failing a whole scan over.
            log.info("BLE scan did not run: %s", e)
            return []

        candidates = []
        for d in found:
            if not getattr(d, "name", None):
                continue          # unnamed beacons are noise to a human picking
            candidates.append(DiscoveredDevice(
                adapter="ble",
                device_id=f"ble-{d.address.replace(':', '').lower()}",
                device_name=d.name,
                ip=d.address,     # the address field carries the MAC here
                extra={"rssi": getattr(d, "rssi", None), "transport": "bluetooth-le"},
            ))
        log.info("BLE scan found %d named device(s)", len(candidates))
        return candidates

    @property
    def adapter_name(self) -> str:
        return "ble"

    def _get_config(self, action: DeviceAction) -> dict:
        """Resolve adapter_config: action.params override, then manifest."""
        cfg = action.params.get("adapter_config")
        if cfg:
            return cfg
        if self._hub:
            device = self._hub.registry.get(action.device_id)
            if device and device.adapter_config:
                return device.adapter_config
        return {}

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Translate a DoSync action into a GATT characteristic write."""
        cfg = self._get_config(action)
        address = cfg.get("address")
        actions_map = cfg.get("actions", {})

        if not address:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error="BLE manifest missing 'address'. Use ble_manifest(address=...).",
            )

        spec = actions_map.get(action.action)
        if spec is None:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"BLE device has no mapping for action '{action.action}'.",
            )

        char_uuid = spec.get("char")
        write_hex = spec.get("write")
        if not char_uuid or write_hex is None:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"BLE action '{action.action}' mapping needs 'char' and 'write'.",
            )

        try:
            payload = bytes.fromhex(write_hex)
        except ValueError:
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"BLE 'write' value is not valid hex: {write_hex!r}.",
            )

        if not _BLEAK_AVAILABLE:
            # Simulated mode — bleak not installed on this host.
            log.info("[SIMULATED] BLE %s @ %s: write %s to %s",
                     action.device_id, address, write_hex, char_uuid)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=True,
                response={"status": "simulated", "address": address,
                          "char": char_uuid, "wrote": write_hex},
            )

        try:
            async with BleakClient(address, timeout=self._connect_timeout) as client:
                await client.write_gatt_char(char_uuid, payload, response=True)
            log.info("BLE %s @ %s: wrote %s to %s → OK",
                     action.device_id, address, write_hex, char_uuid)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=True,
                response={"address": address, "char": char_uuid, "wrote": write_hex},
            )
        except Exception as e:
            log.warning("BLE %s @ %s failed: %s", action.action, address, e)
            return ActionResult(
                device_id=action.device_id, action=action.action, success=False,
                error=f"BLE write failed: {e}",
            )

    async def get_state(self, device_id: str) -> Optional[dict]:
        """BLE state query is optional and device-specific. Deferred, like other
        adapters that return None by default."""
        return None
