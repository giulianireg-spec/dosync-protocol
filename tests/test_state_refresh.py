"""The background state refresher, tested as its own unit.

The eleventh and last extraction moved active health probing out of hub.py into
state_refresh.py as StateRefresher, taking the resolver, device health and the
registry as constructor arguments. Its loop is driven by a task the server owns;
here the unit is checked for correct wiring and for the guard that makes one
cycle a clean no-op unless it is given an AdapterExecutor.
"""
import asyncio

from dosync.hub import DoSyncHub
from dosync.state_refresh import StateRefresher


def test_hub_owns_a_state_refresher_wired_to_its_services():
    hub = DoSyncHub(db_path=":memory:")
    sr = hub._state_refresher
    assert isinstance(sr, StateRefresher)
    # it probes the same services the hub uses, or it would report on nothing
    assert sr.resolver is hub.resolver
    assert sr.health is hub.health
    assert sr.registry is hub.registry


def test_refresh_cycle_returns_cleanly_without_an_adapter_executor():
    """The refresher probes only through an AdapterExecutor; given anything else
    it returns cleanly without touching devices, never crashing the loop."""
    hub = DoSyncHub(db_path=":memory:")
    asyncio.run(hub._state_refresher._state_refresh_cycle(object()))  # no-op, must not raise
