# DoSync Protocol — Node.js Reference Implementation

A second, independent implementation of the DoSync Protocol in Node.js.

This implementation was built from the protocol specification, independently of the Python reference implementation. Its purpose is to demonstrate that the spec is clear enough to be implemented by third parties.

**Python hub:** port 47200  
**Node.js hub:** port 47201

## Certification status

| Tier | Status |
|---|---|
| Basic | ✅ Passes all 6 tests |
| Standard | 🔄 In progress |
| Emergency | 🔄 Planned |

## Quick start

```bash
cd implementations/dosync-node
npm install
npm start
# Hub running on http://localhost:47201
```

## Run certification

```bash
# From repo root
python3 certify.py --host localhost --port 47201 --tier basic
```

## Architecture

This implementation uses [Fastify](https://fastify.dev) for the HTTP layer.  
The registry, resolver, and audit log are in-memory — sufficient for Basic tier certification.

The resolver uses the same tag-matching algorithm as the Python `CapabilityMatchingResolver`:
- Tag overlap: 10 points per matching tag
- Emergency bonus: 30 points for emergency-capable devices on emergency intents
- Location bonus: 15 points when context location matches a device tag

## What this proves

A conforming DoSync implementation requires:
1. A capability registry (device manifests)
2. A capability-based resolver (intent → device actions via tag matching)
3. A tamper-evident audit log (SHA-256 chained entries)
4. The standard REST endpoints defined in `DOSYNC-SPEC-v0.1.md`

No shared code with the Python implementation. Same protocol, different language.

---

*DoSync Protocol v0.1 · Apache 2.0*

## MQTT transport security

DoSync ships an MQTT transport adapter (`dosync/adapters/mqtt.py`) for devices that communicate via MQTT instead of HTTP. Activating it requires a running Mosquitto broker and proper authentication configuration.

### Quick setup (Raspberry Pi / Linux)

```bash
# 1. Install Mosquitto
sudo apt-get install -y mosquitto mosquitto-clients

# 2. Create credentials (replace passwords with your own)
sudo mosquitto_passwd -c /etc/mosquitto/passwd dosync-hub
sudo mosquitto_passwd    /etc/mosquitto/passwd dosync-device

# 3. Apply secure configuration
sudo cp config/mosquitto-secure.conf /etc/mosquitto/conf.d/dosync.conf
sudo systemctl restart mosquitto

# 4. Configure the hub service
sudo mkdir -p /etc/systemd/system/dosync.service.d
sudo tee /etc/systemd/system/dosync.service.d/mqtt.conf << EOF
[Service]
Environment="DOSYNC_MQTT_BROKER=localhost"
Environment="DOSYNC_MQTT_USER=dosync-hub"
Environment="DOSYNC_MQTT_PASSWORD=<your-password>"
Environment="DOSYNC_MQTT_SECRET=<your-registration-secret>"
EOF

# 5. Restrict file permissions (prevents credential exposure)
sudo chmod 600 /etc/systemd/system/dosync.service.d/mqtt.conf

# 6. Reload and restart
sudo systemctl daemon-reload && sudo systemctl restart dosync
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `DOSYNC_MQTT_BROKER` | — | Broker hostname. Set to enable MQTT transport. |
| `DOSYNC_MQTT_PORT` | 1883 | Broker port (8883 for TLS) |
| `DOSYNC_MQTT_USER` | — | Broker username |
| `DOSYNC_MQTT_PASSWORD` | — | Broker password |
| `DOSYNC_MQTT_SECRET` | — | Registration secret. Devices must include `{"dosync_secret": "<value>"}` in their register payload. |
| `DOSYNC_MQTT_PREFIX` | dosync | Topic prefix |

See `config/mosquitto-secure.conf` for a fully annotated broker configuration including optional TLS and per-device topic ACLs.

