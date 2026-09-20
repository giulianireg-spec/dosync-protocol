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


def test_start_state_refresh_reads_default_interval_without_crashing():
    """Regression: with interval=None (the server's path), start_state_refresh
    reads DOSYNC_STATE_REFRESH_INTERVAL via os. A missing 'import os' killed the
    loop with NameError before it ever logged 'started' -- and no unit test
    caught it, because they all passed an explicit interval. Only firing the real
    startup path exercises the os.environ read."""
    hub = DoSyncHub(db_path=":memory:")

    async def go():
        task = asyncio.create_task(hub.start_state_refresh(object()))  # interval=None
        await asyncio.sleep(0.05)          # time to read os.environ and start the loop
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
        return results[0]

    outcome = asyncio.run(go())
    # a healthy cancel leaves the coroutine returning None; the missing-import bug
    # would leave a NameError here instead.
    assert isinstance(outcome, asyncio.CancelledError) or outcome is None
