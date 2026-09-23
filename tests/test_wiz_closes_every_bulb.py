"""The WiZ adapter closes every bulb connection, including the ones that fail.

The close sat on the success path only. When a bulb did not answer -- powered
off at the wall, the usual case -- the call raised, the close was skipped, and
the UDP socket stayed open for the life of the process: the garbage collector
did not reclaim it. The state refresher polls every bulb each minute, so each
unreachable bulb leaked a socket a minute. The reference hub had 8,225 open
sockets, growing by eight a minute, when this was found.

pywizlight is an optional extra, so the bulb is replaced by a fake that fails
the way a dark bulb does and records whether it was closed.
"""
import asyncio

import pytest

import dosync.adapters.wiz as wiz
from dosync.models import DeviceAction, Urgency


class _DarkBulb:
    """Behaves like a bulb powered off at the wall: every call raises."""
    opened = []

    def __init__(self, ip):
        self.ip, self.closed = ip, False
        _DarkBulb.opened.append(self)

    async def updateState(self):
        raise ConnectionError("the bulb did not answer")

    async def turn_on(self, pilot=None):
        raise ConnectionError("the bulb did not answer")

    async def turn_off(self):
        raise ConnectionError("the bulb did not answer")

    async def async_close(self):
        self.closed = True


@pytest.fixture
def dark_bulbs(monkeypatch):
    _DarkBulb.opened = []
    monkeypatch.setattr(wiz, "WIZ_AVAILABLE", True)
    monkeypatch.setattr(wiz, "wizlight", _DarkBulb, raising=False)
    monkeypatch.setattr(wiz, "PilotBuilder", lambda **kw: object(), raising=False)
    return _DarkBulb.opened


class _Hub:
    class registry:
        @staticmethod
        def get(device_id):
            class _Device:
                adapter_config = {"ip": "192.0.2.10"}
            return _Device()


def test_a_failed_action_closes_its_bulb(dark_bulbs):
    adapter = wiz.WiZAdapter()
    for action in ("turn_on", "turn_off"):
        result = asyncio.run(adapter.execute(
            DeviceAction(device_id="lamp", action=action, params={"ip": "192.0.2.10"}),
            Urgency.INFO))
        assert result.success is False
    assert dark_bulbs and all(b.closed for b in dark_bulbs), \
        "a bulb that did not answer was left open"


def test_a_failed_state_poll_closes_its_bulb(dark_bulbs):
    adapter = wiz.WiZAdapter(hub=_Hub())
    for _ in range(5):                       # the refresher polls every minute
        assert asyncio.run(adapter.get_state("lamp")) is None
    assert len(dark_bulbs) == 5 and all(b.closed for b in dark_bulbs), \
        "each failed poll leaked a socket"
