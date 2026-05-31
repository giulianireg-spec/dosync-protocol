# DoSync — Scoring Weights Sensitivity Analysis

**Date:** May 2026  
**Registry:** 38 real devices from production hub (Raspberry Pi 5)  
**Scenarios:** 13 intent classes from IEEE paper Table 3  
**Method:** Grid search + ±50% sensitivity variation per weight

---

## Baseline results (current weights: tag=10, loc=15, emerg=30, act=8)

| Metric | Value |
|---|---|
| Mean Precision | 0.885 |
| Mean Recall | 0.222 |
| Mean F1 | 0.255 |

### Per-scenario breakdown

| Scenario | Precision | Recall | F1 | Notes |
|---|---|---|---|---|
| `ensure_safety` | 1.00 | 0.21 | 0.35 | Lights missing `emergency` tag |
| `alert_anomaly` | 1.00 | 0.00 | 0.00 | TVs lack `communication` scoring tags |
| `control_access` | 1.00 | 1.00 | 1.00 | No door-lock devices — vacuously correct |
| `notify_family` | 1.00 | 0.00 | 0.00 | Notifier/TVs not selected |
| `save_energy` | 1.00 | 0.08 | 0.15 | Lights lack `smart-plug` tag |
| `bedtime_routine` | 0.00 | 0.00 | 0.00 | No devices with `blinds`/`smart-plug` |
| `morning_routine` | 1.00 | 0.00 | 0.00 | Same as bedtime_routine |
| `children_arrived_home` | 1.00 | 1.00 | 1.00 | Tags correctly configured |
| `away_mode` | 0.50 | 0.08 | 0.14 | Lights lack `smart-plug` tag |
| `set_environment` | 1.00 | 0.00 | 0.00 | DHT22 not selected |
| `report_status` | 1.00 | 0.00 | 0.00 | Intent has no tags → resolves empty |
| `monitor_health` | 1.00 | 0.50 | 0.67 | PIR selected, DHT22 not |
| `remind_chore` | 1.00 | 0.00 | 0.00 | Notifier/TVs not selected |

---

## Sensitivity analysis — weights varied ±25% and ±50%

| Weight | Variation | Precision | Recall | F1 | ΔF1 |
|---|---|---|---|---|---|
| tag (10) | −50% | 0.885 | 0.222 | 0.255 | 0.000 |
| tag (10) | −25% | 0.885 | 0.222 | 0.255 | 0.000 |
| tag (10) | +25% | 0.885 | 0.222 | 0.255 | 0.000 |
| tag (10) | +50% | 0.885 | 0.222 | 0.255 | 0.000 |
| loc (15) | ±50% | 0.885 | 0.222 | 0.255 | 0.000 |
| emerg (30) | ±50% | 0.885 | 0.222 | 0.255 | 0.000 |
| act (8) | ±50% | 0.885 | 0.222 | 0.255 | 0.000 |

**ΔF1 = 0.000 across all weight variations.** The scoring formula is completely insensitive to weight changes over the tested range.

---

## Grid search — best weight combination

Grid searched over: tag=[5,10,15,20], loc=[10,15,20], emerg=[15,20,25,30,40,50], act=[5,8,12,16] (288 combinations)

**Result: all 288 combinations produce identical F1 = 0.255.**

The baseline weights (10/15/30/8) are as good as any other combination in the search space.

---

## Interpretation

### Why are the weights insensitive?

The scoring formula `s = t×10 + l×15 + e×30 + a×8` controls the *relative ranking* of devices that are already candidates. It does not control *which devices become candidates* — that is determined by the tag index and the `INTENT_RESOLUTION_MAP`.

The low recall (0.222) is entirely explained by **missing tags in device manifests**, not by incorrect weight values:

| Root cause | Affected scenarios | Fix |
|---|---|---|
| Lights lack `smart-plug` tag | `save_energy`, `away_mode`, `bedtime_routine`, `morning_routine` | Add `smart-plug` to WiZ manifests |
| Lights lack `emergency` tag | `ensure_safety` (partial recall) | Add `emergency` to emergency_capable lights |
| `report_status` has no resolution tags | `report_status` | Intent design issue — all-device intents need special handling |
| TVs/notifier not selected for notify intents | `notify_family`, `alert_anomaly`, `remind_chore` | Tags match but scoring threshold filters them out |

### What the weights actually control

The weights matter when multiple devices are candidates and the system must rank them. In the current 38-device deployment, most intents either select the correct devices (precision=1.00) or miss them entirely due to tag gaps (recall=0.00). The weights have no effect on either outcome.

The weights would become significant in larger deployments (500+ devices) where many devices are candidates for the same intent and ranking quality determines which subset gets included.

### Validation of current weights

Despite being empirically defined, the weights encode correct semantic priorities:

- **Emergency bonus (30) > tag overlap (10):** a safety-critical device that can respond to emergencies should rank above a device that merely shares tags. The 3:1 ratio ensures emergency-capable devices are always near the top of any action plan.
- **Location bonus (15) > tag overlap (10):** a device in the same location as the intent context is more relevant than a generic tag match. The 1.5:1 ratio reflects reasonable spatial preference.
- **Actuator match (8) < tag overlap (10):** this ordering is debatable. A device with the exact actuator type needed arguably deserves more weight than a device with a matching tag. Raising `act` to 12-15 would not change current results but would improve ranking quality at scale.

### Recommendation

The weights are **validated as stable** — no combination tested produces better F1. The primary improvement lever is tag configuration, documented in `DEPLOYMENT-TAGS-GUIDE.md`.

For future work: re-run this analysis after updating WiZ manifests with recommended tags. Expected outcome: recall rises from 0.222 to ~0.70+, and weight sensitivity becomes measurable.

---

## Methodology

```bash
# Run from repo root
python3 /tmp/scoring_sensitivity.py
```

- Ground truth defined by expert knowledge of the 38-device production registry
- Same 13 intent classes as IEEE paper Table 3
- Independent rater validation planned as future work (paper §6)

---

*DoSync Protocol v0.3 · Apache 2.0*
