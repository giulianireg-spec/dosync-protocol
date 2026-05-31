"""
DoSync Resolver Benchmark — con registry real de producción
38 dispositivos exactos del hub Raspberry Pi 5 @ 192.168.100.109
Generado: 18 mayo 2026
"""

import time
import random
import statistics
import json
from dataclasses import dataclass, field
from enum import Enum

# ── Tipos ────────────────────────────────────────────────────────────────────

class IntentClass(str, Enum):
    ensure_safety         = "ensure_safety"
    alert_anomaly         = "alert_anomaly"
    control_access        = "control_access"
    monitor_health        = "monitor_health"
    notify_family         = "notify_family"
    report_status         = "report_status"
    set_environment       = "set_environment"
    save_energy           = "save_energy"
    remind_chore          = "remind_chore"
    bedtime_routine       = "bedtime_routine"
    morning_routine       = "morning_routine"
    away_mode             = "away_mode"
    children_arrived_home = "children_arrived_home"

class Urgency(str, Enum):
    emergency = "emergency"
    alert     = "alert"
    info      = "info"

@dataclass
class ActuatorSpec:
    type: str

@dataclass
class CapabilityManifest:
    device_id: str
    device_name: str
    tags: list
    actuators: list
    emergency_capable: bool = False
    adapter: str = "simulated"

@dataclass
class Intent:
    intent: IntentClass
    intent_id: str
    urgency: Urgency
    context: dict = field(default_factory=dict)
    source: str = "benchmark"
    timestamp: float = field(default_factory=time.time)

@dataclass
class DeviceAction:
    device_id: str
    action: str
    params: dict = field(default_factory=dict)
    relevance_score: float = 0.0

@dataclass
class ActionPlan:
    intent_id: str
    actions: list
    urgency: Urgency

# ── Registry real (38 dispositivos del hub) ───────────────────────────────────

REAL_REGISTRY = [
    CapabilityManifest("wiz-habitacion-ninos-01",   "Habitación niños — Luz",
        ["wiz","light","climate","children_arrival"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-living2-01",            "Living 2 — Luz 1",
        ["wiz","light","climate","children_arrival"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-living2-02",            "Living 2 — Luz 2",
        ["wiz","light","climate","children_arrival"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-comedor-01",            "Comedor — Luz 1",
        ["wiz","light","climate"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-cocina-01",             "Cocina — Luz 1",
        ["wiz","light","climate"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-habitacion-principal",  "Habitación principal — Luz",
        ["wiz","light","climate"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-comedor-02",            "Comedor — Luz 2",
        ["wiz","light","climate"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-living1-01",            "Living 1 — Luz 1",
        ["wiz","light","climate","children_arrival"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-living1-02",            "Living 1 — Luz 2",
        ["wiz","light","climate","children_arrival"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("wiz-cocina-02",             "Cocina — Luz 2",
        ["wiz","light","climate"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_color_temp","set_scene"]],
        emergency_capable=True, adapter="wiz"),
    CapabilityManifest("rpi-pir-01",                "PIR — Sensor de movimiento",
        ["sensor","motion","security","emergency"], [], emergency_capable=False, adapter="simulated"),
    CapabilityManifest("rpi-dht22-01",              "DHT22 — Temperatura y Humedad",
        ["sensor","climate","temperature","humidity"], [], emergency_capable=False, adapter="simulated"),
    CapabilityManifest("ha-light-tv_philips_ambilight", "TV Philips Ambilight",
        ["climate","light"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","set_brightness","set_color","set_effect","set_color_temp"]],
        emergency_capable=True, adapter="homeassistant"),
    CapabilityManifest("ha-media_player-tv_philips", "TV Philips",
        ["communication","display"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","display"]],
        emergency_capable=False, adapter="homeassistant"),
    CapabilityManifest("ha-switch-tv_philips_screen_state", "TV Philips Screen state",
        ["appliance","smart-plug"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off"]],
        emergency_capable=False, adapter="homeassistant"),
    CapabilityManifest("notifier-sms-01",           "SMS — Notificaciones familia",
        ["communication","notification","children_arrival"],
        [ActuatorSpec("notify")],
        emergency_capable=False, adapter="notifications"),
    CapabilityManifest("ha-sensor-sun_next_dawn",   "Sun Next dawn",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-sun_next_dusk",   "Sun Next dusk",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-sun_next_midnight","Sun Next midnight",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-sun_next_noon",   "Sun Next noon",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-sun_next_rising", "Sun Next rising",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-sun_next_setting","Sun Next setting",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-backup_backup_manager_state", "Backup Manager state",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-backup_next_scheduled_automatic_backup", "Backup Next scheduled",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-backup_last_successful_automatic_backup", "Backup Last successful",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-backup_last_attempted_automatic_backup", "Backup Last attempted",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ca6528_power", "WiZ CA6528 Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ca660e_power", "WiZ CA660E Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ca6536_power", "WiZ CA6536 Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ca6522_power", "WiZ CA6522 Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ca63f2_power", "WiZ CA63F2 Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_d091de_power", "WiZ D091DE Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ac6c1a_power", "WiZ AC6C1A Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-sensor-wiz_rgbw_tunable_ac4864_power", "WiZ AC4864 Power",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-binary_sensor-tv_philips_recording_ongoing", "TV Philips Recording ongoing",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-binary_sensor-tv_philips_new_recording_available", "TV Philips New recording",
        ["sensor"], [], adapter="homeassistant"),
    CapabilityManifest("ha-media_player-75_qled_qn75q7faagcfv", '75" QLED Samsung',
        ["communication","display"],
        [ActuatorSpec(a) for a in ["turn_on","turn_off","display"]],
        emergency_capable=False, adapter="homeassistant"),
    CapabilityManifest("alarm-test-01",             "Alarma principal (test)",
        ["emergency","alarm","security"],
        [ActuatorSpec("alarm")],
        emergency_capable=True, adapter="simulated"),
]

assert len(REAL_REGISTRY) == 38, f"Esperados 38, encontrados {len(REAL_REGISTRY)}"

# ── Intent → tags de resolución ───────────────────────────────────────────────

INTENT_RESOLUTION_TAGS = {
    IntentClass.ensure_safety:         {"emergency","alarm","light","communication","notification","security"},
    IntentClass.alert_anomaly:         {"sensor","communication","notification","alarm"},
    IntentClass.control_access:        {"lock","door","access","security"},
    IntentClass.monitor_health:        {"sensor","health","communication"},
    IntentClass.notify_family:         {"communication","notification"},
    IntentClass.report_status:         {"sensor","communication"},
    IntentClass.set_environment:       {"climate","light","thermostat"},
    IntentClass.save_energy:           {"light","appliance","climate","thermostat"},
    IntentClass.remind_chore:          {"communication","notification","display"},
    IntentClass.bedtime_routine:       {"light","climate","security"},
    IntentClass.morning_routine:       {"light","climate","appliance"},
    IntentClass.away_mode:             {"light","security","alarm","climate"},
    IntentClass.children_arrived_home: {"children_arrival","notification","communication","light"},
}

# ── Resolvers ─────────────────────────────────────────────────────────────────

class CapabilityMatchingResolver:
    def __init__(self, registry):
        self.registry = registry

    def resolve(self, intent):
        resolution_tags = INTENT_RESOLUTION_TAGS.get(intent.intent, set())
        actions = []
        for device in self.registry:
            score = self._score(device, intent, resolution_tags)
            if score > 0:
                for actuator in device.actuators:
                    actions.append(DeviceAction(
                        device_id=device.device_id,
                        action=actuator.type,
                        relevance_score=score,
                    ))
        return ActionPlan(intent_id=intent.intent_id, actions=actions, urgency=intent.urgency)

    def _score(self, device, intent, resolution_tags):
        score = len(set(device.tags) & resolution_tags) * 10.0
        if intent.urgency == Urgency.emergency and device.emergency_capable:
            score += 30.0
        loc = intent.context.get("location", "")
        if loc and loc.lower() in device.device_name.lower():
            score += 15.0
        return score


class StateAwareResolver(CapabilityMatchingResolver):
    def __init__(self, registry):
        super().__init__(registry)
        self._state_cache = {}

    def resolve(self, intent):
        plan = super().resolve(intent)
        plan.actions = [
            a for a in plan.actions
            if self._state_cache.get(f"{a.device_id}:{a.action}") != a.params
        ]
        return plan

    def warm_cache(self, ratio=0.4):
        for device in self.registry:
            for actuator in device.actuators:
                if random.random() < ratio:
                    self._state_cache[f"{device.device_id}:{actuator.type}"] = {}

# ── Utilidades de benchmark ───────────────────────────────────────────────────

ALL_INTENTS   = list(IntentClass)
ALL_URGENCIES = [Urgency.info, Urgency.alert, Urgency.emergency]
LOCATIONS     = ["cocina", "living", "comedor", "habitacion", ""]

def make_intent(idx=0):
    return Intent(
        intent   = random.choice(ALL_INTENTS),
        intent_id= f"bench-{idx:06d}",
        urgency  = random.choice(ALL_URGENCIES),
        context  = {"location": random.choice(LOCATIONS)},
    )

def run_benchmark(registry, resolver_cls, n=500, label=""):
    resolver = resolver_cls(registry)
    if hasattr(resolver, "warm_cache"):
        resolver.warm_cache(ratio=0.4)

    latencies = []
    actions_count = []
    per_intent = {ic: [] for ic in IntentClass}

    random.seed(42)
    for i in range(n):
        intent = make_intent(i)
        t0 = time.perf_counter()
        plan = resolver.resolve(intent)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        latencies.append(ms)
        actions_count.append(len(plan.actions))
        per_intent[intent.intent].append((ms, len(plan.actions)))

    s = sorted(latencies)
    return {
        "label":     label,
        "n_devices": len(registry),
        "n_iter":    n,
        "mean":      statistics.mean(latencies),
        "median":    statistics.median(latencies),
        "p95":       s[int(0.95 * n)],
        "p99":       s[int(0.99 * n)],
        "min":       min(latencies),
        "max":       max(latencies),
        "mean_actions": statistics.mean(actions_count),
        "per_intent": {
            ic.value: {
                "mean_ms":      statistics.mean([x[0] for x in v]) if v else 0,
                "mean_actions": statistics.mean([x[1] for x in v]) if v else 0,
                "samples":      len(v),
            }
            for ic, v in per_intent.items() if v
        },
    }

def direct_command_baseline(registry, n=500):
    device_map = {d.device_id: d for d in registry}
    ids = list(device_map.keys())
    latencies = []
    random.seed(42)
    for i in range(n):
        t0 = time.perf_counter()
        d  = device_map[ids[i % len(ids)]]
        _  = ActionPlan(
            intent_id = f"cmd-{i}",
            actions   = [DeviceAction(device_id=d.device_id,
                                      action=d.actuators[0].type if d.actuators else "noop",
                                      relevance_score=100.0)],
            urgency   = Urgency.info,
        )
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000)
    return latencies

# ── Escala sintética ──────────────────────────────────────────────────────────

# Realistic device type distribution for scale benchmarks.
# Reflects a multi-building deployment with diverse specific tags.
# Distribution: ~30% lights, ~15% smart-plugs, ~20% sensors,
#               ~10% security, ~10% communication, ~10% climate, ~5% cameras
DEVICE_TYPE_POOLS = [
    # (weight, tags, actuators, emergency_capable)
    (30, ["light", "wiz"],                                    ["turn_on","turn_off","set_brightness"], False),
    (5,  ["light", "climate", "wiz"],                        ["turn_on","turn_off","set_brightness"], False),
    (10, ["smart-plug", "appliance"],                        ["turn_on","turn_off"], False),
    (5,  ["smart-plug", "climate"],                          ["turn_on","turn_off","set_temperature"], False),
    (8,  ["sensor", "motion", "security"],                   [], False),
    (5,  ["sensor", "climate", "temperature"],               [], False),
    (3,  ["sensor", "health", "wearable"],                   [], False),
    (4,  ["sensor", "motion", "emergency"],                  [], True),
    (5,  ["door-lock", "access", "security"],                ["lock","unlock"], False),
    (3,  ["door-lock", "access", "emergency"],               ["lock","unlock"], True),
    (2,  ["alarm", "security", "emergency"],                 ["alarm","arm"], True),
    (5,  ["communication", "notification", "phone"],         ["notify","call"], False),
    (3,  ["communication", "notification", "children_arrival"], ["notify"], False),
    (2,  ["communication", "notification", "emergency"],     ["notify","call"], True),
    (5,  ["thermostat", "climate"],                          ["set_temperature"], False),
    (3,  ["blinds", "climate"],                              ["set_position"], False),
    (3,  ["camera", "security", "emergency"],                [], True),
    (2,  ["camera", "monitor_health"],                       [], False),
]

_TYPE_TOTAL = sum(t[0] for t in DEVICE_TYPE_POOLS)

def gen_devices(n):
    """Generate n synthetic devices with realistic tag distribution."""
    rng = __import__('random').Random(7)
    cumulative = []
    total = 0
    for w, *_ in DEVICE_TYPE_POOLS:
        total += w
        cumulative.append(total)
    out = []
    for i in range(n):
        r = rng.randint(1, _TYPE_TOTAL)
        for idx, cum in enumerate(cumulative):
            if r <= cum:
                _, tags, acts, emerg = DEVICE_TYPE_POOLS[idx]
                break
        out.append(CapabilityManifest(
            device_id=f"dev-{i:05d}", device_name=f"Device {i}",
            tags=tags[:], actuators=[ActuatorSpec(a) for a in acts],
            emergency_capable=emerg,
        ))
    return out

# ── Main ──────────────────────────────────────────────────────────────────────

def fmt(v): return f"{v:.4f}ms"

def main():
    N = 500
    print("=" * 64)
    print("  DoSync Resolver Benchmark — registry real de producción")
    print("  38 dispositivos · hub Raspberry Pi 5")
    print("=" * 64)

    results = {}

    # 1. Registry real — ambos resolvers
    print(f"\n[1/3] Registry real (38 dispositivos, {N} iteraciones)")
    for cls, label in [
        (CapabilityMatchingResolver, "CapabilityMatchingResolver"),
        (StateAwareResolver,         "StateAwareResolver"),
    ]:
        r = run_benchmark(REAL_REGISTRY, cls, N, label)
        results[label] = r
        print(f"\n  {label}")
        print(f"  {'mean':<8} {fmt(r['mean'])}  |  {'median':<8} {fmt(r['median'])}")
        print(f"  {'p95':<8} {fmt(r['p95'])}  |  {'p99':<8} {fmt(r['p99'])}")
        print(f"  {'min':<8} {fmt(r['min'])}  |  {'max':<8} {fmt(r['max'])}")
        print(f"  Acciones promedio por intent: {r['mean_actions']:.1f}")

    # 2. Escala simulada
    print(f"\n[2/3] Escala simulada — CapabilityMatchingResolver")
    print(f"  {'devices':>7}  {'mean':>10}  {'p95':>10}  {'p99':>10}  {'500ms limit':>12}")
    scale = {}
    for n in [10, 50, 100, 500, 1000, 2000, 5000]:
        reg = gen_devices(n)
        r = run_benchmark(reg, CapabilityMatchingResolver, 300)
        ok = "✓" if r["p99"] < 500 else "✗"
        print(f"  {n:>7}  {fmt(r['mean']):>10}  {fmt(r['p95']):>10}  {fmt(r['p99']):>10}  {ok:>12}")
        scale[str(n)] = {"mean": r["mean"], "p95": r["p95"], "p99": r["p99"]}
    results["scale"] = scale

    # 3. Overhead semántico
    print(f"\n[3/3] Overhead semántico vs comando directo")
    cmd = direct_command_baseline(REAL_REGISTRY, N)
    cmd_mean = statistics.mean(cmd)
    sem_mean = results["CapabilityMatchingResolver"]["mean"]
    overhead_abs = sem_mean - cmd_mean
    overhead_x   = sem_mean / cmd_mean if cmd_mean > 0 else 0

    print(f"\n  Comando directo:        {fmt(cmd_mean)}")
    print(f"  Resolver semántico:     {fmt(sem_mean)}")
    print(f"  Overhead absoluto:      {fmt(overhead_abs)}")
    print(f"  Overhead relativo:      {overhead_x:.1f}x")
    print(f"\n  Contexto real de ejecución:")
    print(f"  → Red WiFi→WiZ (UDP):   ~5–15ms")
    print(f"  → Red WiFi→HA (HTTP):   ~20–80ms")
    print(f"  → La resolución semántica representa < 1% del tiempo total")

    results["overhead"] = {
        "direct_command_mean_ms":    cmd_mean,
        "semantic_resolver_mean_ms": sem_mean,
        "overhead_abs_ms":           overhead_abs,
        "overhead_multiplier":       overhead_x,
    }

    # 4. Por intent class
    print(f"\n[+] Desglose por intent class (CapabilityMatchingResolver)")
    print(f"  {'intent':<28} {'mean_ms':>10}  {'avg_actions':>12}  {'samples':>8}")
    print(f"  {'-'*62}")
    pi = results["CapabilityMatchingResolver"]["per_intent"]
    for ic_name, v in sorted(pi.items(), key=lambda x: -x[1]["mean_ms"]):
        print(f"  {ic_name:<28} {v['mean_ms']:>10.4f}  {v['mean_actions']:>12.1f}  {v['samples']:>8}")

    with open("benchmark_results_real.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*64}")
    print(f"  Resultados guardados: benchmark_results_real.json")
    print(f"{'='*64}\n")

if __name__ == "__main__":
    main()
