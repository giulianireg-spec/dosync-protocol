# DoSync — Technical debt backlog (living)

*Items surfaced during audits and deferred deliberately. Each is verifiable against the repo.*

## POL-1 — Deployment policy config loader (panel decision 2026-07-12) · effort M
The reference hub hard-codes example policies in `server.py` (concrete quiet hours,
concrete device IDs in NeverAfterHoursPolicy / RequireConfirmationPolicy). Per the
2026-07-12 panel (see DoSync-Panel-Frontera-Deployment), device preferences are
DEPLOYMENT configuration, not protocol or reference-hub code. Work:
  (a) define a deployment policy config format (JSON),
  (b) add a loader so the hub reads policies from a deployment file,
  (c) move the example policies out of server.py into an example deployment file
      (e.g. examples/policies.deployment.json).
This also enables the benchmark's future POST-policy mode and the shareable-config
future the project wants to enable (without curating that catalog).
Validation: hub loads policies from a file; server.py contains no deployment-specific
hours or device IDs; benchmark can run pre- and post-policy.

## QA-benchmark-postpolicy — Post-policy benchmark mode · effort S (after POL-1)
`tools/recall_benchmark.py` measures the resolver PRE-policy (a protocol property,
recall 1.0). Once POL-1 lands, add a mode that applies the PolicyEngine so the tool
can also report the configured-deployment precision (an operator property). Both
numbers are honest; they measure different layers (see docs/BENCHMARK-RECALL.md).
