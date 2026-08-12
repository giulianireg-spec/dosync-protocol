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

## What does NOT live here — and why

The reference deployment's real registry (`prod_registry.json`, ~30 devices
with room names and LAN addresses) and its per-run ground truths stay on the
deployment, untracked. They contain personal data and must never be committed.

An **anonymized** replacement fixture is pending work (roadmap, August 2026):
it would give the paper a second home-domain registry that reviewers can
actually reproduce. Until it exists, the reproducible home-domain evidence is
`recall_registry.json`.

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
