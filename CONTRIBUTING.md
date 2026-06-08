# Contributing to DoSync Protocol

## Development workflow

### Before pushing to main

The CI pipeline runs automatically on every push. To avoid fixing bugs on the Pi after deploying, run the same checks locally before pushing:

```bash
# 1. Syntax check
find . -name "*.py" -not -path "*/venv/*" -not -path "*/__pycache__/*" \
  | xargs python3 -m py_compile
echo "✓ syntax OK"

# 2. Import check (catches NameErrors from renaming types)
PYTHONPATH=. python3 -c "
from dosync.models import Intent, IntentClass, Urgency, Severity, EventSpec, DeviceEvent
from dosync.hub import DoSyncHub, CapabilityMatchingResolver, ExternalResolver
from dosync.policies import PolicyEngine, BlockIntentPolicy, DeviceActuatorRateLimitPolicy
from dosync.adapters.mqtt import MQTTAdapter
print('✓ imports OK')
"

# 3. Resolver scoring tests
PYTHONPATH=. python3 tests/test_resolver_scoring.py

# 4. Full hub startup
PYTHONPATH=. python3 -c "from dosync.hub import DoSyncHub; DoSyncHub(db_path=':memory:'); print('✓ hub OK')"
```

### Renaming a type or moving a symbol

**Always** search all import sites before committing:

```bash
# Example: you renamed Urgency to Priority
grep -r "Urgency" --include="*.py" .
# Fix every file that shows up, then run the import check above
```

This pattern caused 3 separate production failures during the `Severity`/`Urgency` refactoring session. The CI import check now catches this automatically, but it's faster to fix locally.

### Deploying to the Pi

Changes always flow: **Mac → commit → push → Pi `git pull`**. Never apply changes directly on the Pi without committing from Mac first.

```bash
# Mac
git add -A && git commit -m "..." && git push origin main

# Pi
cd ~/dosync-protocol && git pull origin main && sudo systemctl restart dosync

# Verify
sudo journalctl -u dosync --since "30 seconds ago" | grep -i "error\|warning\|INFO  dosync.server"
```

If the Pi diverges: `git reset --hard origin/main`

### Running certification

```bash
DOSYNC_TOKEN=<your-token> \
DOSYNC_CA_CERT=~/dosync-protocol/certs/ca.crt \
python3 certify.py --host 192.168.100.109 --port 47200 --tier standard
# Expected: 32/32 CERTIFIED
```

---

## CI pipeline

`.github/workflows/ci.yml` runs on every push and PR:

| Step | What it catches |
|---|---|
| Syntax check | SyntaxError in any .py file |
| Import check | NameError from missing imports after type renames |
| Severity regression | EventSpec/DeviceEvent severity type invariants |
| Resolver scoring (8 tests) | Regression in tag matching, emergency bonus, location bonus |
| Hub startup | Import errors that only manifest at startup |
| Policy engine | bypass_on_emergency defaults, BlockIntentPolicy blocks emergency |

The CI does **not** run against physical hardware — it uses in-memory DB and simulated devices. Certify the running hub separately after deploying.

---

## Adding a new adapter

See [docs/ADAPTER-GUIDE.md](docs/ADAPTER-GUIDE.md) for the full guide.

Minimum implementation:

```python
from dosync.adapters import DoSyncAdapter
from dosync.models import ActionResult, DeviceAction, Urgency

class MyAdapter(DoSyncAdapter):
    @property
    def adapter_name(self) -> str:
        return "myadapter"

    async def execute(self, action: DeviceAction, urgency: Urgency) -> ActionResult:
        # translate DoSync action to device-native protocol
        return ActionResult(device_id=action.device_id, action=action.action,
                            success=True, response={})
```

Register in `server.py`:

```python
executor.register(MyAdapter())
```

---

## Specification changes

- `spec/DoSync-SPEC-v0.1.md` — full protocol spec. Update when adding new endpoints or changing data models.
- `spec/RESOLVER-SPEC-v0.3.md` — resolver interface. Update when changing BaseResolver or ExternalResolver wire format.
- `COMPATIBILITY.md` — update when making any breaking or additive changes.
- `spec/schemas/*.json` — JSON Schema for wire format. Update when models change.

---

## License

Apache 2.0. All contributions must be compatible with this license.
