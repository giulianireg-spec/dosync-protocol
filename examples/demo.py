"""
DoSync — Ejemplo de uso completo

Escenario 1: Detección de caída de la a monitored person → emergencia
Escenario 2: Avería de heladera → notificación familiar
"""

import asyncio
import json
import logging

from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
    DeviceEvent, EventSpec, Intent, IntentClass, SensorSpec, Urgency,
)
from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)


def build_home_devices() -> list[CapabilityManifest]:
    """Define the gadgets registered in this home."""

    camera_bedroom = CapabilityManifest(
        device_id="camera-bedroom-01",
        device_name="Bedroom camera",
        manufacturer="SafeHome",
        model="Cam-4K",
        firmware="1.2.0",
        category=DeviceCategory.HYBRID,
        tags=["camera", "bedroom", "emergency", "motion"],
        sensors=[
            SensorSpec("motion", "motion",  "Motion detection"),
            SensorSpec("fall",   "boolean", "AI fall detection"),
        ],
        actuators=[
            ActuatorSpec("stream", "stream", "Stream video feed"),
        ],
        events=[
            EventSpec("fall_detected", Urgency.EMERGENCY, "Person fell on floor"),
            EventSpec("motion",        Urgency.INFO,      "Motion detected"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    front_door_lock = CapabilityManifest(
        device_id="lock-frontdoor-01",
        device_name="Front door lock",
        manufacturer="SecureLock",
        model="SmartBolt Pro",
        firmware="3.0.1",
        category=DeviceCategory.HYBRID,
        tags=["door-lock", "entrance", "emergency", "access"],
        sensors=[
            SensorSpec("state", "boolean", "Locked / unlocked state"),
        ],
        actuators=[
            ActuatorSpec("unlock", "unlock", "Unlock the door",
                         params_schema={"duration_seconds": "int"}),
            ActuatorSpec("lock",   "lock",   "Lock the door"),
        ],
        events=[
            EventSpec("forced_open", Urgency.EMERGENCY, "Door forced open"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    family_phone = CapabilityManifest(
        device_id="phone-family-hub-01",
        device_name="Family notification hub",
        manufacturer="DoSync",
        model="NotifyBridge",
        firmware="0.1.0",
        category=DeviceCategory.COMMUNICATION,
        tags=["communication", "phone", "emergency"],
        sensors=[],
        actuators=[
            ActuatorSpec("call",   "call",   "Call a phone number",
                         params_schema={"number": "str", "message": "str"}),
            ActuatorSpec("notify", "notify", "Send push/SMS notification",
                         params_schema={"message": "str", "urgency": "str"}),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    alarm = CapabilityManifest(
        device_id="alarm-main-01",
        device_name="Main alarm siren",
        manufacturer="AlertCo",
        model="Siren X",
        firmware="1.0.0",
        category=DeviceCategory.ACTUATOR,
        tags=["alarm", "emergency", "audio"],
        sensors=[],
        actuators=[
            ActuatorSpec("alarm", "alarm", "Activate siren with pattern"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    smart_fridge = CapabilityManifest(
        device_id="fridge-kitchen-01",
        device_name="Kitchen refrigerator",
        manufacturer="CoolMaster",
        model="CM-3000",
        firmware="2.1.4",
        category=DeviceCategory.HYBRID,
        tags=["kitchen", "appliance", "food-safety", "sensor"],
        sensors=[
            SensorSpec("temp_internal",    "temperature", "Internal temperature",
                       unit="celsius", range=[-30, 10], poll_interval_ms=30_000),
            SensorSpec("compressor_status","boolean",     "Compressor running"),
            SensorSpec("door_open",        "boolean",     "Door open/closed"),
        ],
        actuators=[],
        events=[
            EventSpec("malfunction",       Urgency.WARNING, "Compressor failure or abnormal temp"),
            EventSpec("door_open_extended",Urgency.INFO,    "Door open > 2 minutes"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    return [camera_bedroom, front_door_lock, family_phone, alarm, smart_fridge]


async def scenario_fall_emergency(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO 1: Caída detectada — emergencia")
    print("="*60)

    # 1. Camera sends event to hub
    event = DeviceEvent(
        device_id="camera-bedroom-01",
        event_id="fall_detected",
        severity=Urgency.EMERGENCY,
        data={"confidence": 0.97, "location": "bedroom", "person": "grandmother"},
    )
    await hub.receive_event(event)

    # 2. AI builds intent from event
    intent = Intent(
        intent=IntentClass.ENSURE_SAFETY,
        subject="grandmother",
        urgency=Urgency.EMERGENCY,
        context={
            "trigger":          "fall_detected",
            "location":         "bedroom",
            "emergency_number": "911",
            "message":          "Emergency: person fell at home. Door will be unlocked for responders.",
        },
        constraints={"timeout_ms": 5_000, "require_confirmation": False},
    )

    # 3. Execute
    result = await hub.execute_intent(intent, executor)

    print(f"\n  Intent result: {'✓ SUCCESS' if result.success else '✗ PARTIAL FAILURE'}")
    for r in result.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} [{r.device_id}] {r.action} → {json.dumps(r.response)}")
    if result.failed_devices:
        print(f"\n  Failed devices: {result.failed_devices}")


async def scenario_fridge_malfunction(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO 2: Avería de heladera — notificación familiar")
    print("="*60)

    # 1. Fridge sends malfunction event
    event = DeviceEvent(
        device_id="fridge-kitchen-01",
        event_id="malfunction",
        severity=Urgency.WARNING,
        data={"temp_internal": 18.5, "compressor_status": False, "duration_minutes": 45},
    )
    await hub.receive_event(event)

    # 2. AI builds notify intent
    intent = Intent(
        intent=IntentClass("notify_family"),
        urgency=Urgency.WARNING,
        context={
            "trigger":    "fridge_malfunction",
            "device_id":  "fridge-kitchen-01",
            "message":    (
                "⚠️ Tu heladera dejó de enfriar (18.5°C, hace 45 min). "
                "Considerá mover los alimentos. Revisá el compresor."
            ),
        },
    )

    result = await hub.execute_intent(intent, executor)

    print(f"\n  Intent result: {'✓ SUCCESS' if result.success else '✗ PARTIAL FAILURE'}")
    for r in result.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} [{r.device_id}] {r.action} → {json.dumps(r.response)}")


async def main():
    # ── Setup ────────────────────────────────────────────────────────────────
    hub      = DoSyncHub()
    executor = SimulatedExecutor(failure_rate=0.0)

    # Register event handler on hub (AI listens to device events)
    def on_device_event(event: DeviceEvent):
        print(f"\n  [HUB EVENT] {event.device_id} → {event.event_id} [{event.severity.value}]")
    hub.on_event(on_device_event)

    # Register all home devices
    print("\n── Registering home devices ──────────────────────────────")
    for device in build_home_devices():
        hub.register_device(device)

    print(f"\n  {len(hub.registry.all())} devices registered.")

    # ── Run scenarios ────────────────────────────────────────────────────────
    await scenario_fall_emergency(hub, executor)
    await scenario_fridge_malfunction(hub, executor)

    # ── Audit log ────────────────────────────────────────────────────────────
    print("\n── Audit log (tamper-evident) ────────────────────────────")
    for entry in hub.audit_log.entries():
        ts   = entry.get("timestamp", 0)
        kind = entry.get("type", "?")
        h    = entry.get("hash", "")[:12]
        print(f"  [{h}…] {kind}")

    print(f"\n  Audit log integrity: {'✓ VALID' if hub.audit_log.verify() else '✗ TAMPERED'}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
