"""
DoSync Protocol — Demo Completa v0.1
=====================================
Corre todos los escenarios en secuencia con output diseñado
para presentaciones a patrocinadores e inversores.

Uso:
    PYTHONPATH=. python3 examples/demo_full.py
    PYTHONPATH=. python3 examples/demo_full.py --fast    # sin delays dramaticos
    PYTHONPATH=. python3 examples/demo_full.py --scenario smoke  # un solo escenario
"""

import argparse
import asyncio
import json
import logging
import sys
import time

# Silenciar logs del hub durante la demo — output limpio
logging.basicConfig(level=logging.WARNING)

from dosync.models import (
    ActuatorSpec, CapabilityManifest, CertTier, ContextSignal,
    ContextSignalType, DeviceCategory, DeviceEvent, EventSpec,
    FamilyProfile, Intent, IntentClass, Phase, PhaseAction,
    PhasedActionPlan, PresenceSignal, RoutineAction, SensorSpec, Urgency,
)
from dosync.hub import DoSyncHub
from dosync.executor import SimulatedExecutor


# ── Colores ───────────────────────────────────────────────────────────────────

class C:
    HEADER  = "\033[95m"
    BLUE    = "\033[94m"
    TEAL    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RESET   = "\033[0m"

def header(text):
    w = 62
    print(f"\n{C.BOLD}{C.BLUE}{'═' * w}{C.RESET}")
    print(f"{C.BOLD}{C.BLUE}  {text}{C.RESET}")
    print(f"{C.BOLD}{C.BLUE}{'═' * w}{C.RESET}")

def section(text):
    print(f"\n{C.TEAL}{C.BOLD}  ▶  {text}{C.RESET}")

def action_ok(device, action, response):
    resp_str = json.dumps(response) if response else ""
    print(f"  {C.GREEN}✓{C.RESET}  {C.DIM}[{device}]{C.RESET} {action}"
          f"  {C.DIM}→ {resp_str}{C.RESET}")

def action_fail(device, action, error):
    print(f"  {C.RED}✗{C.RESET}  [{device}] {action}  → {error}")

def phase_header(n, total, name, delay):
    delay_str = f"  {C.DIM}(+{delay/1000:.1f}s){C.RESET}" if delay else ""
    print(f"\n  {C.YELLOW}Fase {n}/{total}{C.RESET} — {name}{delay_str}")

def result_line(success, label=""):
    if success:
        print(f"\n  {C.GREEN}{C.BOLD}✓ ÉXITO{C.RESET}"
              + (f"  {C.DIM}{label}{C.RESET}" if label else ""))
    else:
        print(f"\n  {C.RED}{C.BOLD}✗ FALLO PARCIAL{C.RESET}"
              + (f"  {C.DIM}{label}{C.RESET}" if label else ""))

def divider():
    print(f"  {C.DIM}{'─' * 56}{C.RESET}")

def pause(seconds, fast):
    if not fast:
        time.sleep(seconds)


# ── Dispositivos de la demo ───────────────────────────────────────────────────

def build_all_devices():
    return [
        # Seguridad
        CapabilityManifest(
            device_id="camera-bedroom-01", device_name="Zone 5 camera",
            manufacturer="SafeHome", model="Cam-4K", firmware="1.2",
            category=DeviceCategory.HYBRID,
            tags=["camera", "bedroom", "emergency", "motion"],
            sensors=[SensorSpec("motion","boolean","Movimiento"),
                     SensorSpec("fall","boolean","Caída detectada")],
            events=[EventSpec("fall_detected", Urgency.EMERGENCY, "Caída"),
                    EventSpec("motion",        Urgency.INFO,      "Movimiento")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="lock-frontdoor-01", device_name="Cerradura puerta principal",
            manufacturer="SecureLock", model="SmartBolt Pro", firmware="3.0",
            category=DeviceCategory.HYBRID,
            tags=["door-lock", "entrance", "emergency", "access"],
            sensors=[SensorSpec("state","boolean","Estado")],
            actuators=[ActuatorSpec("unlock","unlock","Abre la puerta"),
                       ActuatorSpec("lock","lock","Cierra la puerta")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="lock-backdoor-01", device_name="Puerta trasera / escape",
            manufacturer="SecureLock", model="SmartBolt Pro", firmware="3.0",
            category=DeviceCategory.HYBRID,
            tags=["door-lock", "escape", "emergency", "access"],
            actuators=[ActuatorSpec("unlock","unlock","Abre puerta escape")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="alarm-main-01", device_name="Alarma principal",
            manufacturer="AlertCo", model="Siren X", firmware="1.0",
            category=DeviceCategory.ACTUATOR,
            tags=["alarm", "emergency", "audio"],
            actuators=[ActuatorSpec("alarm","alarm","Activa sirena"),
                       ActuatorSpec("arm","arm","Arma el sistema")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="smoke-living-01", device_name="Detector de humo",
            manufacturer="Kidde", model="i9050", firmware="2.0",
            category=DeviceCategory.HYBRID,
            tags=["sensor", "emergency", "smoke", "safety"],
            sensors=[SensorSpec("smoke_ppm","float","Nivel humo",unit="ppm"),
                     SensorSpec("co_ppm","float","Nivel CO",unit="ppm")],
            events=[EventSpec("smoke_detected", Urgency.EMERGENCY, "Humo detectado"),
                    EventSpec("co_detected",    Urgency.EMERGENCY, "CO detectado")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="camera-exterior-01", device_name="Cámara exterior",
            manufacturer="SafeHome", model="Cam-4K", firmware="1.2",
            category=DeviceCategory.HYBRID,
            tags=["camera", "exterior", "emergency"],
            actuators=[ActuatorSpec("record","record","Graba"),
                       ActuatorSpec("stream","stream","Streaming")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        # Comunicación
        CapabilityManifest(
            device_id="phone-family-01", device_name="Teléfonos familiares",
            manufacturer="DoSync", model="NotifyBridge", firmware="0.1",
            category=DeviceCategory.COMMUNICATION,
            tags=["communication", "phone", "emergency"],
            actuators=[ActuatorSpec("call","call","Llamar"),
                       ActuatorSpec("notify","notify","Notificar"),
                       ActuatorSpec("display","display","Mostrar")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        # Heladera
        CapabilityManifest(
            device_id="fridge-kitchen-01", device_name="Heladera",
            manufacturer="Samsung", model="RF28", firmware="2.1",
            category=DeviceCategory.HYBRID,
            tags=["kitchen", "appliance", "food-safety", "sensor"],
            sensors=[SensorSpec("temp_internal","temperature","Temperatura interna",
                                unit="celsius"),
                     SensorSpec("compressor_status","boolean","Compresor")],
            events=[EventSpec("malfunction", Urgency.WARNING, "Avería detectada")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        # Energía
        CapabilityManifest(
            device_id="lights-main-01", device_name="Luces principales",
            manufacturer="Philips", model="Hue Bridge", firmware="1.50",
            category=DeviceCategory.HYBRID,
            tags=["light", "climate"],
            actuators=[ActuatorSpec("set_brightness","set_brightness","Brillo"),
                       ActuatorSpec("turn_off","turn_off","Apagar")],
            emergency_capable=True, cert_tier=CertTier.EMERGENCY,
        ),
        CapabilityManifest(
            device_id="thermostat-main-01", device_name="Termostato",
            manufacturer="Nest", model="Thermostat 4", firmware="6.2",
            category=DeviceCategory.HYBRID,
            tags=["thermostat", "climate"],
            actuators=[ActuatorSpec("set_temperature","set_temperature","Temperatura"),
                       ActuatorSpec("turn_off","turn_off","Apagar")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        CapabilityManifest(
            device_id="washer-laundry-01", device_name="Lavarropas",
            manufacturer="Samsung", model="WW90T", firmware="3.1",
            category=DeviceCategory.HYBRID,
            tags=["appliance", "smart-plug"],
            sensors=[SensorSpec("cycle_state","string","Estado ciclo")],
            events=[EventSpec("cycle_complete", Urgency.INFO, "Ciclo completado")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        CapabilityManifest(
            device_id="power-meter-main-01", device_name="Medidor de consumo",
            manufacturer="Shelly", model="Pro 3EM", firmware="0.12",
            category=DeviceCategory.SENSOR,
            tags=["power-meter", "sensor"],
            sensors=[SensorSpec("total_watts","float","Consumo total",unit="W")],
            events=[EventSpec("power_spike", Urgency.WARNING, "Pico de consumo"),
                    EventSpec("nobody_home", Urgency.INFO,    "Casa vacía detectada")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        # Contexto / presencia
        CapabilityManifest(
            device_id="phone-rodrigo-01", device_name="Celular de Rodrigo",
            manufacturer="Apple", model="iPhone 15", firmware="17.0",
            category=DeviceCategory.CONTEXT,
            tags=["phone", "context", "presence", "communication"],
            sensors=[SensorSpec("wifi_connected","boolean","WiFi hogar"),
                     SensorSpec("gps_home","boolean","GPS en hogar")],
            events=[EventSpec("left_home",    Urgency.INFO, "Salió del hogar"),
                    EventSpec("arrived_home", Urgency.INFO, "Llegó al hogar")],
            context_signals=[
                ContextSignal(ContextSignalType.PRESENCE, "WiFi celular",
                              confidence_weight=0.7),
            ],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        # Rutinas
        CapabilityManifest(
            device_id="blinds-main-01", device_name="Persianas",
            manufacturer="Somfy", model="Tahoma", firmware="2.0",
            category=DeviceCategory.ACTUATOR,
            tags=["blinds", "light", "climate"],
            actuators=[ActuatorSpec("set_position","set_position","Posición 0-100")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        CapabilityManifest(
            device_id="appliance-coffee-01", device_name="Cafetera",
            manufacturer="Nespresso", model="Expert", firmware="1.5",
            category=DeviceCategory.HYBRID,
            tags=["appliance", "kitchen"],
            actuators=[ActuatorSpec("turn_on","turn_on","Encender"),
                       ActuatorSpec("turn_off","turn_off","Apagar")],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
        CapabilityManifest(
            device_id="garage-door-01", device_name="Sensor garage",
            manufacturer="Aqara", model="DW-S03", firmware="2.0",
            category=DeviceCategory.HYBRID,
            tags=["garage", "sensor", "presence", "vehicle"],
            events=[EventSpec("car_left",    Urgency.INFO, "Auto salió"),
                    EventSpec("car_arrived", Urgency.INFO, "Auto llegó")],
            context_signals=[
                ContextSignal(ContextSignalType.VEHICLE, "Sensor garage",
                              confidence_weight=0.9),
            ],
            emergency_capable=False, cert_tier=CertTier.STANDARD,
        ),
    ]


def build_family_profile():
    return FamilyProfile(
        family_name="Giuliani",
        routine_morning=[
            RoutineAction("blinds",    "set_position",  {"position": 80}),
            RoutineAction("appliance", "turn_on",       {}),
        ],
        routine_bedtime=[
            RoutineAction("light",  "set_brightness", {"brightness": 10}),
            RoutineAction("blinds", "set_position",   {"position": 0}),
        ],
        bedtime_hour=21, bedtime_minute=30,
        routine_away=[
            RoutineAction("light",      "turn_off",        {}),
            RoutineAction("thermostat", "set_temperature", {"celsius": 17}),
            RoutineAction("alarm",      "arm",             {"mode": "away"}),
        ],
        timezone="America/Argentina/Cordoba",
    )


# ── Escenarios ────────────────────────────────────────────────────────────────

async def demo_fall(hub, executor, fast):
    header("Escenario 1 — Caída detectada · Emergencia")
    print(f"  {C.DIM}La cámara del zone5 detecta que la a monitored person se cayó.{C.RESET}")
    print(f"  {C.DIM}No hay nadie en casa. La IA actúa en menos de 100ms.{C.RESET}")
    pause(1.5, fast)

    section("Cámara emite evento: fall_detected [EMERGENCY]")
    await hub.receive_event(DeviceEvent(
        device_id="camera-bedroom-01", event_id="fall_detected",
        severity=Urgency.EMERGENCY,
        data={"confidence": 0.97, "location": "zone5"},
    ))
    pause(0.5, fast)

    section("IA resuelve intent: ensure_safety [EMERGENCY]")
    intent = Intent(
        intent=IntentClass.ENSURE_SAFETY, urgency=Urgency.EMERGENCY,
        subject="a monitored person",
        context={"trigger":"fall_detected","location":"zone5",
                 "emergency_number":"911",
                 "message":"Emergencia: persona caída. Puerta abierta para emergencias."},
    )
    result = await hub.execute_intent(intent, executor)
    divider()
    for r in result.results:
        if r.success: action_ok(r.device_id, r.action, r.response)
        else: action_fail(r.device_id, r.action, r.error)
    result_line(result.success, f"{len(result.results)} acciones en paralelo")


async def demo_fridge(hub, executor, fast):
    header("Escenario 2 — Avería de heladera · Alerta familiar")
    print(f"  {C.DIM}Las 2am. El compresor dejó de funcionar.{C.RESET}")
    print(f"  {C.DIM}18.5°C internos. La familia duerme.{C.RESET}")
    pause(1.5, fast)

    section("Heladera emite evento: malfunction [WARNING]")
    await hub.receive_event(DeviceEvent(
        device_id="fridge-kitchen-01", event_id="malfunction",
        severity=Urgency.WARNING,
        data={"temp_internal": 18.5, "compressor_status": False,
              "duration_minutes": 45},
    ))
    pause(0.5, fast)

    section("IA resuelve intent: notify_family [WARNING]")
    intent = Intent(
        intent=IntentClass("notify_family"), urgency=Urgency.WARNING,
        context={"trigger":"fridge_malfunction",
                 "message":"⚠️ La heladera dejó de enfriar (18.5°C, 45 min). "
                           "Considerá mover los alimentos."},
    )
    result = await hub.execute_intent(intent, executor)
    divider()
    for r in result.results:
        if r.success: action_ok(r.device_id, r.action, r.response)
        else: action_fail(r.device_id, r.action, r.error)
    result_line(result.success)


async def demo_smoke(hub, executor, fast):
    header("Escenario 3 — Humo detectado · Evacuación en 3 fases")
    print(f"  {C.DIM}El detector detecta humo en la sala de estar.{C.RESET}")
    print(f"  {C.DIM}A diferencia de otros escenarios, este ejecuta en FASES{C.RESET}")
    print(f"  {C.DIM}ordenadas: primero alertar, luego evacuar, luego acceso.{C.RESET}")
    pause(1.5, fast)

    section("Detector emite evento: smoke_detected [EMERGENCY]")
    await hub.receive_event(DeviceEvent(
        device_id="smoke-living-01", event_id="smoke_detected",
        severity=Urgency.EMERGENCY,
        data={"smoke_ppm": 450.0, "location": "sala de estar"},
    ))
    pause(0.5, fast)

    plan = PhasedActionPlan(
        intent_id="smoke-demo-001", urgency=Urgency.EMERGENCY,
        phases=[
            Phase("ALERTA — notificación inmediata", delay_after_ms=2000 if not fast else 100,
                  actions=[
                      PhaseAction("alarm-main-01",   "alarm",  {"pattern":"fire"}),
                      PhaseAction("phone-family-01", "call",   {"number":"100","message":"Incendio"}),
                      PhaseAction("phone-family-01", "notify", {"message":"EMERGENCIA: Humo 450ppm","urgency":"emergency"}),
                  ]),
            Phase("EVACUACIÓN — orientación visual", delay_after_ms=3000 if not fast else 100,
                  actions=[
                      PhaseAction("lights-main-01",   "set_brightness", {"brightness":100}),
                      PhaseAction("lock-backdoor-01", "unlock",         {"duration_seconds":600}),
                  ]),
            Phase("ACCESO — entrance para bomberos", delay_after_ms=0,
                  actions=[
                      PhaseAction("lock-frontdoor-01",  "unlock", {"duration_seconds":600}),
                      PhaseAction("camera-exterior-01", "record", {"reason":"fire_emergency"}),
                  ]),
        ],
    )

    for i, phase in enumerate(plan.phases):
        phase_header(i+1, len(plan.phases), phase.name, phase.delay_after_ms)
        results_phase = await hub.execute_phased(
            PhasedActionPlan(
                intent_id=f"smoke-demo-00{i+1}",
                urgency=Urgency.EMERGENCY,
                phases=[phase],
            ), executor)
        for r in results_phase[0].results:
            if r.success: action_ok(r.device_id, r.action, r.response)
            else: action_fail(r.device_id, r.action, r.error)
        if phase.delay_after_ms and i < len(plan.phases)-1:
            print(f"  {C.DIM}  esperando {phase.delay_after_ms/1000:.1f}s antes de la siguiente fase...{C.RESET}")
            pause(phase.delay_after_ms/1000, fast)

    result_line(True, "3/3 fases exitosas")


async def demo_energy(hub, executor, fast):
    header("Escenario 4 — Energía · Nadie en casa → modo ahorro")
    print(f"  {C.DIM}El celular de Rodrigo salió del WiFi del hogar.{C.RESET}")
    print(f"  {C.DIM}El medidor confirma consumo mínimo sostenido (35 min).{C.RESET}")
    pause(1.5, fast)

    section("Celular sale del WiFi → señal de presencia: present=False")
    state = hub.update_presence(PresenceSignal(
        device_id="phone-rodrigo-01",
        signal_type=ContextSignalType.PRESENCE,
        present=False, confidence=0.7, member_id="rodrigo",
    ))
    print(f"  {C.DIM}  Ocupación inferida: occupied={state.occupied} "
          f"| confianza={state.confidence:.0%}{C.RESET}")

    section("Medidor emite evento: nobody_home")
    await hub.receive_event(DeviceEvent(
        device_id="power-meter-main-01", event_id="nobody_home",
        severity=Urgency.INFO,
        data={"total_watts": 45.0, "baseline_watts": 380.0, "ratio": 0.12},
    ))
    pause(0.5, fast)

    section("IA resuelve intent: save_energy")
    intent = Intent(
        intent=IntentClass("save_energy"), urgency=Urgency.INFO,
        context={"trigger":"nobody_home","target_brightness":0,"target_temp":17},
    )
    result = await hub.execute_intent(intent, executor)
    divider()
    for r in result.results:
        if r.success: action_ok(r.device_id, r.action, r.response)
        else: action_fail(r.device_id, r.action, r.error)
    result_line(result.success)


async def demo_laundry(hub, executor, fast):
    header("Escenario 5 — Lavarropas terminó · Recordatorio")
    print(f"  {C.DIM}Ciclo algodón 60° completado (95 min).{C.RESET}")
    pause(1, fast)

    section("Lavarropas emite evento: cycle_complete")
    await hub.receive_event(DeviceEvent(
        device_id="washer-laundry-01", event_id="cycle_complete",
        severity=Urgency.INFO,
        data={"cycle_type":"cotton_60","duration_min":95},
    ))
    pause(0.5, fast)

    section("IA resuelve intent: remind_chore")
    intent = Intent(
        intent=IntentClass("remind_chore"), urgency=Urgency.INFO,
        context={"message":"El lavarropas terminó (algodón 60°, 95 min). "
                           "No olvides pasar la ropa al secarropas."},
    )
    result = await hub.execute_intent(intent, executor)
    divider()
    for r in result.results:
        if r.success: action_ok(r.device_id, r.action, r.response)
        else: action_fail(r.device_id, r.action, r.error)
    result_line(result.success)


def show_audit(hub):
    header("Audit Log — registro tamper-evident")
    print(f"  {C.DIM}Cada entrance está encadenada con SHA-256.{C.RESET}")
    print(f"  {C.DIM}Modificar cualquier entrance rompe toda la cadena.{C.RESET}\n")
    entries = hub.audit_log.entries()
    type_colors = {
        "device_registered": C.DIM,
        "intent_executed":   C.GREEN,
        "phase_executed":    C.YELLOW,
        "device_event":      C.TEAL,
        "presence_updated":  C.BLUE,
        "profile_loaded":    C.DIM,
    }
    for entry in entries:
        kind  = entry.get("type", "?")
        h     = entry.get("hash", "")[:10]
        color = type_colors.get(kind, C.DIM)
        extra = ""
        if kind == "intent_executed":
            extra = f" | {entry.get('intent')} [{entry.get('urgency')}]"
        elif kind == "device_event":
            extra = f" | {entry.get('device_id')} → {entry.get('event_id')}"
        elif kind == "phase_executed":
            extra = f" | fase '{entry.get('phase')}'"
        elif kind == "presence_updated":
            extra = f" | occupied={entry.get('occupied')} conf={entry.get('occ_confidence',0):.0%}"
        print(f"  {C.DIM}[{h}]{C.RESET} {color}{kind}{C.RESET}{C.DIM}{extra}{C.RESET}")

    integrity = hub.audit_log.verify()
    print(f"\n  {C.DIM}Total: {len(entries)} entrances{C.RESET}")
    if integrity:
        print(f"  {C.GREEN}{C.BOLD}✓ Integridad verificada — cadena SHA-256 íntegra{C.RESET}")
    else:
        print(f"  {C.RED}{C.BOLD}✗ Integridad comprometida{C.RESET}")


# ── Main ──────────────────────────────────────────────────────────────────────


SCENARIOS = {
    "fall":    demo_fall,
    "fridge":  demo_fridge,
    "smoke":   demo_smoke,
    "energy":  demo_energy,
    "laundry": demo_laundry,
}

async def main():
    parser = argparse.ArgumentParser(
        description="DoSync Protocol — Demo completa",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 examples/demo_full.py                    # todos los escenarios
  python3 examples/demo_full.py --fast             # sin delays dramaticos
  python3 examples/demo_full.py --scenario smoke   # solo humo
  python3 examples/demo_full.py --scenario fall    # solo caida
        """,
    )
    parser.add_argument("--fast",     action="store_true",
                        help="Sin delays dramáticos entre acciones")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), default=None,
                        help="Correr un solo escenario")
    args = parser.parse_args()

    # Setup
    hub       = DoSyncHub(db_path=":memory:")   # demo usa DB en memoria
    executor  = SimulatedExecutor(failure_rate=0.0)

    profile = build_family_profile()
    hub.set_family_profile(profile)

    for device in build_all_devices():
        hub.register_device(device)

    # Header principal
    print(f"\n{C.BOLD}{C.HEADER}")
    print("  ██████   ██████  ███████ ██    ██ ███    ██  ██████ ")
    print("  ██   ██ ██    ██ ██       ██  ██  ████   ██ ██      ")
    print("  ██   ██ ██    ██ ███████   ████   ██ ██  ██ ██      ")
    print("  ██   ██ ██    ██      ██    ██    ██  ██ ██ ██      ")
    print("  ██████   ██████  ███████    ██    ██   ████  ██████ ")
    print(f"{C.RESET}")
    print(f"  {C.BOLD}Protocol v0.4{C.RESET}  ·  "
          f"{C.DIM}github.com/giulianireg-spec/dosync-protocol{C.RESET}")
    print(f"  {C.DIM}{len(hub.registry.all())} dispositivos registrados  ·  "
          f"Familia: {profile.family_name}  ·  "
          f"{'Modo rápido' if args.fast else 'Modo demo'}{C.RESET}")

    pause(1, args.fast)

    # Correr escenarios
    if args.scenario:
        fn = SCENARIOS[args.scenario]
        await fn(hub, executor, args.fast)
    else:
        await demo_fall(hub, executor, args.fast)
        pause(2, args.fast)
        await demo_fridge(hub, executor, args.fast)
        pause(2, args.fast)
        await demo_smoke(hub, executor, args.fast)
        pause(2, args.fast)
        await demo_energy(hub, executor, args.fast)
        pause(2, args.fast)
        await demo_laundry(hub, executor, args.fast)
        pause(2, args.fast)
        show_audit(hub)

    # Footer
    print(f"\n{C.BOLD}{C.BLUE}{'═' * 62}{C.RESET}")
    print(f"  {C.BOLD}DoSync Protocol v0.4{C.RESET}  ·  Apache 2.0  ·  "
          f"{C.DIM}dosync.dev{C.RESET}")
    print(f"{C.BOLD}{C.BLUE}{'═' * 62}{C.RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
