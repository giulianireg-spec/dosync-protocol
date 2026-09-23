"""Active background state refresh -- the hub's health-probing loop.

The eleventh and last of the responsibilities carved out of `hub.py`: a
background loop that periodically queries get_state() on every device whose
adapter supports it, WITHOUT executing any action, marking each responder
reachable (active health probing) and noticing recoveries. It is a subsystem,
not orchestration -- it runs on its own cadence and touches only the resolver,
device health and the registry, which it reads from the hub at the moment of use.

The event loop task that drives it is owned by the server (which creates and
cancels it around the app lifespan); this class provides the coroutine the task
runs. `DoSyncHub` owns a StateRefresher and delegates start_state_refresh and
_state_refresh_cycle to it, so every caller keeps working. The methods are
unchanged; only their address is.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation only
    from .executor import DeviceExecutor

log = logging.getLogger("dosync.hub")


class StateRefresher:
    def __init__(self, hub):
    # The hub, not its services. server.py builds the hub first and installs
    # some of them afterwards -- `hub.policy_engine = policy_engine` always, and
    # `hub.resolver = ExternalResolver(...)` when an external resolver is
    # configured. Values captured here at construction froze the ones the hub
    # started with: the policy engine was None, so every composite step was
    # dispatched with no policy at all -- a 100 m geofence let a 1000 m waypoint
    # through. Each service is read from the hub at the moment of use, as the
    # code did before it moved out of hub.py.
        self._hub = hub

    resolver = property(lambda self: self._hub.resolver)
    health   = property(lambda self: self._hub.health)
    registry = property(lambda self: self._hub.registry)

    async def start_state_refresh(
        self,
        executor: "DeviceExecutor",
        interval: float = None,
    ) -> None:
        """Hub-owned background state refresher — ACTIVE device health probing.

        Periodically queries get_state() on every device whose adapter supports
        it, WITHOUT executing any action. Two effects:
          * hub.health.mark_reachable() on every responder — this is the active
            probing that makes recovery detectable within one interval, instead
            of waiting for the unreachable TTL to lapse or for some action to
            happen to succeed.
          * the resolver's own state cache is refreshed if it keeps one
            (StateAwareResolver.update_state), preserving redundancy detection
            for deployments that use it.

        Until 2026-07-14 this lived on StateAwareResolver and server.py started
        it behind `isinstance(hub.resolver, StateAwareResolver)` — always False
        in production (which runs ExternalResolver), so it NEVER ran, silently,
        behind a log.debug. State refresh is a hub concern, not a resolver's:
        the same reasoning that moved device health to the hub.

        POSITIVE SIGNAL ONLY, by deliberate design (preserved from the original):
        a device that does not answer get_state() is skipped, NOT marked
        unreachable. A failing get_state is weaker evidence than an action
        timing out (adapters implement it unevenly), and marking on weak
        evidence would manufacture false "dead device" reports.

        Args:
            executor: AdapterExecutor to source adapters from
            interval: seconds between cycles. Defaults to the
                      DOSYNC_STATE_REFRESH_INTERVAL env var (default 60).
        """
        if interval is None:
            interval = float(os.environ.get("DOSYNC_STATE_REFRESH_INTERVAL", "60"))

        log.info("Hub: background state refresh started (interval=%.0fs)", interval)

        while True:
            try:
                await asyncio.sleep(interval)
                await self._state_refresh_cycle(executor)
            except asyncio.CancelledError:
                log.info("Hub: background state refresh stopped")
                break
            except Exception as e:
                log.warning("Hub: state refresh cycle error: %s", e)

    async def _state_refresh_cycle(self, executor: "DeviceExecutor") -> None:
        """One refresh cycle — probe every device whose adapter supports get_state()."""
        from .adapters import AdapterExecutor
        if not isinstance(executor, AdapterExecutor):
            return

        refreshed = 0
        skipped = 0
        recovered: list[str] = []

        for device in self.registry.all():
            adapter = executor.get_adapter(device.adapter)
            if adapter is None or not hasattr(adapter, "get_state"):
                skipped += 1
                continue

            try:
                state = await asyncio.wait_for(
                    adapter.get_state(device.device_id), timeout=3.0)
            except Exception:
                skipped += 1          # positive-signal only: no unreachable mark
                continue

            if state is None:
                skipped += 1
                continue

            # The device answered: it is reachable, right now, without us acting.
            was_unreachable = self.health.is_unreachable(device.device_id)
            self.health.mark_reachable(device.device_id)
            if was_unreachable:
                recovered.append(device.device_id)

            # Keep a state-caching resolver coherent, if one is plugged in.
            _update = getattr(self.resolver, "update_state", None)
            if callable(_update):
                try:
                    _update(device.device_id, state)
                except Exception as e:
                    log.debug("Hub: resolver update_state failed for %s: %s",
                              device.device_id, e)
            _clear = getattr(self.resolver, "clear_unreachable", None)
            if was_unreachable and callable(_clear):
                try:
                    _clear(device.device_id)
                except Exception:
                    pass
            refreshed += 1

        for device_id in recovered:
            log.info("Hub: %s back online (detected by state refresh)", device_id)
        if refreshed:
            log.debug("Hub: state refresh done — %d probed, %d skipped, %d recovered",
                      refreshed, skipped, len(recovered))


    # ── Checkpoints ───────────────────────────────────────────────────────────
    #
    # The work is in CheckpointKeeper. These stay because server.py and the
    # audit tests call them on the hub, and an extraction that forces its
    # callers to move is a rewrite with better manners.
