# DoSync Resolver — Benchmark Results

**Registry:** 38 real devices from production hub (Raspberry Pi 5 @ 192.168.100.109)  
**Date:** May 2026 · **Iterations:** 500 per resolver · **Seed:** 42 (reproducible)

---

## Summary — real production registry (38 devices)

| Resolver | Mean | Median | p95 | p99 | Avg actions/intent |
|---|---|---|---|---|---|
| `CapabilityMatchingResolver` | 0.081ms | 0.090ms | 0.099ms | 0.104ms | 52.3 |
| `StateAwareResolver` | 0.082ms | 0.080ms | 0.120ms | 0.124ms | **33.7** |

Both resolvers operate **under 0.13ms** at p99 on the production registry.

`StateAwareResolver` eliminates **35% of redundant actions** by checking device state before including an action in the plan — same latency, fewer unnecessary device calls.

**Note on index overhead at small scale:** the inverted tag index (v0.3) adds a small overhead at 38 devices due to set union operations. This overhead is negligible — 0.08ms vs the previous 0.05ms — and disappears at scale where candidate reduction dominates.

---

## Tag index — candidate reduction (v0.3)

As of v0.3, the resolver uses an inverted tag index with union lookup. Instead of scoring all registered devices, it first narrows candidates via the index, then adds all `emergency_capable` devices for emergency intents.

The table below shows candidate reduction at 1000 devices with a realistic tag distribution:

| Intent | Candidates evaluated | vs 1000 total | Reduction |
|---|---|---|---|
| `ensure_safety` | 94 / 1000 | **−91%** |
| `children_arrived_home` | 25 / 1000 | **−97%** |
| `control_access` | 0 / 1000 | **−100%** |
| `save_energy` | 527 / 1000 | −47% |
| `bedtime_routine` | 527 / 1000 | −47% |

**Why save_energy and bedtime_routine show lower reduction:** these intents include `smart-plug` as a tag, and smart-plugs are common in realistic deployments. The index still reduces candidates from 1000 to 527.

**Emergency handling:** emergency intents always include `emergency_capable` devices as candidates regardless of tag overlap. This ensures physical safety devices (lights, alarms) are never excluded by the tag filter.

**Index strategy:** union lookup — devices with ANY of the intent's resolution tags are candidates. The `find_by_required_tags` intersection method exists as a utility but is not used in `resolve()` — semantic intents need devices relevant to ANY context, not ALL contexts simultaneously.

---

## Scalability — CapabilityMatchingResolver

| Devices | Mean | p95 | p99 | Within 500ms spec |
|---|---|---|---|---|
| 10 | 0.013ms | 0.017ms | 0.018ms | ✓ |
| 50 | 0.065ms | 0.087ms | 0.092ms | ✓ |
| 100 | 0.116ms | 0.157ms | 0.160ms | ✓ |
| 500 | 0.630ms | 0.842ms | 0.950ms | ✓ |
| 1000 | 1.305ms | 1.789ms | 5.952ms | ✓ |
| 2000 | 2.918ms | 4.066ms | 9.899ms | ✓ |
| 5000 | 8.635ms | 19.251ms | 22.736ms | ✓ |

All scale points remain within the 500ms spec limit defined in `RESOLVER-SPEC-v0.3.md`.

**Natural scaling limit:** at 5000+ devices, p95 exceeds 19ms. The dominant cost at this scale is `_build_actions_for_device` iterating actuators per candidate — not tag lookup. A pre-computed action cache per (device, intent_class) pair would address this for v0.4.

---

## Semantic overhead vs direct command

| | Mean latency |
|---|---|
| Direct command (dict lookup + ActionPlan construction) | 0.0014ms |
| Capability-based resolver (full resolution over 38 devices) | 0.0811ms |
| Overhead (absolute) | **0.0798ms** |
| Overhead (relative) | 60x |

The 60x multiplier is misleading in isolation. In real deployment context:

| Operation | Typical latency |
|---|---|
| Capability-based resolution (38 devices) | ~0.08ms |
| WiFi → WiZ bulb (UDP) | ~5–15ms |
| WiFi → Home Assistant (HTTP) | ~20–80ms |
| **Semantic layer as % of total** | **< 1%** |

The resolver adds less than 1% overhead to total execution time in any real deployment.

---

## Breakdown by intent class

| Intent | Mean latency | Avg actions | Notes |
|---|---|---|---|
| `children_arrived_home` | 0.096ms | 73.4 | Highest avg actions |
| `ensure_safety` | 0.095ms | 74.0 | Emergency bonus + broad tag set |
| `morning_routine` | 0.092ms | 68.4 | Broad — affects most devices |
| `save_energy` | 0.091ms | 68.3 | |
| `bedtime_routine` | 0.091ms | 67.0 | |
| `away_mode` | 0.090ms | 67.0 | |
| `set_environment` | 0.089ms | 66.5 | |
| `alert_anomaly` | 0.077ms | 41.1 | Narrower tag set |
| `remind_chore` | 0.072ms | 40.3 | |
| `notify_family` | 0.069ms | 33.3 | Communication tag only |
| `monitor_health` | 0.069ms | 33.9 | |
| `report_status` | 0.066ms | 28.7 | All devices, no tag filter |
| `control_access` | 0.062ms | 24.3 | Fastest — lock/door tags narrow the set |

---

## Methodology

- **Registry source:** 38 devices fetched via MCP from the production hub (Raspberry Pi 5). Device manifests hardcoded in the benchmark script for reproducibility without network dependency.
- **Intent sampling:** random across all 13 intent classes, 3 urgency levels, and 5 location contexts. Seed fixed at 42 for reproducibility.
- **StateAwareResolver:** cache pre-warmed at 40% fill ratio (simulates a system running for several hours).
- **Scale test:** synthetic devices with realistic tag distribution matching real-world IoT deployments (30% lights, 20% sensors, 15% smart-plugs, 10% security, 10% communication, 10% climate, 5% cameras).
- **Direct command baseline:** Python dict lookup + single `DeviceAction` + `ActionPlan` construction.

```bash
# Run from repo root (no hub required)
python3 benchmarks/benchmark_resolver.py

# Against a live hub
python3 benchmarks/benchmark_resolver.py --hub https://192.168.100.109:47200 --token <token>
```

Source: [`benchmarks/benchmark_resolver.py`](../benchmarks/benchmark_resolver.py)  
Raw results: [`benchmarks/benchmark_results_real.json`](../benchmarks/benchmark_results_real.json)
