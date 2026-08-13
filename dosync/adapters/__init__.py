"""
DoSync — Adapter Layer
======================
Translation layer between the DoSync protocol and real physical devices.

Modelo:
    DoSync Hub → AdapterExecutor → [WiZAdapter | GPIOAdapter | ShellyAdapter | ...]

Para agregar un nuevo dispositivo:
    1. Crear adapters/mi_marca.py implementando DoSyncAdapter
    2. Register the device with adapter="my_brand" in its CapabilityManifest
    3. The hub treats it like any other device — no core changes needed

Publishing third-party adapters:
    pip install dosync-adapter-philipshue
    pip install dosync-adapter-shelly
    pip install dosync-adapter-matter
"""

from __future__ import annotations
import logging
import time as _time
from abc import ABC, abstractmethod

from ..models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters")


# ── Base interface every adapter must implement ───────────────────────────────

class DoSyncAdapter(ABC):
    """
    Base interface for physical device adapters.

    Cada adapter traduce acciones DoSync al protocolo nativo
    del dispositivo (UDP, HTTP, GPIO, BLE, etc.).

    Para implementar un adapter nuevo:

        class MyBrandAdapter(DoSyncAdapter):
            async def execute(self, action, urgency):
                # traducir action.action + action.params al protocolo del dispositivo
                return ActionResult(
                    device_id=action.device_id,
                    action=action.action,
                    success=True,
                    response={"status": "ok"},
                )

            async def connect(self, config):
                # initialize connection with the device
                pass

            async def disconnect(self):
                pass

            @property
            def adapter_name(self):
                return "mybrand"
    """

    @abstractmethod
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Execute an action on the physical device."""
        ...

    async def connect(self, config: dict) -> None:
        """Initialize the connection to the device. Optional."""
        pass

    async def disconnect(self) -> None:
        """Close the connection. Optional."""
        pass

    #: What kind of adapter this is. Declared, not inferred, because the
    #: distinction is a claim the project makes and should be checkable.
    #:
    #:   "ecosystem" — implements an OPEN STANDARD or an open project: MQTT,
    #:       Matter, BLE, MAVLink, the Home Assistant bridge. These belong in a
    #:       protocol the way HTTP support belongs in a web framework.
    #:
    #:   "reference" — implements ONE VENDOR'S PRODUCT. Shipped as a worked
    #:       example of how an adapter is written, not as an endorsement, a
    #:       partnership, or a promise to track that vendor's firmware. A
    #:       protocol that ships vendor code without saying this implies both
    #:       that it privileges those brands and that it is a smart-home
    #:       product — neither of which is true here.
    #:
    #:   "infrastructure" — not a device technology at all (notifications).
    #:
    #:   "third_party" — arrived through an entry point from a package the
    #:       operator installed. Set BY THE LOADER, never by the plugin: where
    #:       code came from is not the code's to assert.
    adapter_kind: str = "ecosystem"

    async def discover(self, timeout: float = 5.0) -> list:
        """Find devices reachable over THIS adapter's transport.

        Optional. The default returns nothing, which is the correct answer for
        most adapters and is not a failure: a drone does not announce itself on
        a broadcast, a clinical device sits on a proprietary bus, and a sensor
        on a radio link is only visible to whatever gateway speaks that radio.
        Discovery is a property of a transport, not a promise a protocol can
        make on every transport's behalf.

        Implementing it means answering it in that transport's own terms —
        UDP broadcast for WiZ, BLE advertisements for Bluetooth, mDNS for
        Shelly, commissioning for Matter. Discovery previously lived in a
        central module with `if adapter == "wiz"`, which meant every new
        transport had to edit shared code to be findable; now a transport
        answers for itself.

        Returns a list of `dosync.discovery.DiscoveredDevice`. Finding a device
        does NOT register it — the operator approves and names candidates (see
        POST /v1/discovery/adopt).
        """
        return []

    def can_discover(self) -> bool:
        """Whether this adapter can search its transport RIGHT NOW.

        Implementing `discover` is necessary and not sufficient: a BLE adapter
        with no `bleak` installed, or a hub with no radio, implements it and
        cannot use it. The distinction matters because the answer feeds a report
        of which transports were searched — and claiming to have searched
        Bluetooth when the library was missing produces exactly the false
        "nothing found" this reporting exists to prevent.

        Adapters whose readiness depends on something beyond the method existing
        should override this. The default answers the structural question alone.
        """
        return type(self).discover is not DoSyncAdapter.discover

    async def get_state(self, device_id: str) -> dict | None:
        """
        Query current device state directly from the physical device.

        Returns a state dict if supported, None if not implemented.
        The StateAwareResolver background refresher calls this periodically
        to keep the state cache fresh without blocking intent resolution.

        Example return values:
            {"on": True, "brightness": 80}       — WiZ bulb
            {"on": False}                         — Shelly relay
            {"state": "locked"}                   — door lock
            None                                  — adapter does not support state query
        """
        return None  # default: not supported — override in subclasses

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Unique adapter name. Must match the 'adapter' field in the device manifest."""
        ...


# ── AdapterExecutor — el ejecutor central ────────────────────────────────────

class AdapterExecutor:
    """
    Ejecutor central que delega acciones al adapter correcto
    based on the 'adapter' field of the device's CapabilityManifest.

    A device with no registered adapter falls back to the SimulatedExecutor.

    Uso:
        executor = AdapterExecutor(hub)
        executor.register(WiZAdapter())
        executor.register(GPIOAdapter())

        # The hub uses this executor instead of the SimulatedExecutor
        result = await hub.execute_intent(intent, executor)
    """

    def __init__(self, hub, fallback_to_simulated: bool = True):
        """
        Args:
            hub: instancia de DoSyncHub
            fallback_to_simulated: si True, dispositivos sin adapter
                                   usan SimulatedExecutor en lugar de fallar
        """
        self._hub = hub
        self._adapters: dict[str, DoSyncAdapter] = {}
        self._fallback = fallback_to_simulated

        if fallback_to_simulated:
            from ..executor import SimulatedExecutor
            self._simulated = SimulatedExecutor()
        else:
            self._simulated = None

    def register(self, adapter: DoSyncAdapter) -> None:
        """Register an adapter under its name."""
        self._adapters[adapter.adapter_name] = adapter
        log.info("Adapter registered: %s", adapter.adapter_name)

    def get_adapter(self, adapter_name: str) -> DoSyncAdapter | None:
        """Returns the adapter instance for a given adapter name, or None if not registered."""
        return self._adapters.get(adapter_name)


    def registered_adapters(self) -> list[str]:
        """Lista de adapters registrados."""
        return list(self._adapters.keys())

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """
        Execute an action, looking up the right adapter for the device.
        Falls back to the SimulatedExecutor when the device has no adapter.
        """
        device = self._hub.registry.get(action.device_id)

        if device is None:
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error=f"Device '{action.device_id}' not found in registry",
            )

        adapter_name = getattr(device, "adapter", None)

        # State awareness: skip redundant actions
        from ..hub import StateAwareResolver
        resolver = getattr(self._hub, 'resolver', None)
        if isinstance(resolver, StateAwareResolver):
            dummy_action = action
            if resolver._is_redundant(dummy_action):
                log.info("StateAwareResolver: skipped redundant %s on %s",
                         action.action, action.device_id)
                # ActionResult already imported at module level (line 25)
                return ActionResult(
                    device_id=action.device_id,
                    action=action.action,
                    success=True,
                    response={"status": "skipped_redundant"},
                )

        if adapter_name and adapter_name in self._adapters:
            log.info(
                "Dispatching %s.%s to adapter '%s'",
                action.device_id, action.action, adapter_name,
            )
            try:
                # Stamped at the moment of dispatch so verification can tell a
                # sensor reading that arrived AFTER the action from one that
                # predates it — the latter confirms nothing, however recent.
                action.dispatched_at = _time.time()
                result = await self._adapters[adapter_name].execute(action, urgency)
                if result.success:
                    self._update_resolver_state(action)
                # Device Health Monitor — registrar resultado
                self._record_health(action, result)
                return result
            except Exception as e:
                log.error(
                    "Adapter '%s' failed for %s.%s: %s",
                    adapter_name, action.device_id, action.action, e,
                )
                err_result = ActionResult(
                    device_id=action.device_id,
                    action=action.action,
                    success=False,
                    error=f"Adapter error: {e}",
                )
                self._record_health(action, err_result)
                return err_result

        # Fallback
        if self._simulated:
            log.info(
                "No adapter for '%s' (device %s) — using SimulatedExecutor",
                adapter_name or "none", action.device_id,
            )
            return await self._simulated.execute(action, urgency)

        return ActionResult(
            device_id=action.device_id,
            action=action.action,
            success=False,
            error=f"No adapter registered for '{adapter_name}'",
        )

    def _record_health(self, action: DeviceAction, result) -> None:
        """Registra el resultado en el Device Health Monitor."""
        try:
            db = getattr(self._hub, 'db', None)
            if db:
                db.record_execution(
                    device_id=action.device_id,
                    action=action.action,
                    success=result.success,
                    error=getattr(result, 'error', None),
                )
        except Exception as _e:
            log.warning('DeviceHealthMonitor: failed to record execution for %s: %s',
                        action.device_id, _e)

    def _update_resolver_state(self, action: DeviceAction) -> None:
        """Tell the StateAwareResolver the new state after a successful action."""
        from ..hub import StateAwareResolver
        resolver = getattr(self._hub, 'resolver', None)
        if not isinstance(resolver, StateAwareResolver):
            return
        state_update = {}
        if action.action == 'turn_on':
            state_update = {'on': True, 'brightness': action.params.get('brightness', 100)}
        elif action.action == 'turn_off':
            state_update = {'on': False, 'brightness': 0}
        elif action.action == 'set_brightness':
            state_update = {'on': True, 'brightness': action.params.get('brightness', 100)}
        elif action.action == 'unlock':
            state_update = {'locked': False}
        elif action.action == 'lock':
            state_update = {'locked': True}
        elif action.action == 'set_temperature':
            state_update = {'temperature': action.params.get('celsius')}
        if state_update:
            resolver.update_state(action.device_id, state_update)


