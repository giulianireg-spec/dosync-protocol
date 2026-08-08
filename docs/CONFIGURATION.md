# Configuration reference

Every `DOSYNC_*` setting the hub reads, with the default it uses when unset.

**Generated from the source.** Do not edit by hand — run
`python3 -m dosync.config_reference --write`. A test fails if this file and the
code disagree, because a hand-maintained table is how a project ends up with one
fact in two places, and this one has done that four times already.

**Nothing here is required.** A hub with no configuration at all starts, requires
a token, keeps an audit chain, checkpoints daily, and scans for devices. These
settings exist for deployments whose needs differ from that, not as a checklist.


## Running the hub

| Setting | Default |
|---|---|
| `DOSYNC_HOST` | `127.0.0.1` |
| `DOSYNC_PORT` | `47200` |
| `DOSYNC_DB` | _unset_ |
| `DOSYNC_DB_PATH` | _unset_ |
| `DOSYNC_CERTIFY` | _unset_ |
| `DOSYNC_HUB_ROLE` | `primary` |
| `DOSYNC_HUB_URL` | `http://localhost:47200` |
| `DOSYNC_PRIMARY_URL` | _unset_ |
| `DOSYNC_STATUS_SCOPE` | `all` |

## Access

| Setting | Default |
|---|---|
| `DOSYNC_AUTH` | _unset_ |
| `DOSYNC_TOKEN` | _unset_ |
| `DOSYNC_DEVICE_AUTH` | `permissive` |
| `DOSYNC_LIGHTWEIGHT_HEARTBEAT` | _unset_ |
| `DOSYNC_DEMO_TOKEN` | _unset_ |
| `DOSYNC_CERTS_DIR` | _unset_ |
| `DOSYNC_CA_CERT` | _unset_ |
| `DOSYNC_CERT_KEY` | `str(Path.home(` |

## Audit and evidence

| Setting | Default |
|---|---|
| `DOSYNC_ASSURANCE` | `standard` |
| `DOSYNC_CHECKPOINT_INTERVAL` | `86400` |
| `DOSYNC_CHECKPOINT_EXPORT_DIR` | _unset_ |
| `DOSYNC_CHECKPOINT_EXPORT_EXTERNAL` | _unset_ |
| `DOSYNC_AUDIT_HEAD_EVERY` | `25` |
| `DOSYNC_AUDIT_MAX_LIVE` | `10000` |

## Devices and adapters

| Setting | Default |
|---|---|
| `DOSYNC_DECLARATIVE_DIR` | `declarative` |
| `DOSYNC_BLE_ENABLED` | `true` |
| `DOSYNC_MAVLINK_ENABLED` | `false` |
| `DOSYNC_HA_EXCLUDE_ENTITIES` | _unset_ |
| `DOSYNC_HA_IMPORT_HOUSEKEEPING` | _unset_ |
| `DOSYNC_MQTT_BROKER` | `localhost` |
| `DOSYNC_MQTT_PORT` | `1883` |
| `DOSYNC_MQTT_USER` | _unset_ |
| `DOSYNC_MQTT_PASSWORD` | _unset_ |
| `DOSYNC_MQTT_PREFIX` | `dosync` |
| `DOSYNC_MQTT_QOS` | `1` |
| `DOSYNC_MQTT_SECRET` | _unset_ |

## Behaviour under load and failure

| Setting | Default |
|---|---|
| `DOSYNC_INTENT_TIMEOUT` | `5.0" if intent.urgency.value == "emergency" else "10.0` |
| `DOSYNC_UNREACHABLE_TTL` | `1800` |
| `DOSYNC_FAILURE_THRESHOLD` | `3` |
| `DOSYNC_STATE_REFRESH_INTERVAL` | `60` |
| `DOSYNC_CLAIM_MIN_URGENCY` | `emergency` |
| `DOSYNC_EMERGENCY_CLAIM_GRACE` | `3` |
| `DOSYNC_EMERGENCY_CLAIM_MAX_HOLD` | `60` |
| `DOSYNC_VALIDATE_PARAMS` | `true` |
| `DOSYNC_EMERGENCY_CONTACT` | _unset_ |
| `DOSYNC_RESOLVER_URL` | _unset_ |
| `DOSYNC_RESOLVER_CA_CERT` | _unset_ |
