"""
Phase 1.2 — head-to-head baselines.

Reviewer 2 of IEEE WF-IoT 2026:

    The comparison the argument rests on is never run. The claim is that a
    deterministic resolver can replace LLM-in-path approaches, but no LLM-based
    resolver is evaluated on the same 15 scenarios.

This runs three baselines against the same corpora, the same ground truth and
the same registries the DoSync resolver is measured on. Every baseline sees
exactly what the resolver sees — the manifest as published — so a difference in
score is a difference in method, not in information.

    KEYWORD      the simplest thing that could work: match intent words against
                 device name, tags and actuator types. No weights, no ranking.
    UNWEIGHTED   the resolver's own signals with every weight set to 1. Isolates
                 how much the tuned weights are worth.
    RULEBOOK     an explicit device-per-intent mapping written by hand, in the
                 spirit of a Home Assistant automation. The thing DoSync claims
                 to make unnecessary.
    DOSYNC       the shipped resolver.

An LLM baseline is deliberately absent: it needs an API key and network access,
and a result that cannot be reproduced offline is not a baseline. The interface
for one is `resolve_llm` below, unimplemented on purpose.

Usage:
    python3 tools/resolver_baselines.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dosync.hub import DoSyncHub  # noqa: E402
from dosync.models import Intent, IntentClass, Urgency  # noqa: E402
from recall_benchmark import _register_domain_intents, load_registry  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORPORA = {
    "industrial": (REPO / "benchmarks/corpus/industrial_registry.json",
                   REPO / "benchmarks/corpus/industrial_ground_truth.json"),
    "clinical":   (REPO / "benchmarks/corpus/clinical_registry.json",
                   REPO / "benchmarks/corpus/clinical_ground_truth.json"),
    "recall":     (REPO / "benchmarks/fixtures/recall_registry.json",
                   REPO / "benchmarks/fixtures/recall_ground_truth.json"),
}


def _words(text: str) -> set[str]:
    return {w for w in text.lower().replace("_", " ").replace("-", " ").split() if len(w) > 2}


def resolve_keyword(hub, intent_name: str, urgency: str,
                    use_tags: bool = True) -> set[str]:
    """Match intent words against whatever the device says about itself.

    `use_tags=True` reads the curated tags alongside the name and the declared
    capabilities. That is the floor a weighted resolver has to clear, but it is
    NOT a comparison between curation and its absence: both sides read the same
    curated field, differently.

    `use_tags=False` is that second comparison — word overlap against only what
    the device declares objectively. The gap between the two is what the tags
    are worth to a method that does no weighting at all.
    """
    target = _words(intent_name)
    ic = hub.db.get_intent_class(intent_name) or {}
    target |= _words(ic.get("description", ""))
    if use_tags:
        target |= {t.lower() for t in (ic.get("resolution_tags") or [])}
    target |= {a.lower() for a in (ic.get("resolution_actuators") or [])}

    chosen = set()
    for d in hub.registry.all():
        vocab = _words(d.device_name)
        if use_tags:
            vocab |= {t.lower() for t in d.tags}
        vocab |= {a.type.lower() for a in d.actuators}
        vocab |= {s.type.lower() for s in d.sensors}
        if target & vocab:
            chosen.add(d.device_id)
    return chosen


def resolve_unweighted(hub, intent_name: str, urgency: str) -> set[str]:
    """The resolver's signals, every weight equal to 1.

    Answers a question the sensitivity analysis could not: is the tuning worth
    anything, or would any positive weights do?
    """
    ic = hub.db.get_intent_class(intent_name) or {}
    want_tags = {t.lower() for t in (ic.get("resolution_tags") or [])}
    want_acts = {a.lower() for a in (ic.get("resolution_actuators") or [])}

    scored = []
    for d in hub.registry.all():
        tags = {t.lower() for t in d.tags}
        acts = {a.type.lower() for a in d.actuators}
        score = len(want_tags & tags) + len(want_acts & acts)
        if urgency == "emergency" and d.emergency_capable:
            score += 1
        if score:
            scored.append((score, d.device_id))
    if not scored:
        return set()
    top = max(s for s, _ in scored)
    return {did for s, did in scored if s == top}


def resolve_rulebook(hub, intent_name: str, urgency: str, rules: dict) -> set[str]:
    """An explicit mapping, written by hand — a Home Assistant automation in
    spirit. Devices are named one by one for each intent.

    It should win on the corpus it was written for. The question is what it
    costs: the rulebook is regenerated per deployment by a person, which is the
    burden DoSync claims to remove.
    """
    return {d for d in rules.get(intent_name, []) if hub.registry.get(d)}


def resolve_llm(hub, intent_name: str, urgency: str) -> set[str]:
    """Not implemented, deliberately.

    An LLM resolver needs a key and a network, and a number that cannot be
    reproduced offline is not a baseline — it is an anecdote. The signature is
    here so that adding one later does not require reshaping this file.
    """
    raise NotImplementedError(
        "An LLM baseline requires network access and an API key. "
        "Left unimplemented so this tool stays reproducible offline.")


def _evaluate(chooser, hub, cases) -> tuple[float, float, float]:
    ps, rs, fs = [], [], []
    for case in cases:
        selected = chooser(hub, case["intent"], case.get("urgency", "info"))
        expected = set(case["expected"])
        tp = len(selected & expected)
        p = tp / len(selected) if selected else (1.0 if not expected else 0.0)
        r = tp / len(expected) if expected else 1.0
        f = 2 * p * r / (p + r) if (p + r) else 0.0
        ps.append(p); rs.append(r); fs.append(f)
    n = len(cases) or 1
    return sum(ps) / n, sum(rs) / n, sum(fs) / n


def _dosync(hub, intent_name: str, urgency: str) -> set[str]:
    plan = hub.resolver.resolve(Intent(
        intent_id=f"bl-{intent_name}",
        intent=IntentClass(intent_name),
        urgency=Urgency(urgency),
        context={},
    ))
    return {a.device_id for a in plan.actions}


def main() -> None:
    # Stated before the numbers rather than after them. A caveat that only the
    # reader who gets to the bottom sees is a caveat that did not happen.
    print("\n  The ground truth and these baselines were written by the same")
    print("  person. If the expected sets were chosen by asking which devices")
    print("  sound appropriate, word overlap agrees with them by construction —")
    print("  so what follows says as much about the corpus as about the resolver.")
    print(f"\n{'corpus':<16} {'method':<16} {'prec':>6} {'recall':>7} {'F1':>6}")
    print("-" * 66)

    for name, (reg_path, truth_path) in CORPORA.items():
        if not reg_path.exists():
            continue
        truth_doc = json.loads(truth_path.read_text(encoding="utf-8"))
        cases = truth_doc.get("cases", truth_doc)

        hub = DoSyncHub(db_path=":memory:")
        load_registry(reg_path, hub)
        _register_domain_intents(hub, truth_doc)

        # The rulebook is handed the answers: it is the upper bound a person
        # reaches by naming devices one at a time, and the cost of that is the
        # point of the comparison.
        rules = {c["intent"]: c["expected"] for c in cases}

        methods = [
            ("KEYWORD", resolve_keyword),
            ("KEYWORD-notags", lambda h, i, u: resolve_keyword(h, i, u, use_tags=False)),
            ("UNWEIGHTED", resolve_unweighted),
            ("DOSYNC", _dosync),
        ]
        for label, fn in methods:
            p, r, f = _evaluate(fn, hub, cases)
            print(f"{name:<16} {label:<16} {p:6.2f} {r:7.2f} {f:6.2f}  n={len(cases)}")

        # The rulebook is deliberately NOT in the table above. It is handed the
        # ground truth, so its 1.00 is arithmetic rather than a measurement, and
        # printing it in the same column invites the reader to compare a method
        # against an answer key. What compares is its cost: the number of
        # device-per-intent decisions a person has to make, per deployment.
        entries = sum(len(v) for v in rules.values())
        print(f"{'':16} {'RULEBOOK':<16} {'perfect by construction':>21}"
              f"  — {entries} hand-written device assignments"
              f" across {len(rules)} intents")
        print("-" * 66)

    print()
    print("n=5 per corpus. A move of 0.05 in F1 is one case in five: these are")
    print("directional numbers, not measurements with an interval.")
    print()
    print("KEYWORD reads the curated tags; KEYWORD-notags reads only what the")
    print("device declares objectively. The gap between them is what curation")
    print("is worth to a method that does no weighting at all.")
    print()
    print()


if __name__ == "__main__":
    main()
