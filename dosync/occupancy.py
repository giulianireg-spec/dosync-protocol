"""The occupancy engine.

The seventh of the eleven responsibilities extracted from `hub.py`: inferring
whether the home is occupied by aggregating presence signals from several
context providers, weighting them, and expiring the stale ones. A self-contained
state machine that had been sharing a file with the orchestrator that reads it.

Re-exported from `hub` so every existing import keeps working unchanged.
"""
from __future__ import annotations

import logging
import time

from .models import OccupancyState, PresenceSignal

log = logging.getLogger("dosync.hub")


class OccupancyEngine:
    """
    Infers home occupancy state by aggregating signals from multiple
    context providers. Never relies on a single source — combines and weights them.

    Supported signals and their default weights:
      Phone GPS outside perimeter     → absence, weight 0.9
      Phone WiFi disconnected         → absence, weight 0.7
      No PIR motion for 30+ min       → absence, weight 0.4
      Smartwatch GPS outside perimeter → absence, weight 0.8
      Smart TV off                    → absence, weight 0.2
    """

    def __init__(self):
        self._signals: list[PresenceSignal] = []
        self._signal_ttl_seconds = 300      # signals expire after 5 minutes

    def update(self, signal: PresenceSignal) -> None:
        """Register or update a presence signal."""
        # Replace any previous signal from the same device
        self._signals = [
            s for s in self._signals
            if not (s.device_id == signal.device_id and
                    s.signal_type == signal.signal_type)
        ]
        self._signals.append(signal)
        log.info(
            "Presence signal: %s / %s → present=%s (confidence=%.2f)",
            signal.device_id, signal.signal_type.value,
            signal.present, signal.confidence,
        )

    def _active_signals(self) -> list[PresenceSignal]:
        """Filter out expired signals."""
        cutoff = time.time() - self._signal_ttl_seconds
        return [s for s in self._signals if s.timestamp >= cutoff]

    def get_occupancy(self) -> OccupancyState:
        """
        Calcula el estado de ocupacion actual.
        Returns occupied=True when weighted presence confidence >= 0.5.
        """
        signals = self._active_signals()
        if not signals:
            # No signals = unknown state; default to occupied for safety
            return OccupancyState(
                occupied=True,
                confidence=0.0,
                members_home=[],
                signals_used=0,
            )

        # Calculate weighted presence confidence
        total_weight = sum(s.confidence for s in signals)
        presence_weight = sum(
            s.confidence for s in signals if s.present
        )

        confidence_present = presence_weight / total_weight if total_weight > 0 else 0.5
        occupied = confidence_present >= 0.5

        members_home = list({
            s.member_id for s in signals
            if s.present and s.member_id
        })

        return OccupancyState(
            occupied=occupied,
            confidence=abs(confidence_present - 0.5) * 2,  # 0=incertidumbre, 1=certeza
            members_home=members_home,
            signals_used=len(signals),
        )

    def all_signals(self) -> list[dict]:
        return [
            {
                "device_id":   s.device_id,
                "signal_type": s.signal_type.value,
                "present":     s.present,
                "confidence":  s.confidence,
                "member_id":   s.member_id,
                "timestamp":   s.timestamp,
                "age_seconds": round(time.time() - s.timestamp, 1),
            }
            for s in self._active_signals()
        ]
