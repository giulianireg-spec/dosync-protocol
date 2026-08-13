"""
DoSync — Device Executor
Abstracts the actual sending of actions to physical devices.
Provides a SimulatedExecutor for testing without hardware.
"""

from __future__ import annotations
import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

from .models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.executor")


class DeviceExecutor(ABC):
    """
    Abstract executor. In production, subclass this for each transport:
    WiFiExecutor, BLEExecutor, ZigbeeExecutor, etc.
    """

    @abstractmethod
    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        ...


class SimulatedExecutor(DeviceExecutor):
    """
    Simulates device responses for development and certification testing.
    Each device_id can be configured with custom behaviors.
    """

    def __init__(self, failure_rate: float = 0.0):
        """
        Args:
            failure_rate: probability [0.0–1.0] that a device randomly fails.
                          Use 0.1 to simulate a 10% hardware failure rate.
        """
        self.failure_rate = failure_rate
        self._custom: dict[str, Any] = {}

    def set_device_behavior(self, device_id: str, always_fail: bool = False,
                             latency_ms: int = 50) -> None:
        self._custom[device_id] = {"always_fail": always_fail, "latency_ms": latency_ms}

    async def execute(self, action: DeviceAction, urgency: Urgency,
                      reason: str = "explicit_simulation") -> ActionResult:
        """Simulate the action and SAY SO in the result.

        `reason` is why simulation happened, and the caller is the only one who
        knows: an AdapterExecutor falling back passes "no_adapter_declared",
        while a hub built for certification or a corpus gets the default.
        """
        behavior = self._custom.get(action.device_id, {})
        latency  = behavior.get("latency_ms", 50) / 1000
        await asyncio.sleep(latency)

        if behavior.get("always_fail") or random.random() < self.failure_rate:
            log.warning("Simulated failure: %s / %s", action.device_id, action.action)
            return ActionResult(
                device_id=action.device_id,
                action=action.action,
                success=False,
                error="Simulated device failure",
                simulated=True,
                simulated_reason=reason,
            )

        response = self._simulate_response(action, urgency)
        log.info("Simulated (%s): %s.%s → %s", reason, action.device_id,
                 action.action, response)

        return ActionResult(
            device_id=action.device_id,
            action=action.action,
            success=True,
            response=response,
            simulated=True,
            simulated_reason=reason,
        )

    def _simulate_response(self, action: DeviceAction, urgency: Urgency) -> dict:
        responses = {
            # Safety
            "unlock":          {"status": "unlocked", "duration_seconds": action.params.get("duration_seconds", 300)},
            "lock":            {"status": "locked"},
            "call":            {"status": "calling", "number": action.params.get("number", "911")},
            "alarm":           {"status": "activated", "pattern": action.params.get("pattern", "alert")},
            "arm":             {"status": "armed", "mode": action.params.get("mode", "away")},
            # Notificaciones
            "notify":          {"status": "sent", "channels": ["push", "sms"]},
            "display":         {"status": "shown", "message": action.params.get("message", "")},
            # Iluminacion
            "light":           {"status": "on", "brightness": action.params.get("brightness", 100)},
            "set_brightness":  {"status": "set", "brightness": action.params.get("brightness", 0)},
            # Clima
            "set_temperature": {"status": "set", "celsius": action.params.get("celsius", 21)},
            "set_position":    {"status": "set", "position": action.params.get("position", 0)},
            # Energia
            "turn_off":        {"status": "off", "device": action.device_id},
            "turn_on":         {"status": "on",  "device": action.device_id},
            # Sensores
            "read_sensors":    {"readings": {sid: round(random.uniform(15, 25), 1)
                                             for sid in action.params.get("sensor_ids", [])}},
        }
        return responses.get(action.action, {"status": "ok"})
