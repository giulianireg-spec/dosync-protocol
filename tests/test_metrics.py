"""Unit tests for dosync/metrics.py — zero-dep Prometheus exposition."""
import importlib

import dosync.metrics as metrics_mod


def _fresh_registry():
    return metrics_mod.MetricsRegistry()


def test_counter_no_labels_renders_zero_before_first_inc():
    reg = _fresh_registry()
    reg.counter("t_total", "help text")
    out = reg.render()
    assert "# TYPE t_total counter" in out
    assert "\nt_total 0\n" in out or out.endswith("t_total 0\n")


def test_counter_with_labels_increments_and_renders():
    reg = _fresh_registry()
    c = reg.counter("t_intents_total", "h", ("intent_class", "outcome"))
    c.inc({"intent_class": "ensure_safety", "outcome": "accepted"})
    c.inc({"intent_class": "ensure_safety", "outcome": "accepted"})
    c.inc({"intent_class": "notify", "outcome": "rejected"})
    out = reg.render()
    assert 't_intents_total{intent_class="ensure_safety",outcome="accepted"} 2' in out
    assert 't_intents_total{intent_class="notify",outcome="rejected"} 1' in out


def test_label_escaping():
    reg = _fresh_registry()
    c = reg.counter("t_esc_total", "h", ("v",))
    c.inc({"v": 'has "quotes" and \\ and \nnewline'})
    out = reg.render()
    assert r'\"quotes\"' in out
    assert r"\\" in out
    assert r"\n" in out
    assert "\nnewline" not in out  # raw newline must not leak into the sample line


def test_gauge_scalar_and_labeled():
    reg = _fresh_registry()
    reg.gauge_func("t_up", "h", lambda: 1)
    reg.gauge_func("t_rate", "h", lambda: [({"device_id": "d1"}, 0.5), ({"device_id": "d2"}, 1.0)])
    out = reg.render()
    assert "t_up 1" in out
    assert 't_rate{device_id="d1"} 0.5' in out
    assert 't_rate{device_id="d2"} 1' in out


def test_broken_gauge_never_breaks_the_endpoint():
    reg = _fresh_registry()
    def boom():
        raise RuntimeError("broken gauge")
    reg.gauge_func("t_bad", "h", boom)
    reg.gauge_func("t_ok", "h", lambda: 7)
    out = reg.render()          # must not raise
    assert "t_ok 7" in out
    assert "t_bad{" not in out  # no samples for the broken one


def test_module_counters_exist_with_expected_names():
    # The DoSync counters the server instruments must exist under these names —
    # they are the names promised by the spec's recommended-metrics appendix.
    importlib.reload(metrics_mod)
    rendered = metrics_mod.REGISTRY.render()
    for name in (
        "dosync_intents_total",
        "dosync_intent_executions_total",
        "dosync_intent_actions_total",
        "dosync_policy_decisions_total",
        "dosync_emergency_intents_total",
        "dosync_device_preemptions_total",
    ):
        assert name in rendered


# ── Histogram (REL-1 phase 2) ────────────────────────────────────────────────

def test_histogram_bucket_cumulative_and_sum_count():
    reg = metrics_mod.MetricsRegistry()
    h = reg.histogram("t_latency_seconds", "h", (0.01, 0.1, 1.0))
    for v in (0.005, 0.05, 0.5, 5.0):   # falls in b0, b1, b2, +Inf
        h.observe(v)
    out = reg.render()
    # cumulative buckets: le=0.01 ->1, le=0.1 ->2, le=1 ->3, +Inf ->4
    assert 't_latency_seconds_bucket{le="0.01"} 1' in out
    assert 't_latency_seconds_bucket{le="0.1"} 2' in out
    assert 't_latency_seconds_bucket{le="1"} 3' in out
    assert 't_latency_seconds_bucket{le="+Inf"} 4' in out
    assert "t_latency_seconds_count 4" in out
    assert "t_latency_seconds_sum 5.555" in out


def test_histogram_with_label():
    reg = metrics_mod.MetricsRegistry()
    h = reg.histogram("t_exec_seconds", "h", (0.1, 1.0), ("result",))
    h.observe(0.05, {"result": "success"})
    h.observe(0.5, {"result": "failed"})
    out = reg.render()
    assert 't_exec_seconds_bucket{result="success",le="0.1"} 1' in out
    assert 't_exec_seconds_bucket{result="failed",le="0.1"} 0' in out
    assert 't_exec_seconds_bucket{result="failed",le="1"} 1' in out
    assert 't_exec_seconds_count{result="success"} 1' in out
    assert 't_exec_seconds_count{result="failed"} 1' in out


def test_latency_histograms_registered():
    importlib.reload(metrics_mod)
    out = metrics_mod.REGISTRY.render()
    assert "dosync_intent_resolution_seconds_bucket" in out or \
           "# TYPE dosync_intent_resolution_seconds histogram" in out
    assert "# TYPE dosync_action_execution_seconds histogram" in out
