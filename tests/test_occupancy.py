"""The occupancy engine, tested against its own module.

The seventh extraction moved OccupancyEngine out of hub.py. Its behaviour --
the safe default when blind, the confidence-weighted vote, the TTL that expires
stale signals, the per-device replace -- is exercised here against
`dosync.occupancy` directly, not only through the hub that reads it.
"""
import time

from dosync.occupancy import OccupancyEngine
from dosync.models import ContextSignalType, PresenceSignal


def _sig(device_id, present, confidence, *, member=None, ts=None,
         kind=ContextSignalType.PRESENCE):
    return PresenceSignal(
        device_id=device_id, signal_type=kind, present=present,
        confidence=confidence, member_id=member,
        timestamp=time.time() if ts is None else ts)


def test_no_signals_defaults_to_occupied():
    """Blind is not the same as empty: with nothing to go on the engine assumes
    someone is home, because acting as if a house is empty when it might not be
    is the more dangerous mistake."""
    st = OccupancyEngine().get_occupancy()
    assert st.occupied is True
    assert st.confidence == 0.0
    assert st.signals_used == 0


def test_a_high_confidence_absence_outweighs_a_low_confidence_presence():
    """The vote is weighted by confidence, not counted. One phone sure it is
    away (0.9) beats one weak presence hint (0.2)."""
    e = OccupancyEngine()
    e.update(_sig("watch", present=False, confidence=0.9))
    e.update(_sig("tv", present=True, confidence=0.2))
    st = e.get_occupancy()
    assert st.occupied is False, "counting instead of weighting would call this occupied"
    assert st.signals_used == 2


def test_all_present_is_occupied_with_members():
    e = OccupancyEngine()
    e.update(_sig("phone-a", present=True, confidence=0.8, member="ana"))
    e.update(_sig("phone-b", present=True, confidence=0.7, member="beto"))
    st = e.get_occupancy()
    assert st.occupied is True
    assert set(st.members_home) == {"ana", "beto"}


def test_expired_signals_are_ignored():
    """A signal past its 5-minute TTL is stale, not evidence. With only an
    expired absence on file, the engine falls back to the safe default."""
    e = OccupancyEngine()
    e.update(_sig("watch", present=False, confidence=0.9, ts=time.time() - 400))
    st = e.get_occupancy()
    assert st.signals_used == 0, "an expired signal was still counted"
    assert st.occupied is True


def test_update_replaces_same_device_and_type():
    """A device's latest signal replaces its previous one of the same type --
    it does not stack. A phone that says 'home' then 'away' is away."""
    e = OccupancyEngine()
    e.update(_sig("phone", present=True, confidence=0.9))
    e.update(_sig("phone", present=False, confidence=0.9))
    st = e.get_occupancy()
    assert st.signals_used == 1
    assert st.occupied is False
