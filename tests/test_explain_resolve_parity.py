"""explain() and resolve() must evaluate the same devices.

The project's first advertised property is that the explanation IS the
decision: "the score it reports is the same value the resolver decided with —
one computation, not a narration of one." v9 (2026-07-21) made that true of the
scoring FORMULA and left it false of the CANDIDATE SET: resolve() selected
through `registry.find_by_tags()`, explain() iterated `registry.active()`.

A device matching only on ACTUATOR scores 12 in the breakdown and was never a
candidate in resolve. So explain reported it as INCLUDED while the resolver
structurally could not act on it. Measured on three registries before the fix:
2 in the reference deployment (ensure_safety — an Ambilight and a TV screen
switch), 2 industrial, and 5 clinical — among them an operating-room
ventilation unit and a patient-facing display, both listed as participating in
an emergency that never touches them. An operator auditing "what does my system
do in an emergency?" planned around devices that would not move.

The benchmark could not have caught this: it measures resolve() against ground
truth and never compares the two paths. So the property is asserted directly
here — the mechanism, not a symptom another mechanism also prevents.
"""
import pytest

from dosync.hub import CapabilityMatchingResolver, CapabilityRegistry
from dosync.models import (ActuatorSpec, CapabilityManifest, DeviceCategory,
                           Intent, IntentClass, SensorSpec, Urgency)


def _device(device_id, tags, actuator_types, *, emergency=False, sensors=()):
    return CapabilityManifest(
        device_id=device_id,
        device_name=device_id,
        manufacturer="test",
        model="test",
        firmware="1.0",
        category=DeviceCategory.ACTUATOR,
        tags=list(tags),
        emergency_capable=emergency,
        sensors=[SensorSpec(id=s, type="boolean", description=s) for s in sensors],
        actuators=[ActuatorSpec(id=t, type=t, description=t) for t in actuator_types],
    )


RESOLUTION = {"tags": ["emergency", "alarm"],
              "actuators": ["alarm", "turn_on", "notify"]}


@pytest.fixture
def resolver():
    """A registry with the shape that exposed the bug: a device whose actuators
    fit the resolution and whose tags do not (the clinical OR ventilation unit),
    alongside one that matches properly.

    The resolution is pinned rather than read from a database: this test is
    about the candidate SET, and a DB-seeded resolution would make it also
    depend on the seed — two reasons to fail, one of them unrelated. The
    contract between the seed and the spec is pinned separately in
    tests/test_universal_intent_contract.py.
    """
    registry = CapabilityRegistry()
    registry.register(_device("siren-01", ["emergency", "alarm"], ["alarm"],
                              emergency=True))
    registry.register(_device("hvac-or3", ["clinical", "or-3", "ventilation"],
                              ["turn_on", "set_speed"]))
    registry.register(_device("display-ward2", ["clinical", "display"], ["notify"]))
    r = CapabilityMatchingResolver(registry)
    r._get_resolution = lambda intent: dict(RESOLUTION)
    return r


def test_explain_includes_only_devices_resolve_evaluates(resolver):
    """THE property. Everything reported as included must be a candidate."""
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.ALERT, context={})
    candidates = {d.device_id for d in resolver._candidates(intent, RESOLUTION)}
    explanation = resolver.explain(intent)
    included = {d["device_id"] for d in explanation.get("included", [])}
    ghosts = included - candidates
    assert not ghosts, (
        f"explain() reports {sorted(ghosts)} as included; resolve() never "
        "evaluates them — the explanation is not the decision")


def test_actuator_only_device_is_excluded_not_included(resolver):
    """The exact regression: fitting actuators, no fitting tag."""
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.ALERT, context={})
    explanation = resolver.explain(intent)
    included = {d["device_id"] for d in explanation.get("included", [])}
    excluded = {d["device_id"] for d in explanation.get("excluded", [])}
    assert "hvac-or3" not in included, \
        "a device the resolver cannot act on is reported as included"
    assert "hvac-or3" in excluded, \
        "the device disappeared entirely — E2 requires it be reported, not dropped"


def test_exclusion_names_the_tag_that_would_change_it(resolver):
    """E2: the exclusion must be actionable, not just correct.

    Dropping these devices would lose real information — 'your ventilation unit
    could act and your tags do not let it' is what an operator needs.
    """
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.ALERT, context={})
    row = next(d for d in resolver.explain(intent)["excluded"]
               if d["device_id"] == "hvac-or3")
    assert row["actuators_fit_resolution"] == ["turn_on"], \
        "the fitting actuator is not reported"
    assert "turn_on" in row["reason"] and "emergency" in row["reason"], \
        f"exclusion reason is not actionable: {row['reason']}"


def test_device_with_no_fitting_actuator_says_so_plainly(resolver):
    """Not every exclusion is interesting; only conflate them deliberately."""
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.ALERT, context={})
    row = next(d for d in resolver.explain(intent)["excluded"]
               if d["device_id"] == "display-ward2")
    # notify IS in the resolution actuators, so this device DOES fit — assert the
    # honest outcome rather than a convenient one.
    assert row["actuators_fit_resolution"] == ["notify"]


def test_emergency_capable_devices_are_candidates_in_both_paths(resolver):
    """The emergency extension moved into _candidates; it must apply to explain.

    Before, force-inclusion lived only inside resolve(). Sharing the candidate
    set without carrying it would have fixed one divergence by creating another,
    in the direction that matters most — emergencies.
    """
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.EMERGENCY, context={})
    resolution = {"tags": ["nonexistent-tag"], "actuators": ["alarm"]}
    candidates = {d.device_id for d in resolver._candidates(intent, resolution)}
    assert "siren-01" in candidates, \
        "an emergency_capable device was dropped by the tag filter in an emergency"
    included = {d["device_id"] for d in resolver.explain(intent).get("included", [])}
    assert "siren-01" in included


def test_counts_are_consistent_with_the_lists(resolver):
    """devices_evaluated must not count devices that were not evaluated."""
    intent = Intent(intent=IntentClass("ensure_safety"),
                    urgency=Urgency.ALERT, context={})
    ex = resolver.explain(intent)
    assert ex["devices_included"] == len(ex["included"])
    assert ex["devices_excluded"] == len(ex["excluded"])
    assert ex["devices_evaluated"] == len(ex["included"]) + len(ex["excluded"])


# ── Quarantine ───────────────────────────────────────────────────────────────

def test_quarantined_device_is_never_a_candidate():
    """A withdrawn device must not be planned into anything, emergencies least of all.

    `active()` filters quarantined devices and its docstring is explicit that
    such a device "must not be planned into an emergency, because the operator
    already believes it is gone". But resolution went through `find_by_tags()`
    and `find_emergency_capable()`, neither of which filtered — so the contract
    held only for callers that happened to use `active()`.

    Found on the reference deployment, not here: a quarantined emergency_capable
    light was being planned into ensure_safety. No fixture in any of the four
    corpora contains a quarantined device, which is why the benchmark could not
    have caught it — it surfaced because explain() and resolve() began reporting
    the same set and the device counts stopped matching between intents.
    """
    from dosync.hub import QUARANTINE_KEY

    registry = CapabilityRegistry()
    live = _device("siren-live", ["emergency", "alarm"], ["alarm"], emergency=True)
    gone = _device("siren-withdrawn", ["emergency", "alarm"], ["alarm"], emergency=True)
    gone.adapter_config = {QUARANTINE_KEY: "declarative file deleted"}
    registry.register(live)
    registry.register(gone)
    resolver = CapabilityMatchingResolver(registry)
    resolver._get_resolution = lambda intent: dict(RESOLUTION)

    for urgency in (Urgency.ALERT, Urgency.EMERGENCY):
        intent = Intent(intent=IntentClass("ensure_safety"), urgency=urgency, context={})
        candidates = {d.device_id for d in resolver._candidates(intent, RESOLUTION)}
        assert "siren-live" in candidates
        assert "siren-withdrawn" not in candidates, (
            f"a quarantined device is a candidate under {urgency.value} — "
            "emergency force-inclusion must beat the tag filter, not quarantine")
        planned = {a.device_id for a in resolver.resolve(intent).actions}
        assert "siren-withdrawn" not in planned, \
            "a device the operator withdrew was planned into an intent"


# ── Found on the reference deployment, 2026-08-12 ────────────────────────────

def test_quarantined_device_is_not_a_candidate_even_in_an_emergency():
    """A device the operator withdrew must not be planned into an emergency.

    `active()` says so in its own docstring; `find_by_tags()` and
    `find_emergency_capable()` are raw indexes that filtered nothing, so
    resolve() planned quarantined devices anyway — the contract was documented
    in one method and broken in two.

    Found by counting: the deployment's explain reported 21 devices for
    ensure_safety and 20 for every other intent. The extra one was
    `luz-declarativa`, quarantined after its declarative file was removed and
    still entering every emergency — through force-inclusion, carrying no
    emergency tag, so no tag audit would ever have shown it.
    """
    from dosync.hub import QUARANTINE_KEY, is_quarantined

    registry = CapabilityRegistry()
    live = _device("siren-live", ["emergency", "alarm"], ["alarm"], emergency=True)
    withdrawn = _device("siren-withdrawn", ["emergency", "alarm"], ["alarm"],
                        emergency=True)
    withdrawn.adapter_config = {QUARANTINE_KEY: True,
                                "quarantine_reason": "declarative file removed"}
    registry.register(live)
    registry.register(withdrawn)
    assert is_quarantined(withdrawn), "fixture does not actually quarantine"

    r = CapabilityMatchingResolver(registry)
    resolution = {"tags": ["emergency", "alarm"], "actuators": ["alarm"]}
    r._get_resolution = lambda intent: dict(resolution)

    for urgency in (Urgency.ALERT, Urgency.EMERGENCY):
        intent = Intent(intent=IntentClass("ensure_safety"),
                        urgency=urgency, context={})
        ids = {d.device_id for d in r._candidates(intent, resolution)}
        assert "siren-live" in ids
        assert "siren-withdrawn" not in ids, (
            f"a quarantined device is a candidate at urgency={urgency.value} — "
            "force-inclusion beats the tag filter, not the operator's withdrawal")
        acted = {a.device_id for a in r.resolve(intent).actions}
        assert "siren-withdrawn" not in acted
