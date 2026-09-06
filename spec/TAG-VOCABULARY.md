# DoSync Protocol — Standard Tag Vocabulary

**Version:** 0.1  
**Status:** Active  
**Location:** `spec/TAG-VOCABULARY.md`

---

## Overview

> **Changed 4 September 2026.** Tags no longer decide which devices take part in
> an intent. Participation comes from declared capability — the actuator or
> sensor an intent needs — and tags contribute to *ranking* among the devices
> that already qualify.
>
> The change was measured. A lock declaring `lock` and `unlock` was excluded
> from `control_access` for want of the tag `lock`, on a device tagged `access`
> and `security` — which is what anyone would write for a lock. Against a ground
> truth written from what the deployment's owner wants, precision went from
> 0.514 to 0.645; on two corpora it had never been tuned on, F1 went from 0.64
> and 0.61 to 0.85 and 0.83.
>
> What a device can do belongs in `spec/CAPABILITY-TYPES.md`. What it means in a
> particular deployment — where it is, what it must never touch — is what tags
> are for, and that is a decision only the installer can make.

Tags describe a device's place in a deployment. When a hub resolves an intent it
first admits every device declaring a needed capability, then ranks them, and
matching tags raise a device's score.

This document defines the standard vocabulary. Implementors **SHOULD** use these tags when applicable to ensure interoperability between hubs and devices from different vendors.

**Naming convention:** lowercase, hyphenated. Example: `door-sensor`, not `doorSensor` or `DOOR_SENSOR`.

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
| `appliance` | Generic on/off appliance with no more specific role | Washing machine, industrial dryer, coffee maker |

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

**Location tags are an open namespace. The deployment defines them; the protocol
does not.** Unlike the categories above — which are a shared vocabulary precisely
so that an intent written for one deployment means the same thing in another — a
location is a fact about one installation. The protocol has no opinion about
where your devices are, and no list it would accept or reject.

The mechanism is deliberately trivial: a device declares location tags, an intent
carries `context.location`, and the resolver awards its location bonus when the
two strings are equal. There is no enumeration, no normalisation, and no
validation. `ward-2`, `cell-3`, `deck-b`, `sector-7g` and `death-star` all work
exactly as well as `kitchen`, because the resolver is comparing strings, not
interpreting places. A conforming hub MUST NOT reject a location tag for not
appearing in any list, including this one.

Two consequences worth stating:

- **A location tag is only useful if the same string appears on both sides.**
  Whoever tags the devices and whoever writes the intents must agree. That
  agreement is a deployment convention, not a protocol rule.
- **Consistency inside a deployment matters more than which words it picks.**
  `floor-2` and `Floor 2` are different tags. Pick one form and keep it.

Examples from three real registries, to show the range rather than prescribe it:

| Deployment | Location tags in use |
|---|---|
| Residential | `entrance`, `bedroom`, `living-room`, `kitchen`, `bathroom`, `hallway`, `office`, `garage`, `outdoor`, `dining-room`, `basement` |
| Industrial | `floor-2`, `line-2`, `cell-2`, `plant` |
| Clinical | `or-3`, `ward-2`, `corridor-b` |

The residential row is the longest only because the reference deployment is
residential — it carries no more weight than the others.

> **Deployment note:** Location tags are optional but strongly recommended. A hub
> without location tags cannot use the resolver's location-match scoring — all
> devices of the same type score equally regardless of where they are installed.

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

> **Domain-specific intents:** The intent classes above cover universal scenarios applicable across home, hotel, commercial, and industrial deployments. Domain-specific scenarios (e.g., a children arrival notification, a specific maintenance routine, a factory shift change) should be implemented as custom intent classes configured per deployment — not added to this standard vocabulary.

---

## Multi-tag patterns

Devices should declare multiple tags when they serve multiple roles. The resolver accumulates score across all matching tags.

**Correct — bulb with emergency role:**
```json
{
  "device_id": "light-zone1-01",
  "tags": ["light", "emergency", "living-room", "energy"],
  "emergency_capable": true
}
```

**Correct — PIR sensor:**
```json
{
  "device_id": "sensor-motion-01",
  "tags": ["sensor", "motion", "security", "emergency", "entrance"],
  "emergency_capable": true
}
```

**Correct — SMS notifier:**
```json
{
  "device_id": "notifier-01",
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

## Applying this vocabulary to an existing deployment

A deployment that predates this vocabulary will have tags that do not match it.
Retagging is worth doing — resolution quality depends on it — and the common
corrections are the same everywhere:

| Symptom | Correction |
|---|---|
| A tag naming the **vendor or protocol** (`wiz`, `zigbee`, `tuya`) | Remove it. How a device is reached is the adapter's concern, not the resolver's. |
| A tag naming a **capability the device does not have** (`climate` on a bulb) | Remove it. It makes the device score on intents it cannot serve. |
| A tag naming **one deployment's scenario** (`children_arrival`, `night_shift`) | Remove it, or register a custom intent class that declares it. A resolution tag no other deployment would write belongs to that deployment, not to the vocabulary. |
| A device with a role but **no location tag** | Add one. Location is what makes a targeted intent targeted. |

The reference deployment went through exactly this: bulbs carrying `wiz`,
`smart-plug` and `climate`, and a notifier carrying a scenario tag from the
deployment that installed it. The inventory of one deployment is not
specification material, so it is not reproduced here.

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
