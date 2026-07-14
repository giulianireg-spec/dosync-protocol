"""
DEPRECATED (2026-07-12) — do not use. This tool carried a FROZEN copy of the
resolver and a resolution map over intents that no longer exist; its
"recall 0.49" is invalid (see docs/BENCHMARK-RECALL.md). Replaced by
tools/recall_benchmark.py, which imports the LIVE resolver. Retained for
historical reference only.
"""
"""
DoSync — Scoring weights sensitivity analysis
Varies each weight ±50% and measures precision/recall impact
over the 15 scenarios from the IEEE paper (Table 3).
"""
import sys, statistics
sys.path.insert(0, '/home/rgiuliani/dosync-protocol')
import logging
logging.disable(logging.CRITICAL)

from dosync.hub import DoSyncHub, INTENT_RESOLUTION_MAP
from dosync.models import (
    Intent, IntentClass, Urgency, ActionPlan, DeviceAction,
    CapabilityManifest, ActuatorSpec
)

hub = DoSyncHub(db_path='/home/rgiuliani/dosync-protocol/dosync.db')
reg = hub.registry

# ── Ground truth — 15 scenarios from paper Table 3 ───────────────────────────
# Expected devices per scenario based on expert knowledge of the 38-device registry
GROUND_TRUTH = {
    IntentClass.ENSURE_SAFETY: {
        "urgency": Urgency.EMERGENCY,
        "expected": {
            "alarm-test-01", "notifier-sms-01", "rpi-pir-01",
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight",
        }
    },
    IntentClass.ALERT_ANOMALY: {
        "urgency": Urgency.ALERT,
        "expected": {
            "notifier-sms-01", "ha-media_player-tv_philips",
            "ha-media_player-75_qled_qn75q7faagcfv",
        }
    },
    IntentClass.CONTROL_ACCESS: {
        "urgency": Urgency.INFO,
        "expected": set()  # no door-lock devices in registry
    },
    IntentClass.NOTIFY_FAMILY: {
        "urgency": Urgency.INFO,
        "expected": {
            "notifier-sms-01", "ha-media_player-tv_philips",
            "ha-media_player-75_qled_qn75q7faagcfv",
        }
    },
    IntentClass.SAVE_ENERGY: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight", "ha-switch-tv_philips_screen_state",
        }
    },
    IntentClass.BEDTIME_ROUTINE: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight",
        }
    },
    IntentClass.MORNING_ROUTINE: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight",
        }
    },
    IntentClass.CHILDREN_ARRIVED: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-habitacion-ninos-01", "wiz-living1-01", "wiz-living1-02",
            "wiz-living2-01", "wiz-living2-02", "notifier-sms-01",
        }
    },
    IntentClass.AWAY_MODE: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight", "alarm-test-01",
        }
    },
    IntentClass.SET_ENVIRONMENT: {
        "urgency": Urgency.INFO,
        "expected": {
            "wiz-living1-01", "wiz-living1-02", "wiz-living2-01", "wiz-living2-02",
            "wiz-comedor-01", "wiz-comedor-02", "wiz-cocina-01", "wiz-cocina-02",
            "wiz-habitacion-principal", "wiz-habitacion-ninos-01",
            "ha-light-tv_philips_ambilight", "rpi-dht22-01",
        }
    },
    IntentClass.REPORT_STATUS: {
        "urgency": Urgency.INFO,
        "expected": set(d.device_id for d in reg.all())  # all devices
    },
    IntentClass.MONITOR_HEALTH: {
        "urgency": Urgency.INFO,
        "expected": {"rpi-pir-01", "rpi-dht22-01"}
    },
    IntentClass.REMIND_CHORE: {
        "urgency": Urgency.INFO,
        "expected": {
            "notifier-sms-01", "ha-media_player-tv_philips",
            "ha-media_player-75_qled_qn75q7faagcfv",
        }
    },
}

# ── Resolver with configurable weights ───────────────────────────────────────
def resolve_with_weights(intent, w_tag, w_loc, w_emerg, w_act):
    """Run resolver with custom weights, return set of selected device_ids."""
    resolution = INTENT_RESOLUTION_MAP.get(intent.intent, {"tags": [], "actuators": []})
    target_tags = set(resolution.get("tags", []))
    target_actuators = set(resolution.get("actuators", []))
    generic_tags = {"light", "climate", "communication", "sensor", "appliance", "display"}
    specific_tags = target_tags - generic_tags

    # Candidate selection via index
    if target_tags:
        candidates = reg.find_by_tags(list(target_tags))
    else:
        candidates = reg.all()

    # Emergency: always include emergency_capable
    if intent.urgency == Urgency.EMERGENCY:
        candidate_ids = {d.device_id for d in candidates}
        for d in reg.find_emergency_capable():
            if d.device_id not in candidate_ids:
                candidates.append(d)

    selected = set()
    for device in candidates:
        score = 0.0
        device_tags = set(device.tags)

        # Specific tag check
        if specific_tags and not (specific_tags & device_tags):
            continue

        score += len(target_tags & device_tags) * w_tag
        if intent.urgency == Urgency.EMERGENCY and device.emergency_capable:
            score += w_emerg
        score += len(target_actuators & {a.type for a in device.actuators}) * w_act

        if score > 0:
            selected.add(device.device_id)

    return selected

def precision_recall(selected, expected):
    if not expected:
        return (1.0 if not selected else 0.0), 1.0
    if not selected:
        return 1.0, 0.0
    tp = len(selected & expected)
    p = tp / len(selected) if selected else 0.0
    r = tp / len(expected) if expected else 0.0
    return p, r

def f1(p, r):
    return 2*p*r/(p+r) if (p+r) > 0 else 0.0

# ── Baseline evaluation ───────────────────────────────────────────────────────
def evaluate(w_tag, w_loc, w_emerg, w_act):
    """Evaluate all 15 scenarios, return mean precision, recall, f1."""
    precisions, recalls, f1s = [], [], []
    for intent_class, scenario in GROUND_TRUTH.items():
        intent = Intent(
            intent=intent_class,
            urgency=scenario["urgency"],
            context={},
        )
        selected = resolve_with_weights(intent, w_tag, w_loc, w_emerg, w_act)
        expected = scenario["expected"]
        p, r = precision_recall(selected, expected)
        f = f1(p, r)
        precisions.append(p)
        recalls.append(r)
        f1s.append(f)
    return (
        statistics.mean(precisions),
        statistics.mean(recalls),
        statistics.mean(f1s),
    )

# ── Baseline (current weights) ────────────────────────────────────────────────
W_TAG   = 10.0
W_LOC   = 15.0
W_EMERG = 30.0
W_ACT   = 8.0

print("=" * 70)
print("  DoSync — Scoring Weights Sensitivity Analysis")
print("  Baseline: tag=10, loc=15, emerg=30, act=8")
print("=" * 70)

bp, br, bf = evaluate(W_TAG, W_LOC, W_EMERG, W_ACT)
print(f"\nBaseline results:")
print(f"  Precision: {bp:.3f}")
print(f"  Recall:    {br:.3f}")
print(f"  F1:        {bf:.3f}")

# ── Per-scenario baseline ─────────────────────────────────────────────────────
print(f"\nPer-scenario breakdown (baseline):")
print(f"  {'Scenario':<30} {'Prec':>6} {'Rec':>6} {'F1':>6}")
print(f"  {'-'*52}")
for intent_class, scenario in GROUND_TRUTH.items():
    intent = Intent(intent=intent_class, urgency=scenario["urgency"], context={})
    selected = resolve_with_weights(intent, W_TAG, W_LOC, W_EMERG, W_ACT)
    expected = scenario["expected"]
    p, r = precision_recall(selected, expected)
    f = f1(p, r)
    print(f"  {intent_class.value:<30} {p:>6.2f} {r:>6.2f} {f:>6.2f}")

# ── Sensitivity analysis ──────────────────────────────────────────────────────
print(f"\n{'=' * 70}")
print(f"  Sensitivity analysis — each weight varied ±25% and ±50%")
print(f"  {'Weight':<12} {'Variation':>10} {'Precision':>10} {'Recall':>10} {'F1':>10} {'ΔF1':>8}")
print(f"  {'-'*62}")

weights = {
    "tag (10)":   (W_TAG,   lambda v: evaluate(v, W_LOC, W_EMERG, W_ACT)),
    "loc (15)":   (W_LOC,   lambda v: evaluate(W_TAG, v, W_EMERG, W_ACT)),
    "emerg (30)": (W_EMERG, lambda v: evaluate(W_TAG, W_LOC, v, W_ACT)),
    "act (8)":    (W_ACT,   lambda v: evaluate(W_TAG, W_LOC, W_EMERG, v)),
}

for name, (base_val, fn) in weights.items():
    for pct in [-50, -25, 0, +25, +50]:
        val = base_val * (1 + pct/100)
        p, r, f = fn(val)
        delta = f - bf
        marker = " ◄ SENSITIVE" if abs(delta) > 0.05 else ""
        if pct == 0:
            print(f"  {name:<12} {'baseline':>10} {p:>10.3f} {r:>10.3f} {f:>10.3f} {'---':>8}")
        else:
            print(f"  {name:<12} {pct:>+9}% {p:>10.3f} {r:>10.3f} {f:>10.3f} {delta:>+8.3f}{marker}")
    print()

# ── Best weight combination search ───────────────────────────────────────────
print(f"{'=' * 70}")
print(f"  Grid search — best weight combination (F1 maximization)")
print(f"  Searching over tag=[5,10,15,20], loc=[10,15,20],")
print(f"               emerg=[15,20,25,30,40,50], act=[5,8,12,16]")
print()

best_f1, best_weights = 0.0, None
best_p, best_r = 0.0, 0.0
results = []

for wt in [5, 10, 15, 20]:
    for wl in [10, 15, 20]:
        for we in [15, 20, 25, 30, 40, 50]:
            for wa in [5, 8, 12, 16]:
                p, r, f = evaluate(wt, wl, we, wa)
                results.append((f, p, r, wt, wl, we, wa))
                if f > best_f1:
                    best_f1, best_p, best_r = f, p, r
                    best_weights = (wt, wl, we, wa)

results.sort(reverse=True)
print(f"  Top 5 weight combinations:")
print(f"  {'tag':>5} {'loc':>5} {'emerg':>7} {'act':>5} {'Prec':>8} {'Rec':>8} {'F1':>8}")
print(f"  {'-'*50}")
for f, p, r, wt, wl, we, wa in results[:5]:
    marker = " ◄ current" if (wt,wl,we,wa)==(10,15,30,8) else ""
    print(f"  {wt:>5} {wl:>5} {we:>7} {wa:>5} {p:>8.3f} {r:>8.3f} {f:>8.3f}{marker}")

print(f"\n  Baseline F1:  {bf:.3f}")
print(f"  Best F1:      {best_f1:.3f}  (tag={best_weights[0]}, loc={best_weights[1]}, emerg={best_weights[2]}, act={best_weights[3]})")
print(f"  Improvement:  {best_f1-bf:+.3f}")
print("=" * 70)
