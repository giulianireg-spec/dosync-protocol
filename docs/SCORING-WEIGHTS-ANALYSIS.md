# DoSync — Scoring Weights Sensitivity Analysis

**Date:** May 2026  
**Registry:** 38 real devices from production hub (Raspberry Pi 5)  
**Scenarios:** 13 intent classes from IEEE paper Table 3  
**Method:** Grid search + ±50% sensitivity variation per weight

---

## Changes applied based on this analysis

Three changes were made to the production deployment after the initial analysis:

1. **WiZ tag update** — added `smart-plug` and `emergency` tags to all 10 physical WiZ bulbs
2. **report_status clarification** — documented that intents with no resolution tags select all devices by design
3. **Actuator weight** — raised from 8 to 12 to better reflect the signal value of exact actuator matches

---

## Results before changes (original registry)

| Metric | Value |
|---|---|
| Mean Precision | 0.885 |
| Mean Recall | 0.222 |
| Mean F1 | 0.255 |

## Results after changes (updated registry + weight)

| Metric | Before | After | Δ |
|---|---|---|---|
| Mean Precision | 0.885 | 0.987 | +11% |
| Mean Recall | 0.222 | 0.475 | **+114%** |
| Mean F1 | 0.255 | 0.493 | **+93%** |

### Per-scenario breakdown (after changes)

| Scenario | Precision | Recall | F1 | vs before |
|---|---|---|---|---|
| `ensure_safety` | 1.00 | 0.93 | 0.96 | +0.61 |
| `alert_anomaly` | 1.00 | 0.00 | 0.00 | 0.00 |
| `control_access` | 1.00 | 1.00 | 1.00 | 0.00 |
| `notify_family` | 1.00 | 0.00 | 0.00 | 0.00 |
| `save_energy` | 1.00 | 0.92 | 0.96 | +0.81 |
| `bedtime_routine` | 0.91 | 0.91 | 0.91 | **+0.91** |
| `morning_routine` | 1.00 | 0.00 | 0.00 | 0.00 |
| `children_arrived_home` | 1.00 | 1.00 | 1.00 | 0.00 |
| `away_mode` | 0.92 | 0.92 | 0.92 | +0.78 |
| `set_environment` | 1.00 | 0.00 | 0.00 | 0.00 |
| `report_status` | 1.00 | 0.00 | 0.00 | 0.00 |
| `monitor_health` | 1.00 | 0.50 | 0.67 | 0.00 |
| `remind_chore` | 1.00 | 0.00 | 0.00 | 0.00 |

---

## Sensitivity analysis (post-changes)

| Weight | Variation | Precision | Recall | F1 | ΔF1 |
|---|---|---|---|---|---|
| tag (10) | ±50% | 0.987 | 0.475 | 0.493 | 0.000 |
| loc (15) | ±50% | 0.987 | 0.475 | 0.493 | 0.000 |
| emerg (30) | ±50% | 0.987 | 0.475 | 0.493 | 0.000 |
| act (12) | ±50% | 0.987 | 0.475 | 0.493 | 0.000 |

**ΔF1 = 0.000 across all variations — before and after changes.** The scoring formula is insensitive to weight values. This is the key finding.

---

## Grid search — best weight combination (post-changes)

288 combinations searched. All produce identical F1 = 0.493. The baseline weights are as good as any other combination.

---

## Key findings

### 1. Weights are not the bottleneck

ΔF1 = 0 for all weight variations, before and after changes. The scoring formula controls ranking of candidates, not candidate selection. Changing weights cannot fix recall — only tag configuration can.

### 2. Tag configuration drove the entire F1 improvement

Adding two tags (`smart-plug`, `emergency`) to 10 WiZ devices produced +114% recall and +93% F1. No code changes were required for this improvement.

### 3. Remaining F1=0.00 scenarios have structural causes

| Scenario | Root cause | Fix |
|---|---|---|
| `alert_anomaly`, `notify_family`, `remind_chore` | TVs have `display`/`communication` tags but no matching actuators in resolution map | Add `notify` actuator to TV manifests |
| `morning_routine`, `set_environment` | No thermostat or blinds devices in registry | Add thermostat/blinds devices |
| `report_status` | Intent has no resolution tags by design | All-device query — requires special handling |

### 4. Actuator weight (8→12)

The change from 8 to 12 is correct semantically but produces no measurable F1 change at current scale. It improves ranking quality when multiple devices are candidates for the same actuator type — relevant at 500+ device deployments.

### 5. Weights encode correct semantic priorities

- **Emergency bonus (30) >> tag overlap (10):** 3:1 ratio ensures safety-critical devices always rank first
- **Location bonus (15) > tag overlap (10):** spatial relevance is stronger than generic tag match
- **Actuator match (12) > tag overlap (10):** after this change, exact capability match outweighs generic tag match

---

## Methodology

```bash
python3 scoring_sensitivity.py
```

- Ground truth: expert knowledge of 38-device production registry
- 13 intent classes from IEEE paper Table 3
- Independent rater validation planned (paper §6)

---

*DoSync Protocol v0.3 · Apache 2.0*
