"""
DoSync — Adapter Layer
======================
Capa de traducción entre el protocolo DoSync y dispositivos físicos reales.

Modelo:
    DoSync Hub → AdapterExecutor → [WiZAdapter | GPIOAdapter | ShellyAdapter | ...]

Para agregar un nuevo dispositivo:
    1. Crear adapters/mi_marca.py implementando DoSyncAdapter
    2. Registrar el dispositivo con adapter="mi_marca" en su CapabilityManifest
    3. El hub lo maneja igual que cualquier otro dispositivo — sin cambios al núcleo

Publicación de adapters de terceros:
    pip install dosync-adapter-philipshue
    pip install dosync-adapter-shelly
    pip install dosync-adapter-matter
"""

from __future__ import annotations
import logging
from abc import ABC, abstractmethod

from ..models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters")


# ── Interfaz base que todo adapter debe implementar ───────────────────────────

class DoSyncAdapter(ABC):
    """
    Interfaz base para adapters de dispositivos físicos.

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
    según el campo 'adapter' del CapabilityManifest del dispositivo.

    Si un dispositivo no tiene adapter registrado, cae al SimulatedExecutor.

    Uso:
        executor = AdapterExecutor(hub)
        executor.register(WiZAdapter())
        executor.register(GPIOAdapter())

        # El hub usa este executor en lugar del SimulatedExecutor
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
        """Registra un adapter por su nombre."""
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
        Ejecuta una acción buscando el adapter correcto para el dispositivo.
        Fallback al SimulatedExecutor si el dispositivo no tiene adapter.
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
        """Notifica al StateAwareResolver el nuevo estado tras una accion exitosa."""
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


