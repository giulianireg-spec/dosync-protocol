"""
DoSync — Escenario de humo y monoxido de carbono

Demuestra la ejecucion en FASES: a diferencia de otros escenarios
donde todo ocurre en paralelo, una emergencia de incendio requiere
un orden especifico de acciones.

Fase 1 — ALERTA inmediata (0ms):
  → Alarma activa patron de incendio
  → Llamada a bomberos
  → Notificacion a familia con instrucciones de evacuacion

Fase 2 — EVACUACION (2 segundos despues):
  → Todas las luces al maximo (orientacion visual)
  → Apertura de puertas de escape

Fase 3 — ACCESO para bomberos (3 segundos despues):
  → Desbloquear entrance principal
  → Camara exterior activa para orientar a bomberos
"""

import asyncio
import json
import logging

from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, DeviceCategory,
    DeviceEvent, EventSpec, Phase, PhaseAction, PhasedActionPlan,
    SensorSpec, Urgency,
)
from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)


def build_smoke_devices() -> list[CapabilityManifest]:

    smoke_detector = CapabilityManifest(
        device_id="smoke-living-01",
        device_name="Detector de humo sala",
        manufacturer="Kidde",
        model="i9050",
        firmware="2.0",
        category=DeviceCategory.HYBRID,
        tags=["sensor", "emergency", "smoke", "safety"],
        sensors=[
            SensorSpec("smoke_level", "float",   "Nivel de humo (ppm)", unit="ppm"),
            SensorSpec("co_level",    "float",   "Nivel de CO (ppm)",   unit="ppm"),
            SensorSpec("temp",        "float",   "Temperatura ambiente", unit="celsius"),
        ],
        events=[
            EventSpec("smoke_detected", Urgency.EMERGENCY, "Humo detectado sobre umbral"),
            EventSpec("co_detected",    Urgency.EMERGENCY, "Monoxido de carbono sobre umbral"),
            EventSpec("all_clear",      Urgency.INFO,      "Niveles volvieron a normal"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    alarm = CapabilityManifest(
        device_id="alarm-main-01",
        device_name="Alarma principal",
        manufacturer="AlertCo",
        model="Siren X",
        firmware="1.0",
        category=DeviceCategory.ACTUATOR,
        tags=["alarm", "emergency", "audio"],
        actuators=[
            ActuatorSpec("alarm", "alarm", "Activa sirena con patron"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    phone = CapabilityManifest(
        device_id="phone-family-01",
        device_name="Telefonos familiares",
        manufacturer="DoSync",
        model="NotifyBridge",
        firmware="0.1.0",
        category=DeviceCategory.COMMUNICATION,
        tags=["communication", "phone", "emergency"],
        actuators=[
            ActuatorSpec("call",   "call",   "Llamar a un numero"),
            ActuatorSpec("notify", "notify", "Notificacion push/SMS"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    lights = CapabilityManifest(
        device_id="lights-main-01",
        device_name="Luces principales",
        manufacturer="Philips",
        model="Hue Bridge",
        firmware="1.50",
        category=DeviceCategory.HYBRID,
        tags=["light", "emergency"],
        actuators=[
            ActuatorSpec("set_brightness", "set_brightness", "Ajusta brillo"),
            ActuatorSpec("turn_off",       "turn_off",       "Apaga luces"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    front_door = CapabilityManifest(
        device_id="lock-frontdoor-01",
        device_name="Cerradura puerta principal",
        manufacturer="SecureLock",
        model="SmartBolt Pro",
        firmware="3.0.1",
        category=DeviceCategory.HYBRID,
        tags=["door-lock", "entrance", "emergency", "access"],
        actuators=[
            ActuatorSpec("unlock", "unlock", "Abre la puerta"),
            ActuatorSpec("lock",   "lock",   "Cierra la puerta"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    escape_door = CapabilityManifest(
        device_id="lock-backdoor-01",
        device_name="Puerta trasera / escape",
        manufacturer="SecureLock",
        model="SmartBolt Pro",
        firmware="3.0.1",
        category=DeviceCategory.HYBRID,
        tags=["door-lock", "escape", "emergency", "access"],
        actuators=[
            ActuatorSpec("unlock", "unlock", "Abre puerta de escape"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    camera_exterior = CapabilityManifest(
        device_id="camera-exterior-01",
        device_name="Camara exterior",
        manufacturer="SafeHome",
        model="Cam-4K",
        firmware="1.2.0",
        category=DeviceCategory.HYBRID,
        tags=["camera", "exterior", "emergency"],
        actuators=[
            ActuatorSpec("stream", "stream", "Activa streaming"),
            ActuatorSpec("record", "record", "Inicia grabacion"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    return [smoke_detector, alarm, phone, lights,
            front_door, escape_door, camera_exterior]


async def scenario_smoke_emergency(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO: Humo detectado — evacuacion de emergencia")
    print("="*60)

    # El detector emite el evento
    event = DeviceEvent(
        device_id="smoke-living-01",
        event_id="smoke_detected",
        severity=Urgency.EMERGENCY,
        data={
            "smoke_ppm":  450.0,    # umbral de alarma: 300ppm
            "co_ppm":     0.0,
            "temp_c":     38.5,
            "location":   "sala de estar",
            "confidence": 0.98,
        },
    )
    await hub.receive_event(event)

    # Construimos el plan de 3 fases
    plan = PhasedActionPlan(
        intent_id="smoke-emergency-001",
        urgency=Urgency.EMERGENCY,
        phases=[

            Phase(
                name="ALERTA — notificacion inmediata",
                delay_after_ms=2000,
                actions=[
                    PhaseAction("alarm-main-01",    "alarm",  {"pattern": "fire"}),
                    PhaseAction("phone-family-01",  "call",   {
                        "number":  "100",
                        "message": "Incendio detectado. Sala de estar.",
                    }),
                    PhaseAction("phone-family-01",  "notify", {
                        "message": "EMERGENCIA: Humo detectado en sala (450ppm). "
                                   "Evacua el hogar. Bomberos alertados.",
                        "urgency": "emergency",
                    }),
                ],
            ),

            Phase(
                name="EVACUACION — orientacion visual",
                delay_after_ms=3000,
                actions=[
                    PhaseAction("lights-main-01",   "set_brightness", {"brightness": 100}),
                    PhaseAction("lock-backdoor-01", "unlock",         {"duration_seconds": 600}),
                ],
            ),

            Phase(
                name="ACCESO — entrance para bomberos",
                delay_after_ms=0,
                actions=[
                    PhaseAction("lock-frontdoor-01",  "unlock", {"duration_seconds": 600}),
                    PhaseAction("camera-exterior-01", "record", {"reason": "fire_emergency"}),
                    PhaseAction("camera-exterior-01", "stream", {"destination": "bomberos"}),
                ],
            ),
        ],
    )

    print(f"\n  Plan de {len(plan.phases)} fases:")
    for i, phase in enumerate(plan.phases):
        delay = f" (espera {phase.delay_after_ms}ms antes de la siguiente)" \
                if phase.delay_after_ms else ""
        print(f"  Fase {i+1}: {phase.name} — {len(phase.actions)} acciones{delay}")

    print()
    results = await hub.execute_phased(plan, executor)

    for i, (phase, result) in enumerate(zip(plan.phases, results)):
        icon = "✓" if result.success else "✗"
        print(f"\n  {icon} Fase {i+1} — {phase.name}")
        for r in result.results:
            sub = "✓" if r.success else "✗"
            print(f"    {sub} [{r.device_id}] {r.action} → {json.dumps(r.response)}")

    total_ok  = sum(1 for r in results if r.success)
    print(f"\n  Resultado: {total_ok}/{len(results)} fases exitosas")


async def main():
    hub      = DoSyncHub()
    executor = SimulatedExecutor(failure_rate=0.0)

    def on_event(event: DeviceEvent):
        print(f"\n  [HUB EVENT] {event.device_id} → "
              f"{event.event_id} [{event.severity.value}]")
    hub.on_event(on_event)

    print("\n── Registrando dispositivos ──────────────────────────")
    for device in build_smoke_devices():
        hub.register_device(device)
    print(f"  {len(hub.registry.all())} dispositivos registrados.")

    await scenario_smoke_emergency(hub, executor)

    print("\n── Audit log ─────────────────────────────────────────")
    for entry in hub.audit_log.entries():
        kind = entry.get("type", "?")
        h    = entry.get("hash", "")[:10]
        extra = ""
        if kind == "phase_executed":
            extra = f" | fase '{entry.get('phase')}' [{entry.get('phase_num')}]"
        elif kind == "device_event":
            extra = f" | {entry.get('device_id')} → {entry.get('event_id')}"
        print(f"  [{h}] {kind}{extra}")

    print(f"\n  Integridad: {'VALIDA' if hub.audit_log.verify() else 'COMPROMETIDA'}\n")


if __name__ == "__main__":
    asyncio.run(main())
