# DoSync Resolver — Benchmark Results

**Registry:** 38 real devices from production hub (Raspberry Pi 5 @ 192.168.100.109)  
**Date:** May 2026 · **Iterations:** 500 per resolver · **Seed:** 42 (reproducible)

---

## Summary — real production registry (38 devices)

| Resolver | Mean | Median | p95 | p99 | Avg actions/intent |
|---|---|---|---|---|---|
| `CapabilityMatchingResolver` | 0.057ms | 0.063ms | 0.069ms | 0.072ms | 52.3 |
| `StateAwareResolver` | 0.068ms | 0.078ms | 0.085ms | 0.087ms | **33.7** |

Both resolvers operate **under 0.09ms** at p99 on the production registry.

`StateAwareResolver` eliminates **35% of redundant actions** by checking device state before including an action in the plan — same latency, fewer unnecessary device calls.

---

## Tag index — candidate reduction (v0.3)

As of v0.3, the resolver uses an inverted tag index with intersection lookup for specific tags. Instead of scoring all registered devices, it first narrows candidates via the index.

The table below shows candidate reduction at 1000 devices with a realistic tag distribution:

| Intent | Specific tags | Candidates evaluated | Reduction vs O(n) |
|---|---|---|---|
| `ensure_safety` | camera, door-lock, alarm, notification, emergency | 94 / 1000 | **−91%** |
| `children_arrived_home` | children_arrival | 25 / 1000 | **−97%** |
| `control_access` | door-lock, gate, access | 0 / 1000 | **−100%** |
| `save_energy` | blinds, smart-plug, thermostat | 527 / 1000 | −47% |
| `bedtime_routine` | blinds, smart-plug | 527 / 1000 | −47% |

**Why save_energy and bedtime_routine show lower reduction:** these intents include `smart-plug` as a specific tag, and smart-plugs are common in realistic deployments. The index reduces candidates from 1000 to 527 — still a meaningful reduction, but not as dramatic as safety-critical intents.

**Index strategy:** specific (non-generic) tags use intersection lookup — O(|result|). Generic tags (`light`, `climate`, `communication`, `sensor`, `appliance`, `display`) use union lookup. This ensures safety-critical intents with specific tags benefit most from the index.

---

## Scalability — CapabilityMatchingResolver

| Devices | Mean | p95 | p99 | Within 500ms spec |
|---|---|---|---|---|
| 10 | 0.013ms | 0.017ms | 0.019ms | ✓ |
| 50 | 0.064ms | 0.085ms | 0.088ms | ✓ |
| 100 | 0.114ms | 0.155ms | 0.158ms | ✓ |
| 500 | 0.621ms | 0.833ms | 0.931ms | ✓ |
| 1000 | 1.283ms | 1.751ms | 5.932ms | ✓ |
| 2000 | 2.847ms | 3.979ms | 9.748ms | ✓ |
| 5000 | 8.454ms | 19.226ms | 22.416ms | ✓ |

All scale points remain within the 500ms spec limit defined in `RESOLVER-SPEC-v0.2.md`.

**Note on wall-clock vs candidate reduction:** the scale benchmark measures total resolution time including scoring and action building — not just tag lookup. The candidate reduction numbers above (−91% for ensure_safety at 1000 devices) reflect the actual work eliminated by the index. The wall-clock improvement is partially offset by the overhead of set intersection operations, which becomes negligible at scale.

**Natural scaling limit:** at 5000+ devices, p95 exceeds 19ms. The dominant cost at this scale is `_build_actions_for_device` iterating actuators per candidate — not tag lookup. A pre-computed action cache per (device, intent_class) pair would address this for v0.4.

---

## Semantic overhead vs direct command

| | Mean latency |
|---|---|
| Direct command (dict lookup + ActionPlan construction) | 0.0013ms |
| Semantic resolver (full resolution over 38 devices) | 0.0566ms |
| Overhead (absolute) | **0.0552ms** |
| Overhead (relative) | 42x |

The 42x multiplier is misleading in isolation. In real deployment context:

| Operation | Typical latency |
|---|---|
| Semantic resolution (38 devices) | ~0.06ms |
| WiFi → WiZ bulb (UDP) | ~5–15ms |
| WiFi → Home Assistant (HTTP) | ~20–80ms |
| **Semantic layer as % of total** | **< 1%** |

The resolver adds less than 1% overhead to total execution time in any real deployment.

---

## Breakdown by intent class

| Intent | Mean latency | Avg actions | Notes |
|---|---|---|---|
| `ensure_safety` | 0.067ms | 74.0 | Emergency bonus + broad tag set |
| `children_arrived_home` | 0.067ms | 73.4 | Highest avg actions |
| `morning_routine` | 0.064ms | 68.4 | Broad — affects most devices |
| `save_energy` | 0.064ms | 68.3 | |
| `bedtime_routine` | 0.064ms | 67.0 | |
| `away_mode` | 0.063ms | 67.0 | |
| `set_environment` | 0.063ms | 66.5 | |
| `alert_anomaly` | 0.053ms | 41.1 | Narrower tag set |
| `remind_chore` | 0.050ms | 40.3 | |
| `monitor_health` | 0.048ms | 33.9 | |
| `notify_family` | 0.048ms | 33.3 | Communication tag only |
| `report_status` | 0.046ms | 28.7 | All devices, no tag filter |
| `control_access` | 0.043ms | 24.3 | Fastest — lock/door tags narrow the set |

---

## Methodology

- **Registry source:** 38 devices fetched via MCP from the production hub (Raspberry Pi 5). Device manifests hardcoded in the benchmark script for reproducibility without network dependency.
- **Intent sampling:** random across all 13 intent classes, 3 urgency levels, and 5 location contexts. Seed fixed at 42 for reproducibility.
- **StateAwareResolver:** cache pre-warmed at 40% fill ratio (simulates a system running for several hours).
- **Scale test:** synthetic devices with realistic tag distribution matching real-world IoT deployments (30% lights, 20% sensors, 15% smart-plugs, 10% security, 10% communication, 10% climate, 5% cameras).
- **Direct command baseline:** Python dict lookup + single `DeviceAction` + `ActionPlan` construction.

```bash
# Run from repo root (no hub required)
python3 benchmark_resolver.py

# Against a live hub
python3 benchmark_resolver.py --hub https://192.168.100.109:47200 --token <token>
```

Source: [`benchmark_resolver.py`](../benchmark_resolver.py)  
Raw results: [`benchmark_results_real.json`](../benchmark_results_real.json)
