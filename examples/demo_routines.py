"""
DoSync — Escenarios de rutinas familiares

Demuestra el FamilyProfile y el DoSyncScheduler.
Cada familia configura sus propias rutinas — no hay valores impuestos.

Escenario 1: Buenos dias — primer movimiento del dia activa la rutina
Escenario 2: Hora de dormir — scheduler dispara a las 21:30
Escenario 3: Auto salio del garage — modo ausente automatico
"""

import asyncio
import json
import logging

from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
    ContextSignalType, DeviceCategory, DeviceEvent, EventSpec,
    FamilyProfile, Intent, IntentClass, PresenceSignal,
    RoutineAction, SensorSpec, Urgency,
)
from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor
from dosync.scheduler import DoSyncScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)


def build_routine_devices() -> list[CapabilityManifest]:

    motion_bedroom = CapabilityManifest(
        device_id="motion-master-bedroom-01",
        device_name="Sensor de movimiento dormitorio principal",
        manufacturer="Aqara",
        model="MS-S02",
        firmware="3.1",
        category=DeviceCategory.SENSOR,
        tags=["motion", "sensor", "bedroom", "presence"],
        sensors=[SensorSpec("motion", "boolean", "Movimiento detectado")],
        events=[
            EventSpec("motion_detected",    Urgency.INFO, "Movimiento detectado"),
            EventSpec("first_motion_today", Urgency.INFO, "Primer movimiento del dia"),
        ],
        context_signals=[
            ContextSignal(ContextSignalType.PRESENCE, "PIR dormitorio", confidence_weight=0.4),
            ContextSignal(ContextSignalType.ROUTINE,  "Patron matutino", confidence_weight=0.6),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    blinds = CapabilityManifest(
        device_id="blinds-main-01",
        device_name="Persianas principales",
        manufacturer="Somfy",
        model="Tahoma",
        firmware="2.0",
        category=DeviceCategory.ACTUATOR,
        tags=["blinds", "light", "climate"],
        actuators=[
            ActuatorSpec("set_position", "set_position",
                         "Posicion 0=cerrado 100=abierto", {"position": "int"}),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    coffee_maker = CapabilityManifest(
        device_id="appliance-coffee-01",
        device_name="Cafetera",
        manufacturer="Nespresso",
        model="Expert",
        firmware="1.5",
        category=DeviceCategory.HYBRID,
        tags=["appliance", "kitchen", "smart-plug"],
        sensors=[SensorSpec("ready", "boolean", "Lista para usar")],
        actuators=[
            ActuatorSpec("turn_on",  "turn_on",  "Enciende y calienta"),
            ActuatorSpec("turn_off", "turn_off", "Apaga"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    display_home = CapabilityManifest(
        device_id="display-kitchen-01",
        device_name="Pantalla de cocina",
        manufacturer="DoSync",
        model="HomeDisplay",
        firmware="0.1",
        category=DeviceCategory.ACTUATOR,
        tags=["display", "communication", "kitchen"],
        actuators=[
            ActuatorSpec("display",      "display",      "Muestra mensaje"),
            ActuatorSpec("show_weather", "show_weather", "Muestra clima del dia"),
            ActuatorSpec("show_agenda",  "show_agenda",  "Muestra agenda familiar"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    lights = CapabilityManifest(
        device_id="lights-main-01",
        device_name="Luces principales",
        manufacturer="Philips",
        model="Hue Bridge",
        firmware="1.50",
        category=DeviceCategory.HYBRID,
        tags=["light", "climate"],
        actuators=[
            ActuatorSpec("set_brightness", "set_brightness", "Ajusta brillo 0-100%"),
            ActuatorSpec("turn_off",       "turn_off",       "Apaga luces"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    garage_sensor = CapabilityManifest(
        device_id="garage-door-01",
        device_name="Sensor puerta de garage",
        manufacturer="Aqara",
        model="DW-S03",
        firmware="2.0",
        category=DeviceCategory.HYBRID,
        tags=["garage", "sensor", "presence", "vehicle"],
        sensors=[SensorSpec("door_state", "boolean", "Abierto/cerrado")],
        events=[
            EventSpec("car_left",    Urgency.INFO, "Auto salio del garage"),
            EventSpec("car_arrived", Urgency.INFO, "Auto entro al garage"),
        ],
        context_signals=[
            ContextSignal(ContextSignalType.VEHICLE,  "Sensor garage", confidence_weight=0.9),
            ContextSignal(ContextSignalType.PRESENCE, "Garage ocupado", confidence_weight=0.5),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    alarm = CapabilityManifest(
        device_id="alarm-main-01",
        device_name="Sistema de alarma",
        manufacturer="AlertCo",
        model="Panel Pro",
        firmware="2.0",
        category=DeviceCategory.HYBRID,
        tags=["alarm", "security"],
        actuators=[
            ActuatorSpec("arm",   "arm",   "Arma el sistema"),
            ActuatorSpec("alarm", "alarm", "Activa sirena"),
        ],
        emergency_capable=True,
        cert_tier=CertTier.EMERGENCY,
    )

    thermostat = CapabilityManifest(
        device_id="thermostat-main-01",
        device_name="Termostato principal",
        manufacturer="Nest",
        model="Thermostat 4",
        firmware="6.2",
        category=DeviceCategory.HYBRID,
        tags=["thermostat", "climate"],
        actuators=[
            ActuatorSpec("set_temperature", "set_temperature", "Ajusta temperatura"),
            ActuatorSpec("turn_off",        "turn_off",        "Apaga clima"),
        ],
        emergency_capable=False,
        cert_tier=CertTier.STANDARD,
    )

    return [motion_bedroom, blinds, coffee_maker, display_home,
            lights, garage_sensor, alarm, thermostat]


def build_family_profile() -> FamilyProfile:
    """
    Perfil de la familia Giuliani.
    Completamente configurable — cada familia define el suyo.
    """
    return FamilyProfile(
        family_name="Giuliani",

        routine_morning=[
            RoutineAction("blinds",    "set_position",  {"position": 80},
                          "Subir persianas al 80%"),
            RoutineAction("appliance", "turn_on",       {},
                          "Encender cafetera"),
            RoutineAction("display",   "show_weather",  {},
                          "Mostrar clima del dia en la pantalla de cocina"),
            RoutineAction("display",   "show_agenda",   {},
                          "Mostrar agenda familiar del dia"),
        ],

        routine_bedtime=[
            RoutineAction("light",  "set_brightness", {"brightness": 10},
                          "Atenuar luces al 10%"),
            RoutineAction("blinds", "set_position",   {"position": 0},
                          "Bajar persianas completamente"),
        ],
        bedtime_hour=21,
        bedtime_minute=30,

        routine_away=[
            RoutineAction("light",      "turn_off",        {},
                          "Apagar todas las luces"),
            RoutineAction("thermostat", "set_temperature", {"celsius": 17},
                          "Bajar temperatura a modo ahorro"),
            RoutineAction("alarm",      "arm",             {"mode": "away"},
                          "Armar sistema de alarma"),
        ],

        timezone="America/Argentina/Cordoba",
    )


# ── Escenario 1 — Buenos dias ─────────────────────────────────────────────────

async def scenario_good_morning(
    hub: DoSyncHub, scheduler: DoSyncScheduler, executor: SimulatedExecutor
):
    print("\n" + "="*60)
    print("  ESCENARIO 1: Buenos dias — primer movimiento del dia")
    print("="*60)

    # Sensor de movimiento detecta primera actividad
    event = DeviceEvent(
        device_id="motion-master-bedroom-01",
        event_id="first_motion_today",
        severity=Urgency.INFO,
        data={"time": "07:23", "location": "dormitorio principal"},
    )
    await hub.receive_event(event)

    # La IA dispara la rutina matutina via el scheduler
    print("\n  [Scheduler] Primer movimiento detectado → rutina de buenos dias")
    profile = hub.family_profile
    intent = Intent(
        intent=IntentClass.MORNING_ROUTINE,
        urgency=Urgency.INFO,
        context={
            "trigger": "first_motion_today",
            "family":  profile.family_name,
            "actions": [
                {"tag": a.tag, "action_type": a.action_type, "params": a.params}
                for a in profile.routine_morning
            ],
            "message": f"Buenos dias, {profile.family_name}.",
        },
    )
    result = await hub.execute_intent(intent, executor)
    print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
    for r in result.results:
        print(f"  {'✓' if r.success else '✗'} [{r.device_id}] "
              f"{r.action} → {json.dumps(r.response)}")


# ── Escenario 2 — Hora de dormir ──────────────────────────────────────────────

async def scenario_bedtime(
    hub: DoSyncHub, scheduler: DoSyncScheduler, executor: SimulatedExecutor
):
    print("\n" + "="*60)
    print("  ESCENARIO 2: Hora de dormir — scheduler dispara a las 21:30")
    print("="*60)

    # Simulamos que son las 21:30
    scheduler.simulate_time(21, 30)
    print(f"\n  [Scheduler] Tiempo simulado: 21:30 — verificando triggers...")

    # Disparamos manualmente el loop del scheduler una vez
    hour, minute = scheduler._current_time()
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

    for trigger in scheduler._triggers:
        if trigger.hour == hour and trigger.minute == minute:
            trigger.last_fired_date = today
            context = trigger.context_builder() if trigger.context_builder else {}
            intent = Intent(
                intent=trigger.intent_class,
                urgency=trigger.urgency,
                context=context,
            )
            print(f"  [Scheduler] Trigger '{trigger.name}' disparado")
            result = await hub.execute_intent(intent, executor)
            print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
            for r in result.results:
                print(f"  {'✓' if r.success else '✗'} [{r.device_id}] "
                      f"{r.action} → {json.dumps(r.response)}")


# ── Escenario 3 — Auto salio del garage ───────────────────────────────────────

async def scenario_car_left(
    hub: DoSyncHub, scheduler: DoSyncScheduler, executor: SimulatedExecutor
):
    print("\n" + "="*60)
    print("  ESCENARIO 3: Auto salio del garage → modo ausente")
    print("="*60)

    # Sensor del garage detecta que el auto salio
    event = DeviceEvent(
        device_id="garage-door-01",
        event_id="car_left",
        severity=Urgency.INFO,
        data={"time": "08:45", "direction": "out"},
    )
    await hub.receive_event(event)

    # Actualizamos presencia: el garage esta vacio = posible ausencia
    state = hub.update_presence(PresenceSignal(
        device_id="garage-door-01",
        signal_type=ContextSignalType.VEHICLE,
        present=False,
        confidence=0.9,
        member_id=None,
    ))
    print(f"\n  [Occupancy] occupied={state.occupied} | "
          f"confidence={state.confidence:.0%} | signals={state.signals_used}")

    # La IA dispara modo ausente
    print("  [Scheduler] Garage vacio → modo ausente")
    profile = hub.family_profile
    intent = Intent(
        intent=IntentClass.AWAY_MODE,
        urgency=Urgency.INFO,
        context={
            "trigger": "car_left",
            "family":  profile.family_name,
            "actions": [
                {"tag": a.tag, "action_type": a.action_type, "params": a.params}
                for a in profile.routine_away
            ],
            "message": "Modo ausente activado. Todos salieron.",
        },
    )
    result = await hub.execute_intent(intent, executor)
    print(f"\n  Intent result: {'EXITO' if result.success else 'FALLO PARCIAL'}")
    for r in result.results:
        print(f"  {'✓' if r.success else '✗'} [{r.device_id}] "
              f"{r.action} → {json.dumps(r.response)}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    hub       = DoSyncHub()
    executor  = SimulatedExecutor(failure_rate=0.0)
    scheduler = DoSyncScheduler(hub)

    def on_event(event: DeviceEvent):
        print(f"\n  [HUB EVENT] {event.device_id} → "
              f"{event.event_id} [{event.severity.value}]")
    hub.on_event(on_event)

    # Cargar el perfil familiar
    profile = build_family_profile()
    hub.set_family_profile(profile)
    scheduler.load_profile(profile)

    print(f"\n  Perfil cargado: {profile.family_name}")
    print(f"  Hora de dormir: {profile.bedtime_hour:02d}:{profile.bedtime_minute:02d}")
    print(f"  Rutina manana: {len(profile.routine_morning)} acciones")
    print(f"  Rutina noche:  {len(profile.routine_bedtime)} acciones")
    print(f"  Modo ausente:  {len(profile.routine_away)} acciones")

    print("\n── Registrando dispositivos ──────────────────────────")
    for device in build_routine_devices():
        hub.register_device(device)
    print(f"  {len(hub.registry.all())} dispositivos registrados.")

    await scenario_good_morning(hub, scheduler, executor)
    await scenario_bedtime(hub, scheduler, executor)
    await scenario_car_left(hub, scheduler, executor)

    print("\n── Audit log ─────────────────────────────────────────")
    for entry in hub.audit_log.entries():
        kind  = entry.get("type", "?")
        h     = entry.get("hash", "")[:10]
        extra = ""
        if kind == "profile_loaded":
            extra = f" | familia='{entry.get('family_name')}' bedtime={entry.get('bedtime')}"
        elif kind == "intent_executed":
            extra = f" | {entry.get('intent')} [{entry.get('urgency')}]"
        elif kind == "presence_updated":
            extra = f" | occupied={entry.get('occupied')} conf={entry.get('occ_confidence',0):.0%}"
        elif kind == "device_event":
            extra = f" | {entry.get('device_id')} → {entry.get('event_id')}"
        print(f"  [{h}] {kind}{extra}")

    print(f"\n  Integridad: {'VALIDA' if hub.audit_log.verify() else 'COMPROMETIDA'}\n")


if __name__ == "__main__":
    asyncio.run(main())
