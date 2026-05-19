# DoSync Resolver — Benchmark Results

**Registry:** 38 real devices from production hub (Raspberry Pi 5 @ 192.168.100.109)  
**Date:** May 2026 · **Iterations:** 500 per resolver · **Seed:** 42 (reproducible)

---

## Summary — real production registry (38 devices)

| Resolver | Mean | Median | p95 | p99 | Avg actions/intent |
|---|---|---|---|---|---|
| `CapabilityMatchingResolver` | 0.053ms | 0.047ms | 0.074ms | 0.107ms | 52.3 |
| `StateAwareResolver` | 0.053ms | 0.057ms | 0.084ms | 0.109ms | **33.7** |

Both resolvers operate **under 0.11ms** at p99 on the production registry.

`StateAwareResolver` eliminates **35% of redundant actions** by checking device state before including an action in the plan — same latency, fewer unnecessary device calls.

---

## Scalability — CapabilityMatchingResolver

| Devices | Mean | p95 | p99 | Within 500ms spec |
|---|---|---|---|---|
| 10 | 0.012ms | 0.017ms | 0.038ms | ✓ |
| 50 | 0.051ms | 0.073ms | 0.087ms | ✓ |
| 100 | 0.096ms | 0.141ms | 0.196ms | ✓ |
| 500 | 0.498ms | 0.737ms | 1.486ms | ✓ |
| 1000 | 1.013ms | 1.375ms | 3.044ms | ✓ |
| 2000 | 2.460ms | 4.986ms | 8.389ms | ✓ |
| 5000 | 5.300ms | 9.129ms | 11.392ms | ✓ |

The current `CapabilityMatchingResolver` is **O(n)** — it scores every registered device on each intent resolution. All scale points remain within the 500ms spec limit defined in `RESOLVER-SPEC-v0.2.md`.

**Natural scaling limit:** at 5000+ devices, p95 exceeds 9ms. An indexed approach (pre-grouped by tag) would reduce this to near-O(1) lookups — planned for v0.3.

---

## Semantic overhead vs direct command

| | Mean latency |
|---|---|
| Direct command (dict lookup + ActionPlan construction) | 0.0013ms |
| Semantic resolver (full resolution over 38 devices) | 0.0529ms |
| Overhead (absolute) | **0.051ms** |
| Overhead (relative) | 42x |

The 42x multiplier is misleading in isolation. In real deployment context:

| Operation | Typical latency |
|---|---|
| Semantic resolution (38 devices) | ~0.05ms |
| WiFi → WiZ bulb (UDP) | ~5–15ms |
| WiFi → Home Assistant (HTTP) | ~20–80ms |
| **Semantic layer as % of total** | **< 1%** |

The resolver adds less than 1% overhead to total execution time in any real deployment.

---

## Breakdown by intent class

| Intent | Mean latency | Avg actions | Notes |
|---|---|---|---|
| `set_environment` | 0.162ms | 66.5 | High tag overlap (climate + light) |
| `morning_routine` | 0.056ms | 68.4 | Broad — affects most devices |
| `children_arrived_home` | 0.054ms | 73.4 | Highest avg actions |
| `ensure_safety` | 0.052ms | 74.0 | Emergency bonus adds scoring steps |
| `save_energy` | 0.051ms | 68.3 | |
| `alert_anomaly` | 0.043ms | 41.1 | Narrower tag set |
| `notify_family` | 0.037ms | 33.3 | Communication tag only |
| `control_access` | 0.034ms | 24.3 | Fastest — lock/door tags narrow the set |

Intents with broader tag sets resolve more devices and take slightly longer — consistent with the O(n × tag_overlap) cost model.

---

## Methodology

- **Registry source:** 38 devices fetched via MCP from the production hub (Raspberry Pi 5). Device manifests hardcoded in the benchmark script for reproducibility without network dependency.
- **Intent sampling:** random across all 13 intent classes, 3 urgency levels, and 5 location contexts. Seed fixed at 42 for reproducibility.
- **StateAwareResolver:** cache pre-warmed at 40% fill ratio (simulates a system running for several hours).
- **Direct command baseline:** Python dict lookup + single `DeviceAction` + `ActionPlan` construction.
- **Scale test:** synthetic devices with realistic tag and actuator distributions matching the production registry composition.

```bash
# Run from repo root (no hub required)
python3 benchmark_resolver.py

# Against a live hub
python3 benchmark_resolver.py --hub http://192.168.100.109:47200 --token <token>
```

Source: [`benchmark_resolver.py`](../benchmark_resolver.py)  
Raw results: [`benchmark_results_real.json`](../benchmark_results_real.json)
