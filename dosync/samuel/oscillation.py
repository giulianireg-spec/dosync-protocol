"""No oscillation (SAMUEL G4, second half; DoSync protocol §6.8 rule 10).

A device is not reversed across an opposite pair more than an allowed number of
times within a window, across separate intents. A single intent cannot send both
halves of a pair (proposals.validate_proposals and the resolver see to that);
this guard covers the case a single intent cannot see: "turn it off and on five
times", fired as five intents. A declared emergency action is exempt.

The guard keeps the only state the kernel has -- the recent history of each
(device, pair) -- in the instance its owner holds; it reads the clock only
through the ``now`` it is given. Moved here unchanged from
DoSyncHub._apply_oscillation_guard (2026-10-11).
"""
from __future__ import annotations

import os
from typing import Callable, Iterable

from ..models import OPPOSITE_ACTIONS


class OscillationGuard:
    """Rolling-window guard against flapping a device across an opposite pair."""

    def __init__(self, window_s: float | None = None, max_reversals: int | None = None):
        self._window_s = window_s
        self._max_reversals = max_reversals
        #: (device_id, sorted pair) -> [(time, action)] within the window
        self.history: dict = {}

    def settings(self) -> tuple[float, int]:
        """(window seconds, allowed reversals). From the constructor if given,
        else DOSYNC_OSCILLATION_WINDOW (60) and DOSYNC_OSCILLATION_MAX_REVERSALS
        (1: a correction is allowed, flashing is not). A window <= 0 disables it."""
        if self._window_s is not None and self._max_reversals is not None:
            return float(self._window_s), max(0, int(self._max_reversals))
        try:
            window_s = float(os.environ.get("DOSYNC_OSCILLATION_WINDOW", "60"))
            max_rev = max(0, int(os.environ.get("DOSYNC_OSCILLATION_MAX_REVERSALS", "1")))
        except ValueError:
            window_s, max_rev = 60.0, 1
        return window_s, max_rev

    def apply(self, actions: Iterable, get_device: Callable[[str], object],
              now: float) -> tuple[list, list]:
        """Return (kept actions, dropped refusals). A dropped action never runs,
        so it does not enter the history."""
        window_s, max_rev = self.settings()
        actions = list(actions)
        if window_s <= 0:
            return actions, []
        kept, dropped = [], []
        for a in actions:
            pair = next((p for p in OPPOSITE_ACTIONS if a.action in p), None)
            device = get_device(a.device_id)
            declared = {e["action"] for e in (getattr(device, "emergency_actions", None) or [])}
            if pair is None or a.action in declared:
                kept.append(a)
                continue
            key = (a.device_id, tuple(sorted(pair)))
            hist = [(t, act) for (t, act) in self.history.get(key, []) if now - t <= window_s]
            changes = sum(1 for i in range(1, len(hist)) if hist[i][1] != hist[i - 1][1])
            reverses = bool(hist) and hist[-1][1] != a.action
            if reverses and changes >= max_rev:
                dropped.append({"device_id": a.device_id, "action": a.action, "reason": "oscillation"})
                self.history[key] = hist
            else:
                hist.append((now, a.action))
                self.history[key] = hist
                kept.append(a)
        return kept, dropped
