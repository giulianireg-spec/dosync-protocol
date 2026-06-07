# DoSync Adapter Development Guide

This guide explains how to write a DoSync adapter — the component that translates a `DeviceAction` into a device-native command. Adapters are the extension point for adding any new device type to a DoSync hub without modifying the core protocol.

---

## What is an adapter?

An adapter is a class that knows how to talk to a specific type of device. The hub's `AdapterExecutor` calls `adapter.execute(action, urgency)` for every device action in an `ActionPlan`. The adapter translates the DoSync action into the device's native protocol (UDP, HTTP, GPIO, MQTT, etc.) and returns an `ActionResult`.

The hub never changes to support a new device type. Only the adapter changes.

---

## The interface

```python
from dosync.adapters import DoSyncAdapter
from dosync.models import ActionResult, DeviceAction, Urgency

class MyAdapter(DoSyncAdapter):

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        """Translate a DoSync action into a device-native command."""
        # Your implementation here
        ...

    @property
    def adapter_name(self) -> str:
        """Unique adapter name. Must match the 'adapter' field in device manifests."""
        return "myadapter"
```

That is the minimum required interface. Two methods — nothing else is mandatory.

---

## Minimal working example

A simulated adapter that logs every action and returns success:

```python
import logging
from dosync.adapters import DoSyncAdapter
from dosync.models import ActionResult, DeviceAction, Urgency

log = logging.getLogger("dosync.adapters.simulated")

class SimulatedAdapter(DoSyncAdapter):

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        log.info(
            "Simulated: device=%s action=%s urgency=%s params=%s",
            action.device_id, action.action, urgency.value, action.params
        )
        return ActionResult(
            device_id=action.device_id,
            action=action.action,
            success=True,
            response={"simulated": True},
        )

    @property
    def adapter_name(self) -> str:
        return "simulated"
```

---

## Registering an adapter

```python
from dosync.executor import AdapterExecutor
from my_package import MyAdapter

executor = AdapterExecutor()
executor.register(MyAdapter())
```

In the reference hub (`server.py`), adapters are registered in the `lifespan()` startup function. The `AdapterExecutor` dispatches to the right adapter based on the `adapter` field in each device's `CapabilityManifest`.

---

## The DeviceAction object

```python
@dataclass
class DeviceAction:
    device_id:       str    # target device
    action:          str    # actuator type (turn_on, unlock, notify, etc.)
    params:          dict   # action parameters — device-specific
    relevance_score: float  # resolver confidence score — informational only
```

`params` carries any data the resolver or caller passed for this specific action. Common patterns:

```python
# Light control
action.params = {"brightness": 255, "color_temp": 6500}

# Lock
action.params = {"duration_seconds": 300}

# Notification
action.params = {"message": "Motion detected at entrance", "to": "+1234567890"}
```

For emergency intents (`urgency == Urgency.EMERGENCY`), your adapter SHOULD override any brightness/volume/intensity params to maximum — the hub expects emergency-capable devices to behave at full intensity in emergencies.

---

## The ActionResult object

```python
@dataclass
class ActionResult:
    device_id:    str
    action:       str
    success:      bool
    response:     Any           # adapter-specific response data
    error:        Optional[str] # error message if success=False
    aborted:      bool          # True if cancelled by FailurePolicy=ABORT
    retries:      int           # retry attempts before this result
    executed_at:  float         # Unix timestamp
```

Always return an `ActionResult` — never raise an exception from `execute()`. If the device is unreachable, return `success=False` with an `error` message. The hub handles partial failures and logs them in the tamper-evident audit trail.

---

## Optional methods

```python
async def connect(self, config: dict) -> None:
    """Initialize the connection to the device. Called before first execute()."""
    pass

async def disconnect(self) -> None:
    """Close the connection. Called on hub shutdown."""
    pass

async def get_state(self, device_id: str) -> dict | None:
    """Return current device state for the StateAwareResolver cache.
    Return None if state cannot be read. Never raise."""
    return None
```

`get_state()` is used by the `StateAwareResolver` to avoid redundant actions. If your adapter can read device state cheaply (e.g., via HTTP polling), implement it. The resolver uses it to skip `turn_on` actions for devices already on at the requested brightness.

---

## Writing the Capability Manifest

Your adapter is paired with a `CapabilityManifest` that describes the device. The `adapter` field must match your `adapter_name`. The `adapter_config` field holds any connection parameters your adapter needs (IP address, port, credentials, etc.).

```json
{
  "device_id": "my-device-01",
  "device_name": "My Device",
  "manufacturer": "Acme Corp",
  "model": "Model X",
  "firmware": "1.0.0",
  "category": "actuator",
  "tags": ["light", "living-room", "emergency"],
  "capabilities": {
    "sensors": [],
    "actuators": [
      {"id": "turn_on",  "type": "turn_on",  "description": "Turn on"},
      {"id": "turn_off", "type": "turn_off", "description": "Turn off"}
    ],
    "events": [],
    "context_signals": []
  },
  "emergency_capable": true,
  "adapter": "myadapter",
  "adapter_config": {
    "ip": "192.168.1.42",
    "port": 38899
  }
}
```

**Tags are critical for resolution.** A device without the right tags will not be included in action plans for relevant intents. See `docs/DEPLOYMENT-TAGS-GUIDE.md` for the full tag vocabulary.

`adapter_config` is redacted from public API responses (`GET /v1/devices`). Use it freely for IPs, ports, and API keys — it will not be exposed to clients.

---

## Emergency handling

If your device is `emergency_capable: true`, it MUST handle `Urgency.EMERGENCY` intents immediately and at maximum intensity. The hub bypasses all policy constraints for emergency intents.

```python
async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
    if urgency == Urgency.EMERGENCY:
        # Override params — maximum intensity regardless of what was requested
        params = {"brightness": 255, "color_temp": 6500}
    else:
        params = action.params
    # ... proceed with params
```

---

## Testing your adapter

### Unit test

```python
import asyncio
from dosync.models import DeviceAction, Urgency

async def test_adapter():
    adapter = MyAdapter()
    action = DeviceAction(
        device_id="test-device-01",
        action="turn_on",
        params={"brightness": 100},
    )
    result = await adapter.execute(action, Urgency.INFO)
    assert result.success, f"Expected success, got: {result.error}"
    print(f"✓ execute() succeeded: {result.response}")

asyncio.run(test_adapter())
```

### Integration test with certify.py

Start a hub with your adapter registered, register a test device with your adapter type, then run:

```bash
python3 certify.py --host localhost --port 47200 --tier standard
```

An adapter that correctly handles `turn_on`, `turn_off`, `notify`, `unlock`, `alarm`, and `call` actions will pass all Standard tests. Emergency tier additionally requires `emergency_capable: true` in the manifest and correct behavior on `Urgency.EMERGENCY` actions.

---

## Publishing your adapter

There is no central registry — adapters are Python packages. Recommended conventions:

- Package name: `dosync-adapter-<brand>` (e.g., `dosync-adapter-shelly`)
- Module: `dosync_adapter_<brand>` (e.g., `dosync_adapter_shelly`)
- Entry point: the adapter class with `adapter_name` returning the brand name
- Include a `README.md` with: supported device models, `adapter_config` fields, tag recommendations
- Run `certify.py` before publishing and include the result in your README

---

## Reference implementations

| Adapter | Transport | File | Notes |
|---|---|---|---|
| `wiz` | UDP (pywizlight) | `dosync/adapters/wiz.py` | Philips WiZ bulbs |
| `homeassistant` | HTTP (aiohttp) | `dosync/adapters/homeassistant.py` | HA bridge — wraps 10 HA domains |
| `notifications` | HTTP (Twilio) | `dosync/adapters/notifications.py` | SMS via Twilio |
| `simulated` | In-memory | `dosync/executor.py` | Reference, no hardware needed |
| `gpio` | GPIO (RPi.GPIO) | `gpio_adapter.py` | Raspberry Pi PIR + DHT22 |
| `shelly` | HTTP | `dosync/adapters/shelly.py` | Shelly Gen1/Gen2 |
| `matter` | HTTP (python-matter-server) | `dosync/adapters/matter.py` | Matter devices |

Study `dosync/adapters/wiz.py` for a complete production example with emergency handling, state reading, and graceful error handling.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
