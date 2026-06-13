"""
DoSync Multi-Hub — Phase A: Hub Monitor (assisted failover coordination)

This module implements the standby-side coordination logic for multi-hub §11.
It is a pure state machine: it consumes observations (a peer's heartbeat result,
a liveness-probe result, a device-count) and decides a state and whether to
propose promotion. It performs NO network I/O itself — the caller supplies the
observations. This keeps the decision logic deterministic and testable without
two machines.

Design: docs MULTIHUB-PHASE-A-DESIGN.md. Validated by architecture panel:
operator-assisted failover, never automatic; distinguishes "primary dead" from
"I lost the network"; refuses destructive promotion when state diverges.

Delivery guarantee (declared honestly): failover of ROLE, not of STATE. Without
Phase B replication, a promoted standby does not hold the primary's devices —
the monitor detects this divergence and flags promotion as destructive.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MonitorState(str, Enum):
    """The four observable states of a standby's monitor."""
    WATCHING = "WATCHING"        # primary healthy, standby passive (normal)
    PRIMARY_DOWN = "PRIMARY_DOWN"  # primary unreachable, our network is fine → propose
    UNCERTAIN = "UNCERTAIN"      # primary unreachable AND our network degraded → hold
    # (a non-running monitor is simply absent; the primary treats that as normal)


@dataclass
class HeartbeatObservation:
    """One observation cycle the caller feeds to the monitor.

    primary_reachable: did the peer's /v1/hub/heartbeat respond healthy?
    network_reachable: did the independent liveness probe (gateway) succeed?
    primary_devices:   device count reported by the primary when last reachable
                       (None if never seen).
    """
    primary_reachable: bool
    network_reachable: bool
    primary_devices: Optional[int] = None


@dataclass
class PromotionProposal:
    """The monitor's recommendation when the primary appears down."""
    proposed: bool                 # should the operator be offered promotion?
    destructive: bool = False      # would promotion lose devices? (state divergence)
    local_devices: int = 0
    primary_devices_last_known: Optional[int] = None
    reason: str = ""


class HubMonitor:
    """Standby-side monitor. Inert on a primary (never instantiated there).

    Usage (the caller does the network I/O and feeds observations):
        m = HubMonitor(failure_threshold=3, local_device_count=23)
        m.observe(HeartbeatObservation(primary_reachable=True,  network_reachable=True))
        ...
        if m.state is MonitorState.PRIMARY_DOWN:
            proposal = m.promotion_proposal()
    """

    def __init__(self, failure_threshold: int = 3, local_device_count: int = 0):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.local_device_count = local_device_count
        self.state: MonitorState = MonitorState.WATCHING
        self.consecutive_misses: int = 0
        self.primary_devices_last_known: Optional[int] = None

    def observe(self, obs: HeartbeatObservation) -> MonitorState:
        """Feed one observation cycle. Returns the resulting state."""
        if obs.primary_reachable:
            # Primary is healthy: reset and remember its device count.
            self.consecutive_misses = 0
            if obs.primary_devices is not None:
                self.primary_devices_last_known = obs.primary_devices
            self.state = MonitorState.WATCHING
            return self.state

        # Primary did not respond.
        self.consecutive_misses += 1
        if self.consecutive_misses < self.failure_threshold:
            # Not enough misses yet — stay WATCHING (transient blip).
            self.state = MonitorState.WATCHING
            return self.state

        # Threshold reached. Distinguish "primary dead" from "I lost the network".
        if obs.network_reachable:
            # Our network is fine, primary is genuinely unreachable → propose.
            self.state = MonitorState.PRIMARY_DOWN
        else:
            # We may be partitioned — cannot judge the primary. Hold, never promote.
            self.state = MonitorState.UNCERTAIN
        return self.state

    def promotion_proposal(self) -> PromotionProposal:
        """The recommendation for the operator. Only meaningful in PRIMARY_DOWN."""
        if self.state is not MonitorState.PRIMARY_DOWN:
            return PromotionProposal(
                proposed=False,
                reason=f"Not proposing: monitor state is {self.state.value}",
            )

        known = self.primary_devices_last_known
        # Destructive if the primary had strictly more devices than we hold:
        # promoting would serve a registry missing those devices (no Phase B yet).
        destructive = known is not None and known > self.local_device_count
        if destructive:
            reason = (
                f"Promotion is DESTRUCTIVE: primary last had {known} devices, "
                f"this standby holds {self.local_device_count}. Without replication "
                f"(Phase B), promoting loses {known - self.local_device_count} devices."
            )
        else:
            reason = "Primary appears down and local state is not divergent."
        return PromotionProposal(
            proposed=True,
            destructive=destructive,
            local_devices=self.local_device_count,
            primary_devices_last_known=known,
            reason=reason,
        )

    def snapshot(self) -> dict:
        """Serializable view for the GET /v1/hub/peers endpoint."""
        known = self.primary_devices_last_known
        divergent = known is not None and known != self.local_device_count
        proposal = self.promotion_proposal()
        return {
            "monitor_state": self.state.value,
            "consecutive_misses": self.consecutive_misses,
            "primary_devices_last_known": known,
            "local_devices": self.local_device_count,
            "state_divergent": divergent,
            "promotion_safe": proposal.proposed and not proposal.destructive,
        }
