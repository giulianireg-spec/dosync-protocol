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


class MetricsRegistry:
    """Holds counters and gauge callbacks; renders the exposition document."""

    def __init__(self):
        self._counters: list[Counter] = []
        self._gauges: list[tuple[str, str, Callable]] = []

    def counter(self, name: str, help_: str, labelnames: Sequence[str] = ()) -> Counter:
        c = Counter(name, help_, labelnames)
        self._counters.append(c)
        return c

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
