"""
DoSync Hub Monitor Validation (multi-hub Phase A)

Tests the standby monitor's state machine and promotion logic — the pure
decision core, no network. Covers the three panel scenarios (steady, primary
down, partition) plus the state-divergence safeguard that refuses destructive
promotion.

TESTING PHILOSOPHY: the monitor performs NO network I/O — it consumes
observations the caller supplies. So the state machine is fully testable here
without two machines. The network-dependent parts (actually polling the peer,
the live partition behaviour) are validated separately on the real Pi+Mac
topology per MULTIHUB-PHASE-A-DESIGN.md — that integration is the operator's
hands-on test, not a unit test.

Run: python3 -m pytest tests/test_hub_monitor.py -v
  or: python3 tests/test_hub_monitor.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.hub_monitor import HubMonitor, HeartbeatObservation, MonitorState


def obs(primary, network, devices=None):
    return HeartbeatObservation(primary_reachable=primary, network_reachable=network, primary_devices=devices)


# ── Scenario 1: steady state (WATCHING) ───────────────────────────────────────

def test_starts_watching():
    m = HubMonitor()
    assert m.state is MonitorState.WATCHING


def test_healthy_primary_stays_watching():
    m = HubMonitor(failure_threshold=3)
    for _ in range(5):
        m.observe(obs(primary=True, network=True, devices=34))
    assert m.state is MonitorState.WATCHING
    assert m.consecutive_misses == 0
    assert not m.promotion_proposal().proposed


def test_healthy_primary_records_device_count():
    m = HubMonitor()
    m.observe(obs(primary=True, network=True, devices=34))
    assert m.primary_devices_last_known == 34


# ── Misses below threshold stay WATCHING (transient blip) ──────────────────────

def test_misses_below_threshold_stay_watching():
    m = HubMonitor(failure_threshold=3)
    m.observe(obs(primary=True, network=True, devices=34))
    m.observe(obs(primary=False, network=True))   # miss 1
    assert m.state is MonitorState.WATCHING
    m.observe(obs(primary=False, network=True))   # miss 2
    assert m.state is MonitorState.WATCHING
    assert m.consecutive_misses == 2


def test_recovery_resets_misses():
    m = HubMonitor(failure_threshold=3)
    m.observe(obs(primary=False, network=True))
    m.observe(obs(primary=False, network=True))
    m.observe(obs(primary=True, network=True))    # primary came back
    assert m.consecutive_misses == 0
    assert m.state is MonitorState.WATCHING


# ── Scenario 2: real primary failure (PRIMARY_DOWN) ────────────────────────────

def test_threshold_misses_with_network_ok_proposes():
    m = HubMonitor(failure_threshold=3, local_device_count=34)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=True))
    assert m.state is MonitorState.PRIMARY_DOWN
    proposal = m.promotion_proposal()
    assert proposal.proposed is True


def test_primary_down_not_destructive_when_state_matches():
    """If the standby holds as many devices as the primary, promotion is safe."""
    m = HubMonitor(failure_threshold=3, local_device_count=34)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=True))
    proposal = m.promotion_proposal()
    assert proposal.proposed is True
    assert proposal.destructive is False


# ── State-divergence safeguard (the Pi=34 / Mac=23 case) ───────────────────────

def test_promotion_flagged_destructive_when_state_diverges():
    """The real topology: primary had 34 devices, standby holds 23. Promotion
    would lose 11 — must be flagged destructive."""
    m = HubMonitor(failure_threshold=3, local_device_count=23)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=True))
    proposal = m.promotion_proposal()
    assert proposal.proposed is True
    assert proposal.destructive is True
    assert proposal.local_devices == 23
    assert proposal.primary_devices_last_known == 34
    assert "loses 11 devices" in proposal.reason


def test_snapshot_reports_divergence_and_unsafe():
    m = HubMonitor(failure_threshold=3, local_device_count=23)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=True))
    snap = m.snapshot()
    assert snap["state_divergent"] is True
    assert snap["promotion_safe"] is False
    assert snap["monitor_state"] == "PRIMARY_DOWN"


# ── Scenario 3: network partition (UNCERTAIN) ──────────────────────────────────

def test_threshold_misses_with_network_down_is_uncertain():
    """Possible partition: primary unreachable AND our own network degraded.
    The monitor must NOT propose promotion — no silent split-brain (§11.4.5)."""
    m = HubMonitor(failure_threshold=3, local_device_count=23)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=False))   # network also down
    assert m.state is MonitorState.UNCERTAIN
    assert m.promotion_proposal().proposed is False


def test_uncertain_does_not_propose_even_with_matching_state():
    """Even if state would not be destructive, UNCERTAIN never proposes."""
    m = HubMonitor(failure_threshold=3, local_device_count=34)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=False))
    assert m.state is MonitorState.UNCERTAIN
    assert m.promotion_proposal().proposed is False


def test_partition_then_recovery_returns_to_watching():
    m = HubMonitor(failure_threshold=3)
    m.observe(obs(primary=True, network=True, devices=34))
    for _ in range(3):
        m.observe(obs(primary=False, network=False))
    assert m.state is MonitorState.UNCERTAIN
    m.observe(obs(primary=True, network=True, devices=34))  # primary returns
    assert m.state is MonitorState.WATCHING
    assert m.consecutive_misses == 0


# ── Config validation ──────────────────────────────────────────────────────────

def test_invalid_threshold_rejected():
    try:
        HubMonitor(failure_threshold=0)
        assert False, "threshold 0 must raise"
    except ValueError:
        pass


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} hub monitor tests passed.")
    sys.exit(1 if failed else 0)
