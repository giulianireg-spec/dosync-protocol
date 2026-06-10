# DoSync Protocol — Standard Tag Vocabulary

**Version:** 0.1  
**Status:** Active  
**Location:** `spec/TAG-VOCABULARY.md`

---

## Overview

Tags are the primary mechanism for semantic device discovery in DoSync. When a hub resolves an intent, it matches the intent's resolution tags against each device's declared tags to determine which devices are relevant.

This document defines the standard vocabulary. Implementors **SHOULD** use these tags when applicable to ensure interoperability between hubs and devices from different vendors.

**Naming convention:** lowercase, hyphenated. Example: `door-lock`, not `doorLock` or `DOOR_LOCK`.

---

## Tag categories

### 1. Device role — what the device does

| Tag | Description | Example devices |
|---|---|---|
| `light` | Controllable light source (on/off, brightness, color) | Smart bulb, ceiling light, LED strip |
| `lock` | Door or gate actuator | Smart lock, electric strike, gate controller |
| `alarm` | Audible or visual alarm output | Siren, strobe, buzzer |
| `switch` | Generic on/off switch with no other semantic role | Wall switch, relay module |
| `plug` | Smart power outlet or power strip | Smart plug, smart socket |
| `thermostat` | Temperature set-point controller | Smart thermostat, floor heating controller |
| `hvac` | Heating, ventilation, or air conditioning unit | AC, heat pump, ventilation fan |
| `blinds` | Window blinds, shades, or curtains | Motorized blinds, smart curtain rail |
| `fan` | Standalone ventilation fan | Ceiling fan, desk fan with smart control |
| `camera` | Image or video capture device | IP camera, video doorbell |
| `display` | Screen for information output | Smart TV, information display, e-ink panel |
| `speaker` | Audio output device | Smart speaker, soundbar |
| `appliance` | Generic home appliance with on/off control | Washing machine, dishwasher, coffee maker |

---

### 2. Sensing — what the device measures

| Tag | Description | Example devices |
|---|---|---|
| `sensor` | Generic sensor — use a more specific tag when possible | Generic sensor module |
| `motion` | Motion or presence detection | PIR sensor, radar presence sensor |
| `temperature` | Temperature measurement | DHT22, temperature probe |
| `humidity` | Humidity measurement | DHT22, humidity sensor |
| `smoke` | Smoke or fire detection | Smoke detector, CO detector |
| `door-sensor` | Door open/close state | Magnetic door sensor |
| `window-sensor` | Window open/close state | Magnetic window sensor |
| `energy-meter` | Electrical power consumption | Smart energy meter, clamp meter |
| `water-sensor` | Water or flood detection | Leak detector |
| `air-quality` | Air quality measurement | CO2 sensor, VOC sensor, PM2.5 sensor |

---

### 3. Communication — how the device informs people

| Tag | Description | Example devices |
|---|---|---|
| `notification` | Can send push/SMS/email notifications | SMS gateway, push notification service |
| `communication` | Can display or announce information | TV with DoSync display mode, screen |

---

### 4. Semantic role — which scenarios the device participates in

These tags determine which intent classes include the device. They should be combined with role or sensing tags, not used alone.

| Tag | Participates in | Notes |
|---|---|---|
| `emergency` | `ensure_safety`, `alert_anomaly` | Signals semantic relevance for emergency scenarios. **Independent from `emergency_capable: true`** — the flag controls policy bypass; the tag controls scoring inclusion. A device may have one without the other. |
| `security` | `alert_anomaly`, `away_mode` | Devices relevant to physical security |
| `energy` | `save_energy`, `away_mode` | Devices that consume significant power |
| `health` | `monitor_health` | Devices relevant to personal health monitoring |

---

### 5. Location — where the device is installed

Location tags enable the resolver's location-match scoring bonus. A device and an intent context that share a location tag score higher than one without a match.

| Tag | Description |
|---|---|
| `entrance` | Main entrance, front door area, or access point |
| `bedroom` | Bedroom (any) |
| `living-room` | Living room or main common area |
| `kitchen` | Kitchen |
| `bathroom` | Bathroom |
| `hallway` | Corridor or hallway |
| `office` | Home office or study |
| `garage` | Garage |
| `outdoor` | Outdoor area (garden, terrace, porch) |
| `dining-room` | Dining room |
| `basement` | Basement or utility room |

> **Deployment note:** Location tags are optional but strongly recommended. A hub without location tags cannot use the resolver's location-match scoring — all devices of the same type score equally regardless of where they are installed.

---

### 6. Infrastructure — devices excluded from intent resolution

| Tag | Description | Usage |
|---|---|---|
| `system` | Hub-internal or infrastructure device | Declare as the **only** tag. No intent class maps to `system`, so score is always 0 and the device is excluded from all ActionPlans. **Do not combine `system` with other semantic tags** — doing so allows the device to score on those tags. |

> **Examples:** HA system sensors (sun, backup), hub health monitors, internal test devices.

---

## Intent-to-tag mapping

This table shows the default resolution tags configured at hub initialization. Operators may configure different tags per intent via the hub database — this table reflects the default, not a normative constraint.

> **Primary vs Secondary tags:** Both columns use the same scoring weight (tag overlap × 10). "Primary" indicates tags most directly associated with the intent; "Secondary" indicates tags that add relevance but are less specific. The distinction is conceptual guidance, not a difference in scoring behavior.

| Intent class | Primary tags | Secondary tags |
|---|---|---|
| `ensure_safety` | `emergency`, `alarm` | `light`, `lock`, `notification`, `communication` |
| `alert_anomaly` | `emergency`, `alarm`, `security` | `notification`, `communication`, `sensor` |
| `control_access` | `lock` | `entrance`, `door-sensor`, `camera` |
| `monitor_health` | `health`, `sensor` | `temperature`, `humidity`, `camera`, `motion` |
| `notify_family` | `notification`, `communication` | `display`, `speaker` |
| `report_status` | `sensor`, `energy-meter` | `temperature`, `humidity`, `motion`, `door-sensor` |
| `set_environment` | `thermostat`, `hvac`, `light` | `blinds`, `fan`, `temperature` |
| `save_energy` | `energy`, `light`, `plug` | `switch`, `thermostat`, `hvac`, `appliance` |
| `remind_chore` | `notification`, `communication` | `display`, `speaker` |
| `bedtime_routine` | `light`, `blinds` | `thermostat`, `bedroom` |
| `morning_routine` | `light`, `blinds` | `thermostat`, `bedroom` |
| `away_mode` | `energy`, `security`, `lock` | `light`, `plug`, `switch`, `entrance` |

> **Domain-specific intents:** The intent classes above cover universal scenarios applicable across home, hotel, hospital, and industrial deployments. Domain-specific scenarios (e.g., a children arrival notification, a specific medical routine, a factory shift change) should be implemented as custom intent classes configured per deployment — not added to this standard vocabulary.

---

## Multi-tag patterns

Devices should declare multiple tags when they serve multiple roles. The resolver accumulates score across all matching tags.

**Correct — bulb with emergency role:**
```json
{
  "device_id": "wiz-living1-01",
  "tags": ["light", "emergency", "living-room", "energy"],
  "emergency_capable": true
}
```

**Correct — PIR sensor:**
```json
{
  "device_id": "rpi-pir-01",
  "tags": ["sensor", "motion", "security", "emergency", "entrance"],
  "emergency_capable": true
}
```

**Correct — SMS notifier:**
```json
{
  "device_id": "notifier-sms-01",
  "tags": ["notification", "communication", "emergency"],
  "emergency_capable": true
}
```

---

## Non-standard tags

The following tag patterns are **NOT** part of the standard vocabulary and reduce interoperability:

| Pattern | Problem | Alternative |
|---|---|---|
| Vendor names (`wiz`, `shelly`, `zigbee`) | Not portable across hubs | Remove — use `light`, `plug`, etc. |
| Transport names (`mqtt`, `ble`, `wifi`) | Transport is hub-internal | Remove — not semantically meaningful |
| Generic only (`sensor` alone) | Too broad for accurate resolution | Combine: `sensor` + `temperature` |
| Incorrect role tags (`smart-plug` on a bulb) | Confuses intent resolution | Use the correct role tag: `light` |

---

## Production deployment reference

The following table shows the recommended tags for the devices in the DoSync reference deployment, including corrections to the current configuration:

| Device | Current tags | Recommended tags | Change |
|---|---|---|---|
| WiZ bulbs (living 1+2) | `light, emergency, climate, smart-plug, wiz` | `light, emergency, energy, living-room` | Remove `climate`, `smart-plug`, `wiz`; add location |
| WiZ bulbs (comedor 1+2) | `light, emergency, climate, smart-plug, wiz` | `light, emergency, energy, dining-room` | Remove `climate`, `smart-plug`, `wiz`; add location |
| WiZ bulb (bedroom) | `light, emergency, climate, smart-plug, wiz` | `light, emergency, energy, bedroom` | Remove `climate`, `smart-plug`, `wiz`; add location |
| WiZ bulbs (kitchen) | `light, emergency, climate, smart-plug, wiz` | `light, emergency, energy, kitchen` | Remove `climate`, `smart-plug`, `wiz`; add location |
| PIR sensor | `sensor, motion, security, emergency` | `sensor, motion, security, emergency, entrance` | Add location |
| DHT22 sensor | `sensor, climate, temperature, humidity` | `sensor, temperature, humidity` | Remove `climate` (not a climate controller) |
| SMS notifier | `communication, notification, children_arrival` | `notification, communication, emergency` | Add `emergency`; remove domain-specific `children_arrival` |
| Alarm | `emergency, alarm, security` | `emergency, alarm, security` | ✓ Correct |

---

## Deprecated tags

The following tags were used in earlier deployments but are superseded by more specific vocabulary. New implementations SHOULD NOT use them. Existing deployments SHOULD migrate.

| Deprecated tag | Replaced by | Notes |
|---|---|---|
| `climate` | `thermostat` or `hvac` (for controllers); remove entirely (for sensors/lights) | `climate` was used broadly and inconsistently — lights, sensors, and HVAC all used it |
| `door-lock` | `lock` | `lock` is the canonical tag; `door-lock` remains understood by the resolver but is not standard |
| `smart-plug` | `plug` | Vendor-flavored alias for the standard `plug` tag |
| `children_arrival` | Remove entirely | Domain-specific tag for a demo scenario; not part of the standard vocabulary |

---

## Proposing new tags

To add a tag to this vocabulary, open a GitHub Issue with label `tag-proposal` following the process in [GOVERNANCE.md](../GOVERNANCE.md).

A proposal must include:
- Tag name (lowercase, hyphenated)
- Semantic description
- Which intent classes it is relevant for
- At least one real device that would use it

---

*DoSync Protocol v0.1 · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
