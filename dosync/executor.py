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

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
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
            )

        response = self._simulate_response(action, urgency)
        log.info("Executed: %s.%s → %s", action.device_id, action.action, response)

        return ActionResult(
            device_id=action.device_id,
            action=action.action,
            success=True,
            response=response,
        )

    def _simulate_response(self, action: DeviceAction, urgency: Urgency) -> dict:
        responses = {
            "unlock":          {"status": "unlocked", "duration_seconds": action.params.get("duration_seconds", 300)},
            "lock":            {"status": "locked"},
            "call":            {"status": "calling", "number": action.params.get("number", "911")},
            "notify":          {"status": "sent", "channels": ["push", "sms"]},
            "alarm":           {"status": "activated", "pattern": action.params.get("pattern", "alert")},
            "light":           {"status": "on", "brightness": action.params.get("brightness", 100)},
            "set_brightness":  {"brightness": action.params.get("brightness", 100)},
            "set_temperature": {"celsius": action.params.get("celsius", 21), "status": "set"},
            "read_sensors":    {"readings": {sid: round(random.uniform(15, 25), 1)
                                             for sid in action.params.get("sensor_ids", [])}},
        }
        return responses.get(action.action, {"status": "ok"})
