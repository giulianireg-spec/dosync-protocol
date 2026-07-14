"""
DoSync — minimal metrics (Prometheus text exposition format, zero dependencies).
================================================================================

Design (see docs panel 2026-07-07, REL-1):

  * Metrics are an OPERATIONAL feature of the reference implementation — they are
    NOT part of the normative protocol. A hub without /metrics is still conforming.
    The spec carries a non-normative "Recommended metrics" appendix so independent
    implementations can expose comparable series under the same names.

  * Phase 1 is counters + gauges only, hand-rolled (no prometheus_client dep).
    Latency histograms are phase 2 — if/when they land, prometheus_client is the
    right tool (correct histogram semantics are easy to get wrong by hand).

  * CARDINALITY RULE: label values must be bounded sets (intent classes, urgency
    levels, outcome enums). Never put unbounded user input in a label — rejected
    intents are counted under intent_class="_invalid" for exactly this reason.
    device_id appears ONLY in the low-volume health gauge, never in hot counters.

  * Counters live in memory and reset on restart — that is normal Prometheus
    behavior (rate()/increase() handle counter resets). Gauges are derived at
    scrape time from the registry/DB via cheap callbacks; nothing walks the full
    audit log per scrape.
"""

from __future__ import annotations

import threading
from typing import Callable, Iterable, Sequence


def _escape_label(v: str) -> str:
    """Escape a label value per the Prometheus text format."""
    return str(v).replace("\\", r"\\").replace('"', r"\"").replace("\n", r"\n")


def _num(v) -> str:
    """Render a number without trailing .0 noise for integers."""
    f = float(v)
    return str(int(f)) if f.is_integer() else repr(f)


class Counter:
    """A monotonically increasing counter with optional labels. Thread-safe."""

    def __init__(self, name: str, help_: str, labelnames: Sequence[str] = ()):
        self.name = name
        self.help = help_
        self.labelnames = tuple(labelnames)
        self._values: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def inc(self, labels: dict | None = None, amount: float = 1) -> None:
        key = tuple(str((labels or {}).get(n, "")) for n in self.labelnames)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def samples(self) -> dict[tuple, float]:
        with self._lock:
            return dict(self._values)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        samples = self.samples()
        if not samples:
            # Expose an explicit zero so the series exists before first increment
            # (only possible without labels; labeled series appear on first inc).
            if not self.labelnames:
                lines.append(f"{self.name} 0")
            return lines
        for key in sorted(samples):
            if self.labelnames:
                labelstr = ",".join(
                    f'{n}="{_escape_label(v)}"' for n, v in zip(self.labelnames, key)
                )
                lines.append(f"{self.name}{{{labelstr}}} {_num(samples[key])}")
            else:
                lines.append(f"{self.name} {_num(samples[key])}")
        return lines


class Histogram:
    """Cumulative histogram in Prometheus text format. Thread-safe, zero-dep.

    Emits the standard trio: _bucket{le=...} (cumulative counts, including +Inf),
    _sum, and _count. Buckets are upper bounds in seconds. We hand-roll this
    rather than take a prometheus_client dependency (DoSync keeps its dep
    surface minimal); the exposition format is covered exactly by
    tests/test_metrics.py so the "histograms are fiddly by hand" risk is pinned.
    """

    def __init__(self, name: str, help_: str, buckets: Sequence[float],
                 labelnames: Sequence[str] = ()):
        self.name = name
        self.help = help_
        self.labelnames = tuple(labelnames)
        self._buckets = tuple(sorted(buckets))
        # per label-key: [counts-per-bucket (len = nbuckets, no +Inf), sum, count]
        self._data: dict[tuple, list] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, labels: dict | None = None) -> None:
        key = tuple(str((labels or {}).get(n, "")) for n in self.labelnames)
        with self._lock:
            rec = self._data.get(key)
            if rec is None:
                rec = [[0] * len(self._buckets), 0.0, 0]
                self._data[key] = rec
            counts, _sum, _count = rec
            # Record in the single tightest bucket; render() makes it cumulative.
            for i, ub in enumerate(self._buckets):
                if value <= ub:
                    counts[i] += 1
                    break
            rec[1] = _sum + value
            rec[2] = _count + 1

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            data = {k: [list(v[0]), v[1], v[2]] for k, v in self._data.items()}
        for key in sorted(data):
            counts, _sum, _count = data[key]
            base_labels = list(zip(self.labelnames, key))
            cumulative = 0
            for i, ub in enumerate(self._buckets):
                cumulative += counts[i]
                le = _num(ub)
                pairs = base_labels + [("le", le)]
                labelstr = ",".join(f'{n}="{_escape_label(v)}"' for n, v in pairs)
                lines.append(f"{self.name}_bucket{{{labelstr}}} {cumulative}")
            # +Inf bucket == total count
            inf_pairs = base_labels + [("le", "+Inf")]
            inf_labelstr = ",".join(f'{n}="{_escape_label(v)}"' for n, v in inf_pairs)
            lines.append(f"{self.name}_bucket{{{inf_labelstr}}} {_count}")
            suffix = ("{" + ",".join(f'{n}="{_escape_label(v)}"' for n, v in base_labels) + "}") if base_labels else ""
            lines.append(f"{self.name}_sum{suffix} {_num(_sum)}")
            lines.append(f"{self.name}_count{suffix} {_count}")
        return lines


class MetricsRegistry:
    """Holds counters and gauge callbacks; renders the exposition document."""

    def __init__(self):
        self._counters: list[Counter] = []
        self._histograms: list[Histogram] = []
        self._gauges: list[tuple[str, str, Callable]] = []

    def counter(self, name: str, help_: str, labelnames: Sequence[str] = ()) -> Counter:
        c = Counter(name, help_, labelnames)
        self._counters.append(c)
        return c

    def histogram(self, name: str, help_: str, buckets: Sequence[float],
                  labelnames: Sequence[str] = ()) -> Histogram:
        h = Histogram(name, help_, buckets, labelnames)
        self._histograms.append(h)
        return h

    def gauge_func(self, name: str, help_: str, fn: Callable) -> None:
        """Register a gauge computed at scrape time.

        `fn` returns either a scalar, or an iterable of (labels_dict, value)
        for labeled gauges. Exceptions in `fn` skip the gauge for that scrape
        rather than failing the whole endpoint — a broken gauge must never
        take observability down with it.
        """
        self._gauges.append((name, help_, fn))

    def render(self) -> str:
        lines: list[str] = []
        for c in self._counters:
            lines.extend(c.render())
        for h in self._histograms:
            lines.extend(h.render())
        for name, help_, fn in self._gauges:
            try:
                out = fn()
            except Exception:
                continue
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} gauge")
            if isinstance(out, (int, float, bool)):
                lines.append(f"{name} {_num(out)}")
            else:
                for labels, value in out:
                    labelstr = ",".join(
                        f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items())
                    )
                    lines.append(f"{name}{{{labelstr}}} {_num(value)}")
        return "\n".join(lines) + "\n"


# ── DoSync registry and counters ──────────────────────────────────────────────
# Gauges are registered by the server at startup (they close over live state).

REGISTRY = MetricsRegistry()

intents_total = REGISTRY.counter(
    "dosync_intents_total",
    "Intents received by the hub, by class, urgency and acceptance outcome",
    ("intent_class", "urgency", "outcome"),   # outcome: accepted | rejected
)
intent_executions_total = REGISTRY.counter(
    "dosync_intent_executions_total",
    "Completed intent executions by outcome",
    ("outcome",),                              # success | partial | failed
)
intent_actions_total = REGISTRY.counter(
    "dosync_intent_actions_total",
    "Individual device actions dispatched by intents, by result",
    ("result",),                               # success | failed | superseded
)
policy_decisions_total = REGISTRY.counter(
    "dosync_policy_decisions_total",
    "Policy engine decisions",
    ("decision",),                             # allow | block | confirm | modify
)
emergency_intents_total = REGISTRY.counter(
    "dosync_emergency_intents_total",
    "Intents accepted with emergency urgency",
)
device_preemptions_total = REGISTRY.counter(
    "dosync_device_preemptions_total",
    "Lower-urgency actions superseded because the device was claimed by an emergency",
)

# Latency histograms (REL-1 phase 2). Buckets in seconds, tuned for a hub that
# targets sub-second intent handling: fine-grained below 100ms, headroom to 5s.
_LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

intent_resolution_seconds = REGISTRY.histogram(
    "dosync_intent_resolution_seconds",
    "Time for the resolver to turn an intent into an action plan",
    _LATENCY_BUCKETS,
)
action_execution_seconds = REGISTRY.histogram(
    "dosync_action_execution_seconds",
    "Time to execute a single device action, by adapter result",
    _LATENCY_BUCKETS,
    ("result",),   # success | failed — bounded, safe as a label
)
