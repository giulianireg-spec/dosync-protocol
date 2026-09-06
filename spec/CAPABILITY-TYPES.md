# DoSync Protocol — Capability Type Vocabulary

**Version:** 0.1
**Status:** Active
**Location:** `spec/CAPABILITY-TYPES.md`

---

## Why this document exists

Since 4 September 2026 a device takes part in an intent because of what it
**declares it can do**, not because of a tag someone wrote. That made the
contents of `type` load-bearing, and nothing described what belongs there.

The measurement that forced this: a motion detector was excluded from every
alert because it declared `motion_detected` while the intent asked for
`motion`. Both are reasonable names. Neither was wrong. There was simply no
document saying which one to use.

A manufacturer asking "what do I write in `type`?" had no answer, and "whatever
you like" guarantees nothing matches.

---

## The rule

**A type names what the device measures or does, never the shape of the data.**

`boolean`, `integer`, `float` and `string` describe how a value is encoded.
That is what a `DataSchema` is for. A type of `boolean` says the device reports
true or false and says nothing about whether it detects motion, smoke, or an
open door — and a resolver deciding participation cannot tell those apart.

Measured: a particulate counter and a pressure gauge both declaring `number`
made every numeric sensor in the industrial corpus qualify for any alert.
Precision fell from 0.73 to 0.60. **A label that fits everything selects
nothing.**

---

## This is not the only vocabulary

A sensor **type** says what a device measures. An **event identifier** says what
happened. They look alike and they are consumed by different parts of the hub:

| | Example | Read by |
|---|---|---|
| Sensor type | `motion` | The resolver, deciding participation |
| Event identifier | `motion_detected` | The policy engine, weighting a trigger |

The deployment's PIR sensor declared `motion_detected` as its sensor type. That
name exists in this project — `motion_detected at night → weight 1.8 (possible
intrusion)` — but as an event, not as a measurement. The device was excluded
from every alert asking for `motion`, and nothing flagged the mix-up because
nothing knew the two vocabularies were distinct.

Both are declared in the manifest, in different fields:

```
sensors:  [{id: "motion", type: "motion"}]        ← this document
events:   [{id: "motion_detected", severity: "alert"}]   ← EventSpec
```

**If you are declaring what a device can measure, it is a sensor and this
document applies. If you are declaring a condition the device will report when
it occurs, it is an event, and its identifier is yours to choose — the policy
engine matches it against whatever your policies name.**

Event identifiers are deliberately not standardised here. A shared vocabulary is
needed exactly where **the party declaring a capability is not the party writing
the resolution** — and for events those are the same person: an operator writes
a policy against the devices they have, so both sides of that match are authored
together.

Sensor types are not. A device is declared by its manufacturer or by a bridge,
and matched against an intent someone else defined. That holds even for
domain-specific intents the operator wrote themselves, because the devices
answering them still came from elsewhere — which is precisely how the industrial
and clinical corpora ran into this.

---

## Sensor types

Aligned with Home Assistant's `device_class` where an equivalent exists, and
that alignment is deliberate. The HA bridge reads `device_class` verbatim, so
any deployment with a Home Assistant behind it will produce these names without
anyone choosing them. Inventing a parallel vocabulary would mean two names for
the same thing inside one hub.

### Presence and movement

| Type | Measures | Typical device |
|---|---|---|
| `motion` | Movement detected in an area | PIR sensor, radar sensor |
| `occupancy` | Whether a space is occupied | mmWave sensor, seat sensor |
| `presence` | Whether a known person or device is present | Phone tracker, BLE beacon |

### Openings

| Type | Measures | Typical device |
|---|---|---|
| `door` | Door open or closed | Reed switch, contact sensor |
| `window` | Window open or closed | Contact sensor |
| `opening` | A generic opening, where door and window do not fit | Hatch, gate, lid |
| `lock` | Locked or unlocked state | Smart lock's own state sensor |

### Environment

| Type | Measures | Unit usually |
|---|---|---|
| `temperature` | Air or surface temperature | °C |
| `humidity` | Relative humidity | % |
| `pressure` | Atmospheric or line pressure | hPa, kPa |
| `illuminance` | Light level | lx |
| `sound_level` | Noise level | dB |

### Hazards

| Type | Measures | Notes |
|---|---|---|
| `smoke` | Smoke detected | |
| `gas` | Combustible gas detected | Say which gas in the description |
| `carbon_monoxide` | CO detected | Distinct from `gas`: different response |
| `moisture` | Water where it should not be | Leak sensor, flood sensor |
| `heat` | Excessive heat, distinct from a temperature reading | Rate-of-rise detector |

### Energy and machines

| Type | Measures | Unit usually |
|---|---|---|
| `power` | Instantaneous power draw | W |
| `energy` | Cumulative consumption | kWh |
| `current` | Electrical current | A |
| `voltage` | Voltage | V |
| `battery` | Charge remaining | % |
| `running` | Whether a machine is operating | |
| `problem` | Whether a device reports a fault | |

### Shapes — allowed, but they decide nothing

| Type | When it is honest |
|---|---|
| `boolean` | The device genuinely reports an unnamed true/false and nothing more is known |
| `integer`, `float` | A numeric reading whose meaning the source does not publish |
| `string` | Free text, such as a media title or a status label |

**A device declaring only these will not be selected by any intent that asks for
a meaning.** That is correct behaviour, not a gap: the hub cannot decide that an
unnamed boolean belongs in a fire response. If a bridge is producing these, the
question is whether the upstream knows more — Home Assistant usually does, and
publishes it as `device_class`.

---

## Actuator types

| Type | Does | Reversible |
|---|---|---|
| `turn_on`, `turn_off` | Power state | Yes |
| `set_brightness` | Light level, 0–100 | Yes |
| `set_color`, `set_color_temp` | Light colour | Yes |
| `set_temperature` | Target temperature | Yes |
| `lock`, `unlock` | Physical access | **Depends on the hardware** |
| `open`, `close`, `set_position` | Covers, blinds, gates | Usually |
| `alarm` | Sound or flash an alarm | Yes |
| `notify`, `call`, `display` | Deliver a message | **No — a sent message cannot be unsent** |
| `start`, `stop`, `pause` | Long-running operations | Depends |

The reversibility column is descriptive today. Whether a manifest should declare
it — so that an agent knows which actions stay done — is an open question,
recorded in the project's debt register.

---

## Unknown types are allowed

A type this document does not list is **not an error**. The HA bridge reads
`device_class` verbatim precisely so that a class DoSync has never heard of
arrives as itself rather than being flattened to `boolean`, and nothing here
needs updating when Home Assistant adds one.

An unrecognised type simply will not match an intent that does not ask for it.
That is the same outcome as `boolean`, with one difference that matters: it
carries information a human or a future version can act on.

---

## Known limits

**Matching is exact.** A device declaring `temp` will not match an intent asking
for `temperature`. There is no synonym table, and adding one was considered and
rejected: this project has rewritten five word lists in a week and each had
holes. The limit is documented rather than papered over, and becomes worth
solving when a real deployment hits it.

**The alignment with Home Assistant is de facto.** It was adopted because the
bridge reads those names, not by a decision to make HA's catalogue the
protocol's own. It is the most widely used vocabulary in the domestic space,
which is an argument in its favour, but the industrial and clinical types above
have no such backing and are proposals.

**Sensor types carry no units here.** `temperature` says what is measured, not
whether it is Celsius. Units belong in a data schema, which the manifest does
not yet have — the gap that made `number` ambiguous in the first place.
