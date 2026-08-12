# Multi-domain evaluation corpus

Synthetic registries and ground truths that make the domain-agnostic claim
**measurable** instead of asserted. Before this corpus existed, the benchmark
never registered domain intent classes — `line_shutdown` and
`prepare_operating_room` fell through to the default and scored as status
queries, so the claim could not be evaluated with the project's own tooling.

Synthetic is a requirement, not a shortcut: a corpus must live in the
repository for a published table to be reproducible by someone who is not the
author. (WF-IoT reviewer 3 asked for exactly this.)

## Files

| File | Domain |
|---|---|
| `clinical_registry.json` / `clinical_ground_truth.json` | Clinical — operating-room preparation, patient-area devices |
| `industrial_registry.json` / `industrial_ground_truth.json` | Industrial — production line, shutdown and emergency-stop paths |

Each ground truth declares the `intent_classes` its scenarios need; the
benchmark registers them before evaluating (see
`tools/recall_benchmark.py`), so a corpus is self-contained.

The home domain lives in `../fixtures/` (`recall_registry.json`), which
predates this directory.

## Reading the numbers honestly

First multi-domain measurement (2026-08-11): **home 1.00 · industrial 0.64 ·
clinical 0.61.** The resolver scores perfectly on the domain its tag
vocabulary was tuned against and drops to ~0.62 on domains it has not seen.

Every miss is attributed to a cause — `intent_not_registered`, `vocabulary`,
`not_in_registry`, `resolution` — because a recall figure that mixes tooling
gaps with genuine resolution failures measures nothing. Of twelve misses in
the first multi-domain run, ten were not the resolver.

## Running

```bash
python3 tools/recall_benchmark.py \
  --registry benchmarks/corpus/industrial_registry.json \
  --truth benchmarks/corpus/industrial_ground_truth.json
```
