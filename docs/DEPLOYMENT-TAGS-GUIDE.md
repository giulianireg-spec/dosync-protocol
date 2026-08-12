# DoSync — Deployment Tags Guide

This document explains how to configure the `tags` field in a device's Capability Manifest to maximize capability resolution accuracy. Getting tags right is the single most effective way to improve recall for comfort and efficiency intents.

---

## Why tags matter

The capability-based resolver matches an intent against every registered device by computing a relevance score. The primary signal is **tag overlap**: how many of the device's declared tags appear in the intent's resolution tag set.

A device with correct tags participates automatically in every relevant scenario. A device with missing or wrong tags is invisible to the resolver — it will never be included in `bedtime_routine`, `save_energy`, or `away_mode` action plans, regardless of what it can physically do.

This is the most common cause of low recall in production deployments.

---

## Intent resolution tag reference

Each intent resolves against a fixed set of tags and actuator types. A device must declare **at least one matching tag** to be included.

| Intent | Required tags (any match) | Required actuators (any match) |
|---|---|---|
| `ensure_safety` | `camera` `emergency` `door-lock` `alarm` `communication` `notification` | `unlock` `call` `alarm` `light` `notify` |
| `alert_anomaly` | `communication` `phone` `display` | `notify` `call` |
| `control_access` | `door-lock` `gate` `access` | `lock` `unlock` |
| `monitor_health` | `camera` `motion` `wearable` `sensor` | — |
| `notify_family` | `communication` `display` `phone` | `notify` `call` `display` |
| `report_status` | — (all devices) | — |
| `set_environment` | `light` `thermostat` `blinds` `climate` | `set_brightness` `set_temperature` `set_position` |
| `save_energy` | `light` `thermostat` `smart-plug` `climate` `blinds` | `set_brightness` `set_temperature` `turn_off` `set_position` |
| `remind_chore` | `display` `phone` | `display` |
| `bedtime_routine` | `light` `blinds` `display` `smart-plug` `climate` | `set_brightness` `set_position` `turn_off` `set_temperature` |
| `morning_routine` | `light` `blinds` `appliance` `climate` `display` | `set_brightness` `set_position` `turn_on` `set_temperature` |
| `away_mode` | `light` `smart-plug` `camera` `alarm` `thermostat` | `turn_off` `set_brightness` `arm` `set_temperature` |
| `children_arrived_home` | `children_arrival` | `turn_on` `set_brightness` `notify` |

---

## Tag recommendations by device type

### Smart bulb (e.g. Philips WiZ, LIFX, Shelly Bulb)

```json
{
  "tags": ["light", "smart-plug", "climate"],
  "actuators": [
    { "type": "turn_on" },
    { "type": "turn_off" },
    { "type": "set_brightness" }
  ]
}
```

Why `smart-plug`: bulbs registered only as `light` are excluded from `save_energy` and `away_mode` unless `smart-plug` is also present — both intents use `smart-plug` as a resolution tag alongside `light`.

Why `climate`: some resolvers weight bulbs as part of ambient comfort. Including `climate` improves recall for `bedtime_routine` and `morning_routine` in deployments without a dedicated thermostat.

### Smart plug / relay (e.g. Shelly, TP-Link)

```json
{
  "tags": ["smart-plug", "appliance"],
  "actuators": [
    { "type": "turn_on" },
    { "type": "turn_off" }
  ]
}
```

### Thermostat / climate controller

```json
{
  "tags": ["thermostat", "climate"],
  "actuators": [
    { "type": "set_temperature" }
  ]
}
```

### Motorized blinds / shutter

```json
{
  "tags": ["blinds"],
  "actuators": [
    { "type": "set_position" }
  ]
}
```

### Door lock / smart lock

```json
{
  "tags": ["door-lock", "access", "emergency"],
  "actuators": [
    { "type": "lock" },
    { "type": "unlock", "emergency_capable": true }
  ],
  "emergency_capable": true
}
```

### PIR / motion sensor

```json
{
  "tags": ["motion", "sensor", "emergency"],
  "sensors": [
    { "type": "motion" }
  ],
  "emergency_capable": true
}
```

### Camera

```json
{
  "tags": ["camera", "emergency", "monitor_health"],
  "sensors": [
    { "type": "video" }
  ],
  "emergency_capable": true
}
```

### SMS / push notifier

```json
{
  "tags": ["communication", "notification", "phone", "children_arrival"],
  "actuators": [
    { "type": "notify" },
    { "type": "call" }
  ],
  "emergency_capable": true
}
```

### Alarm / siren

```json
{
  "tags": ["alarm", "emergency", "security"],
  "actuators": [
    { "type": "alarm" },
    { "type": "arm" }
  ],
  "emergency_capable": true
}
```

---

## The low-recall patterns to avoid

### Pattern 1 — bulb with only `light` tag

```json
{ "tags": ["light"] }
```

This device will be included in `ensure_safety` and `set_environment` but **excluded from** `save_energy`, `away_mode`, `bedtime_routine`, and `morning_routine` because those intents require `smart-plug`, `blinds`, or `climate` tags that are missing.

Fix: add `smart-plug` to all bulbs.

### Pattern 2 — notifier without `children_arrival` tag

```json
{ "tags": ["communication", "notification"] }
```

This device will not participate in `children_arrived_home` because that intent resolves exclusively against `children_arrival`.

Fix: add `children_arrival` to any notifier that should fire when children arrive.

### Pattern 3 — sensor with no tags

```json
{ "tags": [] }
```

This device scores 0 on every intent except `report_status` (which resolves all devices). It will never appear in any action plan.

Fix: declare the appropriate sensor type tags.

### Pattern 4 — missing `emergency_capable` on critical devices

A device can match all the right tags but still be excluded from emergency scenarios if `emergency_capable` is `false` or missing. The emergency bonus score (+30) only applies to `emergency_capable: true` devices — all others score significantly lower and may fall below the inclusion threshold.

Fix: set `emergency_capable: true` on every device that should respond to `ensure_safety [emergency]`.

---

## Verifying your configuration

Use the explainability endpoint to inspect how the resolver scores your devices for a given intent:

```bash
curl -sk https://<hub-ip>:47200/v1/intents/bedtime_routine/explain \
  -H "Authorization: Bearer <token>" \
  --cacert certs/ca.crt | python3 -m json.tool
```

The response structure:

```json
{
  "devices_evaluated": 38,
  "devices_included": 15,
  "devices_excluded": 23,
  "included": [
    {
      "device_id": "light-zone3-01",
      "device_tags": ["light", "climate", "children_arrival", "wiz"],
      "score": 36.0,
      "score_breakdown": {
        "tag_overlap": 20.0,
        "matched_tags": ["climate", "light"],
        "actuator_match": 16.0,
        "matched_actuators": ["set_brightness", "turn_off"]
      }
    }
  ],
  "excluded": [
    {
      "device_id": "sensor-motion-01",
      "device_tags": ["emergency", "motion", "security", "sensor"],
      "reason": "required specific tags {'blinds', 'smart-plug'} not in device tags {...}"
    }
  ]
}
```

The `reason` field on excluded devices tells you exactly which tags are missing. If a device appears in `excluded` for an intent where you expect it to participate:

1. Read the `reason` field — it lists the specific missing tags
2. Add those tags to the device's manifest
3. Re-register the device (see section below)
4. Verify with the explainability endpoint that it now appears in `included`

---

## Updating a device manifest

Re-registration overwrites the existing manifest with the new tag set:

```bash
curl -s -X POST https://<hub-ip>:47200/v1/devices/register \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  --cacert certs/ca.crt \
  -d '{
    "device_id": "light-zone3-01",
    "tags": ["light", "smart-plug"],
    "actuators": [
      { "type": "turn_on" },
      { "type": "turn_off" },
      { "type": "set_brightness" }
    ],
    "emergency_capable": false
  }'
```

After updating, verify with the explainability endpoint that the device now appears in `included` for the target intent.

---

*DoSync Protocol · Apache 2.0 · github.com/giulianireg-spec/dosync-protocol*
