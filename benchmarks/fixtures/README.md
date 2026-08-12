# Benchmark fixtures

Labeled inputs for the resolver recall benchmark (`tools/recall_benchmark.py`).
Everything in this directory is safe to publish: no real IP addresses, no real
room names, no data that identifies a household.

## Files

| File | What it is |
|---|---|
| `recall_registry.json` | Labeled device registry, modeled on the reference deployment. One device (`wiz-mistagged-01`) deliberately carries the bad production tags (`smart-plug`, `wiz`) instead of TAG-VOCABULARY ones — the benchmark must detect and explain that miss, so do not "fix" it. |
| `recall_ground_truth.json` | Which devices each intent should resolve to, over that registry. |
| `prod_ground_truth_operator.json` | Ground truth written from the deployment owner's **stated intent** (2026-07-12), independent of tags and resolver rules. Divergences against the resolver are true semantic findings, not tautologies. See `docs/BENCHMARK-RECALL.md`. |

## The production snapshot, anonymized

`prod_registry_anonymized.json` is the reference deployment's registry
(30 devices, July 2026 snapshot — it deliberately still contains the HA
housekeeping entities the paper's Table 3 was measured against, which were
deregistered later). Device IDs are unchanged (they were already public in
`prod_ground_truth_operator.json`); names and descriptions were genericized
to English; the export carries no addresses or adapter configuration by
design (manifest privacy). **Tags and capabilities are byte-identical to the
original** — verified by running the benchmark against both and comparing
every metric, per ground truth: identical.

`prod_ground_truth.json` is the resolver-era ground truth from the same
snapshot ("post registry cleanup, 2026-07-12"); the operator file above is
the honest one for semantic findings — see `docs/BENCHMARK-RECALL.md`.

## What does NOT live here — and why

The RAW production registry (`prod_registry.json`, with real room names)
stays on the deployment, untracked and now explicitly ignored by
`.gitignore`. Anything derived from a real deployment gets anonymized first
or it does not enter the repository.

**Rule for adding fixtures:** anything derived from a real deployment gets
anonymized first — device ids, names, addresses — or it does not enter the
repository. Results files (`recall-prod-*.json`, `benchmark_run_*.log`) are
ignored by `.gitignore`; inputs are the files that carry personal data, so
they get the stricter treatment.

## Running the benchmark against these fixtures

```bash
python3 tools/recall_benchmark.py \
  --registry benchmarks/fixtures/recall_registry.json \
  --truth benchmarks/fixtures/recall_ground_truth.json
```

Add `--policies <file>` to score every case pre- and post-policy and report
the delta, and `--min-recall <x>` to fail (exit 1) below a floor.
