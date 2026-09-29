"""The tools behind the paper's numbers run against the live code.

tools/resolver_latency.py and tools/capability_ablation.py produce figures that
are published; a tool that silently broke, or that measured a copy of the
resolver (as benchmarks/benchmark_resolver.py does), would publish numbers
nobody can reproduce.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_latency_tool_measures_the_live_resolver():
    sys.path.insert(0, str(REPO))
    from tools.resolver_latency import measure
    report = measure(sizes=[12], runs=2, warmup=0)
    assert report["resolver"].startswith("hub.resolver (live)")
    restricted = [r for r in report["results"] if r["intent"] == "light_on_presence"]
    assert restricted and 0 < restricted[0]["actions"] < 12, \
        "the room-restricted intent should act on part of the registry, not all of it"


def test_the_ablation_tool_runs():
    out = subprocess.run([sys.executable, "tools/capability_ablation.py"], cwd=REPO,
                         capture_output=True, text=True, timeout=120,
                         env={"PYTHONPATH": str(REPO), "DOSYNC_AUTH": "false", "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr[-500:]
    assert "capability-only  location" in out.stdout


def test_a_corpus_class_can_restrict_by_location_and_declare_sensors():
    """Every corpus had an empty context and no class could declare a location
    role, so the protocol 0.5 rule that a location restricts went unmeasured."""
    sys.path.insert(0, str(REPO))
    from dosync.hub import DoSyncHub
    from tools.recall_benchmark import _register_domain_intents
    hub = DoSyncHub(db_path=":memory:")
    _register_domain_intents(hub, {"intent_classes": {"probe_restricting": {
        "urgency": "info", "target_tags": ["light"], "target_actuators": ["turn_on"],
        "target_sensors": ["motion"], "location_role": "informs"}}})
    row = hub.db.get_intent_class("probe_restricting")
    assert row["location_role"] == "informs" and row["resolution_sensors"] == ["motion"]
