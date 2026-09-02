"""
Phase 1.1 — what does the resolver lose, and gain, without the tag hard-filter?

The measured failures on the industrial and clinical corpora were all one
failure: a device that declares the right actuator is excluded because it lacks
a tag the capability already implies.

    lock-or3-door: declares matching actuators ['lock', 'unlock']
                   but none of the resolution tags ['lock']

This tool re-scores the same corpora under three regimes and prints the
difference. It touches no production code — it monkey-patches the scorer for the
duration of a run, so the number can be trusted to come from the real resolver
rather than a reimplementation of it.

    CURRENT      what ships today: specific tags are a hard gate
    CAPABILITY   the gate is a declared actuator; tags only add points
    NO_GATE      no gate at all — the upper bound on recall, and the
                 lower bound on precision

Usage:
    python3 tools/counterfactual_resolver.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dosync.hub import CapabilityMatchingResolver, DoSyncHub  # noqa: E402
from dosync.models import Intent, IntentClass, Urgency  # noqa: E402

# Reused rather than reimplemented: this loader already accepts both the fixture
# shape and a raw production export, and a second copy would drift from it.
from recall_benchmark import _register_domain_intents, load_registry  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_stats: dict[str, int] = {}

CORPORA = {
    "industrial": (REPO / "benchmarks/corpus/industrial_registry.json",
                   REPO / "benchmarks/corpus/industrial_ground_truth.json"),
    "clinical":   (REPO / "benchmarks/corpus/clinical_registry.json",
                   REPO / "benchmarks/corpus/clinical_ground_truth.json"),
    "recall":     (REPO / "benchmarks/fixtures/recall_registry.json",
                   REPO / "benchmarks/fixtures/recall_ground_truth.json"),
}


def _load_cases(path: Path):
    """Ground-truth files keep their cases under `cases`, alongside a
    `_provenance` block that declares `independent_raters: 0`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) and "cases" in data else data


def _patch(regime: str):
    """Replace the gate, leave everything else untouched.

    The point of patching rather than rewriting is that every other part of the
    score — weights, location, emergency, actuator matching — stays exactly as
    it ships. Only the gate changes, so any difference in the numbers is
    attributable to the gate and nothing else.
    """
    original = CapabilityMatchingResolver._score_breakdown
    _stats["hard_filtered"] = 0

    def patched(self, device, intent, resolution):
        breakdown = original(self, device, intent, resolution)
        if regime == "current":
            # Counted here, after the regime branch, so the number belongs to
            # the regime being measured. Counting before it recorded what the
            # ORIGINAL resolver filtered in all three regimes — harmless only
            # because the count is printed for `current` alone, which is a
            # defect saved by accident rather than by design.
            #
            # Counted rather than inferred: NO_GATE scoring the same as CURRENT
            # admits two explanations — the filter never fires, or the devices
            # it drops score too low to be picked anyway. The first report chose
            # one without measuring. It fires twice on industrial; the second
            # explanation is the right one.
            if breakdown.hard_filtered:
                _stats["hard_filtered"] += 1
            return breakdown

        # `resolution` carries `tags` and `actuators`. It carries no `sensors`,
        # because no intent class declares which sensors answer it — a gap the
        # tag `sensor` on `alert_anomaly` is currently papering over.
        #
        # An earlier version of this gate read `resolution["sensors"]` anyway.
        # The key never existed, the branch never ran, and the tool reported a
        # capability gate "including sensors" that only ever looked at
        # actuators. It went unnoticed because the number it produced was
        # plausible. Hence the assertion below: a branch that cannot execute
        # must fail the measurement, not pass it quietly.
        assert "sensors" not in resolution, (
            "resolution now carries sensors — this gate needs updating rather "
            "than silently ignoring them")

        target_actuators = set(resolution.get("actuators", []))
        device_actuators = {a.type for a in device.actuators}
        has_capability = bool(target_actuators & device_actuators)

        if regime == "capability":
            # The gate becomes "does it declare an actuator this intent needs".
            # A device with no declared actuator for the intent is still out —
            # that is not tag curation, it is the absence of a capability.
            breakdown.hard_filtered = not has_capability if target_actuators else False
        elif regime == "no_gate":
            breakdown.hard_filtered = False
        return breakdown

    CapabilityMatchingResolver._score_breakdown = patched
    return original


def _evaluate(registry_path, truth_doc, truth, regime: str):
    original = _patch(regime)
    try:
        # `:memory:` is not a detail — it seeds the real universal intent
        # classes. Without it the hub has no idea what `ensure_safety` resolves
        # to, and every case falls through to a status query.
        hub = DoSyncHub(db_path=":memory:")
        load_registry(registry_path, hub)
        # Without this the domain intents are unknown to the hub and every case
        # falls through to the default status-query behaviour — which is how an
        # earlier run of this tool produced three identical regimes.
        _register_domain_intents(hub, truth_doc)
        resolver = hub.resolver
        rows = []
        for case in truth:
            # `IntentClass`, not a bare string: the resolver looks the class up
            # to find which tags and actuators an intent resolves to, and a
            # plain string silently resolves to nothing — which is what made an
            # earlier version of this tool report identical numbers for three
            # different regimes.
            intent = Intent(
                intent_id=f"cf-{case['intent']}",
                intent=IntentClass(case["intent"]),
                urgency=Urgency(case.get("urgency", "info")),
                context=case.get("context", {}) or {},
            )
            plan = resolver.resolve(intent)
            selected = {a.device_id for a in plan.actions}
            expected = set(case["expected"])

            tp = len(selected & expected)
            precision = tp / len(selected) if selected else (1.0 if not expected else 0.0)
            recall = tp / len(expected) if expected else 1.0
            f1 = (2 * precision * recall / (precision + recall)
                  if (precision + recall) else 0.0)
            rows.append({
                "intent": case["intent"],
                "precision": precision, "recall": recall, "f1": f1,
                "missed": sorted(expected - selected),
                "extra": sorted(selected - expected),
            })
        return rows
    finally:
        CapabilityMatchingResolver._score_breakdown = original


def main() -> None:
    regimes = ("current", "capability", "no_gate")
    labels = {"current": "CURRENT", "capability": "CAPABILITY", "no_gate": "NO GATE"}

    print(f"\n{'corpus':<12} {'regime':<12} {'prec':>6} {'recall':>7} {'F1':>6}")
    print("-" * 48)

    summary = {}
    for name, (reg_path, truth_path) in CORPORA.items():
        if not reg_path.exists():
            print(f"{name}: fixture not found, skipped")
            continue
        truth_doc = json.loads(truth_path.read_text(encoding="utf-8"))
        truth = _load_cases(truth_path)
        for regime in regimes:
            rows = _evaluate(reg_path, truth_doc, truth, regime)
            n = len(rows) or 1
            p = sum(r["precision"] for r in rows) / n
            r_ = sum(r["recall"] for r in rows) / n
            f = sum(r["f1"] for r in rows) / n
            summary[(name, regime)] = (p, r_, f, rows)
            filtered = _stats.get("hard_filtered", 0)
            note = f"  (hard-filtered {filtered}x)" if regime == "current" else ""
            print(f"{name:<12} {labels[regime]:<12} {p:6.2f} {r_:7.2f} {f:6.2f}"
                  f"  n={len(rows)}{note}")
        print("-" * 48)

    # What changed, case by case. The aggregate hides which cases moved and in
    # which direction, and that is the part a design decision needs.
    print("\nWhat the capability gate changes, case by case:\n")
    for name in CORPORA:
        if (name, "current") not in summary:
            continue
        cur = {r["intent"]: r for r in summary[(name, "current")][3]}
        cap = {r["intent"]: r for r in summary[(name, "capability")][3]}
        for intent, before in cur.items():
            after = cap[intent]
            if abs(before["f1"] - after["f1"]) < 0.005:
                continue
            arrow = "improves" if after["f1"] > before["f1"] else "REGRESSES"
            print(f"  {name}/{intent}: F1 {before['f1']:.2f} → {after['f1']:.2f}  {arrow}")
            recovered = set(before["missed"]) - set(after["missed"])
            added = set(after["extra"]) - set(before["extra"])
            if recovered:
                print(f"      now found: {sorted(recovered)}")
            if added:
                print(f"      now wrongly included: {sorted(added)}")
    print()


if __name__ == "__main__":
    main()
