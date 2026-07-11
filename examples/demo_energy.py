"""
DoSync — Escenarios de energia y eficiencia

Escenario 1: Nadie en casa → modo ahorro automatico
Escenario 2: Lavarropas termino → recordatorio familiar
Escenario 3: Pico de consumo electrico → alerta e investigacion
"""

import asyncio
import json
import logging

from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
    ContextSignalType, DeviceCategory, DeviceEvent, EventSpec,
    Intent, IntentClass, PresenceSignal, SensorSpec, Urgency,
)
from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)


def build_energy_devices() -> list[CapabilityManifest]:
    """Gadgets del hogar relevantes para los escenarios de energia."""

    # ── Context providers ────────────────────────────────────────────────────

    phone_rodrigo = CapabilityManifest(
        device_id="phone-rodrigo-01",
        device_name="Celular de Rodrigo",
        manufacturer="Apple",
        model="iPhone 15",
        firmware="17.0",
        category=DeviceCategory.CONTEXT,
        tags=["phone", "context", "presence", "communication"],
        sensors=[
            SensorSpec("wifi_connected", "boolean", "Conectado al WiFi del hogar"),
            SensorSpec("gps_home",       "boolean", "GPS dentro del perimetro del hogar"),
        ],
        events=[
            EventSpec("left_home",    Urgency.INFO, "Celular salio del perimetro WiFi"),
            EventSpec("arrived_home", Urgency.INFO, "Celular volvio al perimetro WiFi"),
        ],
        context_signals=[
            ContextSignal(ContextSignalType.PRESENCE, "WiFi del celular", confidence_weight=0.7),
            ContextSignal(ContextSignalType.LOCATION,  "GPS del celular", confidence_weight=0.9),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    phone_family = CapabilityManifest(
        device_id="phone-family-01",
        device_name="Telefonos familiares",
        manufacturer="DoSync",
        model="NotifyBridge",
        firmware="0.1.0",
        category=DeviceCategory.COMMUNICATION,
        tags=["communication", "phone"],
        actuators=[
            ActuatorSpec("notify", "notify", "Notificacion push/SMS"),
            ActuatorSpec("display","display","Mostrar mensaje en pantalla"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    # ── Luces ────────────────────────────────────────────────────────────────

    lights_main = CapabilityManifest(
        device_id="lights-main-01",
        device_name="Luces principales",
        manufacturer="Philips",
        model="Hue Bridge",
        firmware="1.50",
        category=DeviceCategory.HYBRID,
        tags=["light", "climate"],
        sensors=[
            SensorSpec("brightness_level", "integer", "Nivel de brillo actual", unit="%"),
        ],
        actuators=[
            ActuatorSpec("set_brightness", "set_brightness", "Ajusta brillo 0-100%"),
            ActuatorSpec("turn_off",       "turn_off",       "Apaga todas las luces"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    # ── Termostato ───────────────────────────────────────────────────────────

    thermostat = CapabilityManifest(
        device_id="thermostat-main-01",
        device_name="Termostato principal",
        manufacturer="Nest",
        model="Thermostat 4",
        firmware="6.2",
        category=DeviceCategory.HYBRID,
        tags=["thermostat", "climate", "light"],
        sensors=[
            SensorSpec("temp_current", "temperature", "Temperatura actual", unit="celsius"),
            SensorSpec("temp_target",  "temperature", "Temperatura objetivo", unit="celsius"),
        ],
        actuators=[
            ActuatorSpec("set_temperature", "set_temperature", "Ajusta temperatura"),
            ActuatorSpec("turn_off",        "turn_off",        "Apaga calefaccion/AC"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    # ── Lavarropas ───────────────────────────────────────────────────────────

    washing_machine = CapabilityManifest(
        device_id="washer-laundry-01",
        device_name="Lavarropas",
        manufacturer="Samsung",
        model="WW90T",
        firmware="3.1",
        category=DeviceCategory.HYBRID,
        tags=["appliance", "smart-plug"],
        sensors=[
            SensorSpec("cycle_state",     "string",  "Estado del ciclo (idle/running/done)"),
            SensorSpec("time_remaining",  "integer", "Minutos restantes", unit="min"),
            SensorSpec("power_watts",     "float",   "Consumo actual", unit="W"),
        ],
        events=[
            EventSpec("cycle_complete", Urgency.INFO,    "Ciclo de lavado completado"),
            EventSpec("cycle_started",  Urgency.INFO,    "Ciclo de lavado iniciado"),
            EventSpec("door_open",      Urgency.INFO,    "Puerta abierta"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    # ── Medidor de consumo electrico ─────────────────────────────────────────

    power_meter = CapabilityManifest(
        device_id="power-meter-main-01",
        device_name="Medidor de consumo electrico",
        manufacturer="Shelly",
        model="Pro 3EM",
        firmware="0.12",
        category=DeviceCategory.SENSOR,
        tags=["power-meter", "sensor"],
        sensors=[
            SensorSpec("total_watts",   "float",   "Consumo total del hogar", unit="W"),
            SensorSpec("baseline_watts","float",   "Consumo base historico",  unit="W"),
            SensorSpec("circuit_a",     "float",   "Circuito A (cocina)",     unit="W"),
            SensorSpec("circuit_b",     "float",   "Circuito B (dormitorios)",unit="W"),
        ],
        events=[
            EventSpec("power_spike",   Urgency.WARNING, "Consumo supera 150% del baseline"),
            EventSpec("power_normal",  Urgency.INFO,    "Consumo volvio a niveles normales"),
            EventSpec("nobody_home",   Urgency.INFO,    "Consumo minimo sostenido: posible ausencia"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    return [phone_rodrigo, phone_family, lights_main, thermostat,
            washing_machine, power_meter]


# ── Escenario 1 — Nadie en casa ───────────────────────────────────────────────

async def scenario_nobody_home(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO 1: Nadie en casa → modo ahorro")
    print("="*60)

    # 1. El celular de Rodrigo sale del perimetro WiFi
    print("\n  [Context] Celular de Rodrigo salio del WiFi del hogar")
    state = hub.update_presence(PresenceSignal(
        device_id="phone-rodrigo-01",
        signal_type=ContextSignalType.PRESENCE,
        present=False,
        confidence=0.7,
        member_id="rodrigo",
    ))
    print(f"  [Occupancy] occupied={state.occupied} | "
          f"confidence={state.confidence:.0%} | "
          f"members_home={state.members_home} | "
          f"signals={state.signals_used}")

    # 2. El medidor detecta consumo minimo sostenido
    event = DeviceEvent(
        device_id="power-meter-main-01",
        event_id="nobody_home",
        severity=Urgency.INFO,
        data={
            "total_watts":    45.0,
            "baseline_watts": 380.0,
            "ratio":          0.12,
            "sustained_minutes": 35,
        },
    )
    await hub.receive_event(event)

    # 3. La IA construye el intent de ahorro de energia
    intent = Intent(
        intent=IntentClass("save_energy"),
        urgency=Urgency.INFO,
        context={
            "trigger":      "nobody_home",
            "absent_since": "35 minutos",
            "target_brightness": 0,
            "target_temp":       17,
            "target_position":   0,
            "message":      "Modo ahorro activado: nadie en casa.",
        },
    )
    result = await hub.execute_intent(intent, executor)

    print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
    for r in result.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} [{r.device_id}] {r.action} → {json.dumps(r.response)}")


# ── Escenario 2 — Lavarropas termino ─────────────────────────────────────────

async def scenario_laundry_done(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO 2: Lavarropas termino → recordatorio")
    print("="*60)

    # El lavarropas emite cycle_complete
    event = DeviceEvent(
        device_id="washer-laundry-01",
        event_id="cycle_complete",
        severity=Urgency.INFO,
        data={
            "cycle_type":     "cotton_60",
            "duration_min":   95,
            "spin_rpm":       1200,
        },
    )
    await hub.receive_event(event)

    # La IA notifica a la familia
    intent = Intent(
        intent=IntentClass("remind_chore"),
        urgency=Urgency.INFO,
        context={
            "trigger":  "cycle_complete",
            "device":   "washer-laundry-01",
            "message":  "El lavarropas termino (ciclo algodon 60°, 95 min). "
                        "No olvides pasar la ropa al secarropas.",
        },
    )
    result = await hub.execute_intent(intent, executor)

    print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
    for r in result.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} [{r.device_id}] {r.action} → {json.dumps(r.response)}")


# ── Escenario 3 — Pico de consumo ────────────────────────────────────────────

async def scenario_power_spike(hub: DoSyncHub, executor: SimulatedExecutor):
    print("\n" + "="*60)
    print("  ESCENARIO 3: Pico de consumo → alerta e investigacion")
    print("="*60)

    # El medidor detecta consumo anormal en el circuito de cocina
    event = DeviceEvent(
        device_id="power-meter-main-01",
        event_id="power_spike",
        severity=Urgency.WARNING,
        data={
            "total_watts":    2840.0,
            "baseline_watts": 380.0,
            "ratio":          7.47,
            "circuit_a":      2450.0,   # cocina: posible horno + microondas + pava electrica
            "circuit_b":      390.0,
            "sustained_seconds": 45,
        },
    )
    await hub.receive_event(event)

    # La IA alerta a la familia con contexto especifico
    intent = Intent(
        intent=IntentClass.ALERT_ANOMALY,
        urgency=Urgency.WARNING,
        context={
            "trigger":   "power_spike",
            "message":   "Consumo electrico inusual detectado: 2840W "
                         "(7.5x el baseline). Circuito de cocina: 2450W. "
                         "Verifica que no haya algo encendido innecesariamente.",
            "device_id": "power-meter-main-01",
            "circuit":   "cocina",
        },
    )
    result = await hub.execute_intent(intent, executor)

    print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
    for r in result.results:
        icon = "✓" if r.success else "✗"
        print(f"  {icon} [{r.device_id}] {r.action} → {json.dumps(r.response)}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    hub      = DoSyncHub()
    executor = SimulatedExecutor(failure_rate=0.0)

    def on_event(event: DeviceEvent):
        print(f"\n  [HUB EVENT] {event.device_id} → "
              f"{event.event_id} [{event.severity.value}]")
    hub.on_event(on_event)

    print("\n── Registrando dispositivos ──────────────────────────")
    for device in build_energy_devices():
        hub.register_device(device)
    print(f"  {len(hub.registry.all())} dispositivos registrados.")

    # Estado inicial de presencia
    print("\n── Estado inicial de ocupacion ──────────────────────")
    state = hub.get_occupancy()
    print(f"  occupied={state.occupied} | "
          f"confidence={state.confidence:.0%} | "
          f"signals={state.signals_used} (sin señales aun)")

    await scenario_nobody_home(hub, executor)
    await scenario_laundry_done(hub, executor)
    await scenario_power_spike(hub, executor)

    print("\n── Audit log ─────────────────────────────────────────")
    for entry in hub.audit_log.entries():
        kind = entry.get("type", "?")
        h    = entry.get("hash", "")[:10]
        extra = ""
        if kind == "presence_updated":
            extra = f" | occupied={entry.get('occupied')} conf={entry.get('occ_confidence', 0):.0%}"
        elif kind == "intent_executed":
            extra = f" | {entry.get('intent')} [{entry.get('urgency')}]"
        elif kind == "device_event":
            extra = f" | {entry.get('device_id')} → {entry.get('event_id')}"
        print(f"  [{h}] {kind}{extra}")

    print(f"\n  Integridad del audit log: "
          f"{'VALIDA' if hub.audit_log.verify() else 'COMPROMETIDA'}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
