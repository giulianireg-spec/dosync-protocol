"""
DoSync MQTT Adapter
===================
Layer 1 transport: MQTT broker as the communication channel.

Enables devices communicating via MQTT to participate in DoSync intents
without HTTP, without REST, and without internet — just a local MQTT broker.
Ideal for microcontrollers (ESP32, Arduino), Tasmota devices, Zigbee2MQTT,
and any sensor network that speaks MQTT natively.

Configuration (environment variables):
  DOSYNC_MQTT_BROKER    localhost         Broker hostname or IP
  DOSYNC_MQTT_PORT      1883             Broker port (8883 for TLS)
  DOSYNC_MQTT_USER                       Optional username
  DOSYNC_MQTT_PASSWORD                   Optional password
  DOSYNC_MQTT_PREFIX    dosync           Topic prefix
  DOSYNC_MQTT_QOS       1                QoS level (0, 1, or 2)

Topic structure:
  {prefix}/devices/{device_id}/commands   Hub → device (action)
  {prefix}/devices/{device_id}/events     Device → hub (sensor event)
  {prefix}/devices/{device_id}/status     Device → hub (state, retained)
  {prefix}/devices/{device_id}/register   Device → hub (self-registration)
  {prefix}/hub/status                     Hub heartbeat + LWT

Command payload (hub → device):
  {"action": "turn_on", "params": {...}, "urgency": "info", "timestamp": 0.0}

Event payload (device → hub):
  {"event_id": "motion_detected", "severity": "alert", "data": {...}}

Registration payload (retained, device → hub):
  Full CapabilityManifest JSON — see spec/schemas/capability-manifest.schema.json

Install broker on Pi:
  sudo apt-get install -y mosquitto mosquitto-clients
  sudo systemctl enable mosquitto && sudo systemctl start mosquitto

Install Python library:
  pip install paho-mqtt
"""

import asyncio
import json
import logging
import os
import time

from . import DoSyncAdapter
from ..models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters.mqtt")

# ── Configuration ─────────────────────────────────────────────────────────────

BROKER   = os.environ.get("DOSYNC_MQTT_BROKER",   "localhost")
PORT     = int(os.environ.get("DOSYNC_MQTT_PORT",  "1883"))
USER     = os.environ.get("DOSYNC_MQTT_USER",      "")
PASSWORD = os.environ.get("DOSYNC_MQTT_PASSWORD",  "")
PREFIX   = os.environ.get("DOSYNC_MQTT_PREFIX",    "dosync")
QOS      = int(os.environ.get("DOSYNC_MQTT_QOS",   "1"))
# Shared secret for device registration authorization.
# If set, devices MUST include {"dosync_secret": "<value>"} in their register payload.
# Devices that do not present the correct secret are silently rejected.
# Set this to a random string and configure all devices with the same value.
MQTT_SECRET = os.environ.get("DOSYNC_MQTT_SECRET", "")

# ── Optional import ───────────────────────────────────────────────────────────

try:
    import paho.mqtt.client as _mqtt
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False
    log.warning(
        "paho-mqtt not installed — MQTTAdapter disabled. "
        "Install with: pip install paho-mqtt"
    )


# ── Adapter ───────────────────────────────────────────────────────────────────

class MQTTAdapter(DoSyncAdapter):
    """
    DoSync adapter for MQTT devices.

    Publishes DeviceActions as MQTT command messages and subscribes to
    device topics to receive events and self-registrations.

    Integrates paho-mqtt (threaded) with DoSync's asyncio event loop
    via asyncio.run_coroutine_threadsafe.
    """

    def __init__(self, hub=None):
        self._hub       = hub
        self._client    = None
        self._connected = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def adapter_name(self) -> str:
        return "mqtt"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self, config: dict | None = None) -> None:
        """Connect to the MQTT broker and start background loop."""
        if not _PAHO_AVAILABLE:
            log.warning("MQTTAdapter: paho-mqtt not installed — skipping connect")
            return

        self._loop = asyncio.get_running_loop()

        client_id = f"dosync-hub-{int(time.time()) % 10000}"
        self._client = _mqtt.Client(
            _mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )

        if USER:
            self._client.username_pw_set(USER, PASSWORD)

        # Last Will Testament — signals hub went offline unexpectedly
        self._client.will_set(
            f"{PREFIX}/hub/status",
            json.dumps({"status": "offline", "timestamp": time.time()}),
            qos=QOS,
            retain=True,
        )

        # Exponential backoff on reconnect: 1s → 120s with jitter (paho built-in)
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)

        self._client.on_connect    = self._on_connect
        self._client.on_message    = self._on_message
        self._client.on_disconnect = self._on_disconnect

        try:
            self._client.connect_async(BROKER, PORT, keepalive=60)
            self._client.loop_start()
            log.info("MQTTAdapter: connecting to %s:%d (prefix=%s)", BROKER, PORT, PREFIX)
        except Exception as exc:
            log.warning("MQTTAdapter: connect failed (%s) — MQTT transport disabled", exc)

    async def disconnect(self) -> None:
        """Disconnect from broker and stop the paho loop."""
        if self._client:
            if self._connected:
                self._client.publish(
                    f"{PREFIX}/hub/status",
                    json.dumps({"status": "offline", "timestamp": time.time()}),
                    qos=QOS,
                    retain=True,
                )
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
            log.info("MQTTAdapter: disconnected")

    # ── paho callbacks (run in paho thread) ───────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected = True
            # Subscribe to all device sub-topics
            for suffix in ("events", "register", "status"):
                client.subscribe(f"{PREFIX}/devices/+/{suffix}", qos=QOS)
            # Announce hub online
            client.publish(
                f"{PREFIX}/hub/status",
                json.dumps({"status": "online", "timestamp": time.time()}),
                qos=QOS,
                retain=True,
            )
            log.info(
                "MQTTAdapter: connected to %s:%d — subscribed to %s/devices/+/{events,register,status}",
                BROKER, PORT, PREFIX,
            )
        else:
            log.warning("MQTTAdapter: connection refused — reason_code=%s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self._connected = False
        log.warning("MQTTAdapter: disconnected (reason_code=%s)", reason_code)

    def _on_message(self, client, userdata, msg):
        """Bridge paho thread → asyncio event loop."""
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(
            self._dispatch(msg.topic, msg.payload),
            self._loop,
        )

    # ── Message dispatch (asyncio thread) ────────────────────────────────────

    async def _dispatch(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("MQTTAdapter: invalid JSON on topic %s", topic)
            return

        # topic = {prefix}/devices/{device_id}/{msg_type}
        parts = topic.split("/")
        if len(parts) < 4:
            return
        device_id = parts[-2]
        msg_type  = parts[-1]

        if   msg_type == "register": await self._handle_registration(device_id, data)
        elif msg_type == "events":   await self._handle_event(device_id, data)
        elif msg_type == "status":   self._handle_status(device_id, data)

    async def _handle_registration(self, device_id: str, data: dict) -> None:
        """Auto-register a device that published its manifest to the register topic.

        Handles both payload formats:
          - Flat:   sensors/actuators/events as top-level lists
          - Nested: capabilities: {sensors, actuators, events, context_signals}
        Converts all dict items to their proper dataclass types before constructing
        the CapabilityManifest.
        """
        # Validate shared secret before accepting any registration
        if MQTT_SECRET:
            presented = data.get("dosync_secret", "")
            if presented != MQTT_SECRET:
                log.warning(
                    "MQTTAdapter: rejected registration from '%s' — invalid or missing secret. "
                    "Set DOSYNC_MQTT_SECRET on both hub and device.", device_id
                )
                return

        if self._hub is None:
            log.warning("MQTTAdapter: received registration from %s but hub is not set", device_id)
            return
        try:
            import dataclasses
            from ..models import (
                CapabilityManifest, SensorSpec, ActuatorSpec, EventSpec,
                DeviceCategory, CertTier, Urgency, Severity,
            )

            d = dict(data)
            d["adapter"]   = "mqtt"
            d["device_id"] = device_id
            d.setdefault("dosync_version", "0.1")

            # Flatten nested capabilities → top-level lists (public API format)
            caps = d.pop("capabilities", {})
            d.setdefault("sensors",          caps.get("sensors",          []))
            d.setdefault("actuators",        caps.get("actuators",        []))
            d.setdefault("events",           caps.get("events",           []))
            d.setdefault("context_signals",  caps.get("context_signals",  []))

            # Convert list items from dicts to typed dataclasses
            d["sensors"]  = [SensorSpec(**s)  if isinstance(s, dict) else s for s in d["sensors"]]
            d["actuators"] = [ActuatorSpec(**a) if isinstance(a, dict) else a for a in d["actuators"]]
            d["events"]   = [
                EventSpec(
                    id=e["id"],
                    severity=Severity(e.get("severity", "info")),
                    description=e.get("description", ""),
                ) if isinstance(e, dict) else e
                for e in d["events"]
            ]

            # Handle enum fields
            if isinstance(d.get("category"), str):
                try:    d["category"] = DeviceCategory(d["category"])
                except ValueError: d["category"] = DeviceCategory.HYBRID
            if isinstance(d.get("cert_tier"), str):
                try:    d["cert_tier"] = CertTier(d["cert_tier"])
                except ValueError: d.pop("cert_tier", None)

            # Remove any fields unknown to CapabilityManifest
            valid = {f.name for f in dataclasses.fields(CapabilityManifest)}
            d = {k: v for k, v in d.items() if k in valid}

            manifest = CapabilityManifest(**d)
            self._hub.register_device(manifest)
            log.info("MQTTAdapter: auto-registered device '%s'", device_id)

            # Acknowledge registration
            if self._client and self._connected:
                self._client.publish(
                    f"{PREFIX}/devices/{device_id}/ack",
                    json.dumps({"registered": True, "hub_timestamp": time.time()}),
                    qos=QOS,
                )
        except Exception as exc:
            log.warning("MQTTAdapter: failed to register device '%s': %s", device_id, exc)

    async def _handle_event(self, device_id: str, data: dict) -> None:
        """Forward a device event to the hub so it reaches the audit log AND the
        event stream (WebSocket broadcast) that autonomous agents subscribe to.

        Previously this called a non-existent `process_event` guarded by hasattr,
        so MQTT device events were silently dropped and never reached an agent."""
        if self._hub is None:
            return
        try:
            from ..models import DeviceEvent, Severity
            try:
                severity = Severity(data.get("severity", "info"))
            except ValueError:
                severity = Severity("info")
            event = DeviceEvent(
                device_id=device_id,
                event_id=data.get("event_id", "mqtt_event"),
                severity=severity,
                data=data.get("data", {}),
            )
            await self._hub.receive_event(event)
            log.debug("MQTTAdapter: event '%s' from '%s'", event.event_id, device_id)
        except Exception as exc:
            log.warning("MQTTAdapter: failed to process event from '%s': %s", device_id, exc)

    def _handle_status(self, device_id: str, data: dict) -> None:
        """Update in-memory state cache for StateAwareResolver."""
        log.debug("MQTTAdapter: status update from '%s': %s", device_id, data)

    # ── Execute (called by AdapterExecutor) ───────────────────────────────────

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Publish a command to the device's MQTT command topic."""
        if not _PAHO_AVAILABLE:
            return ActionResult(
                device_id=action.device_id, action=action.action,
                success=False, error="paho-mqtt not installed",
            )
        if not self._connected:
            return ActionResult(
                device_id=action.device_id, action=action.action,
                success=False, error="MQTT broker not connected",
            )

        topic   = f"{PREFIX}/devices/{action.device_id}/commands"
        payload = json.dumps({
            "action":    action.action,
            "params":    action.params,
            "urgency":   urgency.value,
            "timestamp": time.time(),
        })

        try:
            info = self._client.publish(topic, payload, qos=QOS)
            # Non-blocking for QoS 0; brief wait for QoS 1/2
            if QOS > 0:
                info.wait_for_publish(timeout=2.0)
            log.debug("MQTTAdapter: published '%s' → %s", action.action, topic)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=True,
                response={"topic": topic, "qos": QOS, "mid": info.mid},
            )
        except Exception as exc:
            log.warning("MQTTAdapter: publish failed for '%s': %s", action.device_id, exc)
            return ActionResult(
                device_id=action.device_id, action=action.action,
                success=False, error=str(exc),
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def broker(self) -> str:
        return f"{BROKER}:{PORT}"

    def publish_raw(self, topic: str, payload: dict, retain: bool = False) -> None:
        """Publish an arbitrary message — for hub-initiated announcements."""
        if self._client and self._connected:
            self._client.publish(topic, json.dumps(payload), qos=QOS, retain=retain)

    def clear_device_registration(self, device_id: str) -> None:
        """Clear the retained MQTT registration message for a device.

        Publishes an empty payload (byte string b"") to the device's register
        topic with retain=True. This is the correct MQTT mechanism for deleting
        a retained message — the broker removes it and future subscribers will
        not receive a stale registration.

        Called automatically when DELETE /v1/devices/{device_id} is invoked.
        """
        if not (self._client and self._connected):
            log.debug("MQTTAdapter: cannot clear registration for '%s' — not connected", device_id)
            return
        topic = f"{PREFIX}/devices/{device_id}/register"
        self._client.publish(topic, payload=b"", qos=QOS, retain=True)
        log.info("MQTTAdapter: cleared retained registration for device '%s'", device_id)

    def purge_stale_device_topics(self, active_device_ids: list[str]) -> None:
        """Clear retained registrations for devices no longer in the hub registry.
        Call on hub startup after registry is restored from DB.
        Not called automatically — invoke explicitly if needed.
        """
        # Cannot enumerate retained messages from paho — no-op without external tooling
        # This is a known limitation of MQTT: retained message cleanup requires
        # knowing which topics exist. Subscribe and filter is the only approach.
        log.debug(
            "MQTTAdapter: purge_stale_device_topics called (%d active devices) "
            "— manual cleanup only; use 'mosquitto_sub -t dosync/# -v' to audit.",
            len(active_device_ids),
        )
