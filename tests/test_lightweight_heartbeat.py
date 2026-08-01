"""H4 — heartbeats from hardware that cannot do TLS (2026-08-01).

Kim, on the July panel: a door sensor running a year on a coin cell must wake,
say "I am alive" and sleep in milliseconds. A TLS handshake costs it more battery
than a month of operation — the problem is not speed, it is that TLS does not
fit. Requiring it puts DoSync's hardware floor at "TLS-capable" and excludes the
cheapest tier of IoT.

The item warned that every option trades security for reach and that the trade
must be chosen, not defaulted into. The panel found the trade is narrower than
stated: **authenticity does not require TLS**. What is given up is
confidentiality; what is kept is the property that matters here.

Why it matters here and would NOT for an action: a heartbeat is positive signal
only — it marks a device reachable and never marks one unreachable — so a forged
heartbeat cannot switch anything on. The attack is SUPPRESSION: replay a captured
heartbeat and a burnt-out smoke sensor reports healthy forever, blinding failure
detection exactly when it is needed. That is why replay protection is not
optional.
"""
import time

import pytest

from dosync.lightweight import (DEFAULT_SKEW_S, SignatureError, canonical_message,
                                is_enabled, reset, sign, signing_key, verify)

KEY = signing_key("a-device-provisioning-token")


@pytest.fixture(autouse=True)
def clean():
    reset()
    yield
    reset()


# ── The primitive ───────────────────────────────────────────────────────────

def test_a_valid_signature_is_accepted():
    ts = int(time.time())
    verify("sensor-01", ts, sign(KEY, "sensor-01", ts), KEY)


def test_the_key_is_the_stored_hash_not_the_token():
    """Device tokens are stored HASHED and the hub cannot read them back, which
    is deliberate — a compromised hub must not hand over every device's
    credential. So the key is sha256(token): the device derives it, the hub
    already holds it, and the secret never has to be recoverable."""
    from dosync.auth import hash_token
    assert signing_key("a-device-provisioning-token") == \
        hash_token("a-device-provisioning-token")


def test_a_forged_signature_is_refused():
    ts = int(time.time())
    with pytest.raises(SignatureError):
        verify("sensor-01", ts, "0" * 64, KEY)


def test_a_signature_for_another_device_is_refused():
    """The device_id is inside the signed message, so a heartbeat cannot be
    lifted from one device onto another."""
    ts = int(time.time())
    sig = sign(KEY, "sensor-01", ts)
    with pytest.raises(SignatureError):
        verify("sensor-02", ts, sig, KEY)


def test_a_report_cannot_be_altered_in_flight():
    ts = int(time.time())
    sig = sign(KEY, "sensor-01", ts, '{"battery":80}')
    with pytest.raises(SignatureError):
        verify("sensor-01", ts, sig, KEY, report_json='{"battery":10}')


# ── Replay: the attack that actually matters ────────────────────────────────

def test_the_same_heartbeat_cannot_be_accepted_twice():
    """Without this, capturing one heartbeat keeps a dead device alive forever
    — which is the whole point of attacking a liveness signal."""
    ts = int(time.time())
    sig = sign(KEY, "sensor-01", ts)
    verify("sensor-01", ts, sig, KEY)
    with pytest.raises(SignatureError) as e:
        verify("sensor-01", ts, sig, KEY)
    assert "already accepted" in str(e.value)


def test_a_stale_timestamp_is_refused():
    """The window IS how long a captured heartbeat stays useful, so it is narrow
    on purpose."""
    ts = int(time.time()) - (DEFAULT_SKEW_S + 60)
    with pytest.raises(SignatureError) as e:
        verify("sensor-01", ts, sign(KEY, "sensor-01", ts), KEY)
    assert "clock" in str(e.value)


def test_a_clock_error_says_so_rather_than_blaming_the_key():
    """Cheap hardware has no NTP and drifts. 'Your clock is off by 400 seconds'
    and 'your signature is wrong' are different problems, and both look like
    'rejected' from outside."""
    ts = int(time.time()) - 400
    with pytest.raises(SignatureError) as e:
        verify("sensor-01", ts, sign(KEY, "sensor-01", ts), KEY)
    assert "clock" in str(e.value) and "signature does not match" not in str(e.value)


def test_replay_memory_does_not_grow_without_bound():
    """Entries older than the window are unusable anyway, so they are pruned
    rather than accumulated — a liveness endpoint receives a lot of these."""
    from dosync import lightweight

    old = time.time() - (DEFAULT_SKEW_S + 600)
    for i in range(50):
        lightweight._seen[f"old-{i}"] = old
    ts = int(time.time())
    verify("sensor-01", ts, sign(KEY, "sensor-01", ts), KEY)
    assert len(lightweight._seen) < 50


# ── Canonical form ──────────────────────────────────────────────────────────

def test_the_canonical_message_is_stable():
    """A firmware author has to reproduce this byte for byte; any drift here
    presents as 'signature does not match' with no way to tell why."""
    assert canonical_message("d", 123, "{}") == "d\n123\n{}"
    assert canonical_message("d", 123) == "d\n123\n"


# ── Opt-in ──────────────────────────────────────────────────────────────────

def test_it_is_off_unless_the_deployment_asks(monkeypatch):
    """Ferreyra, on the panel: a hub that starts accepting messages over an
    unencrypted channel because somebody plugged in a cheap sensor — without the
    operator choosing it — is wrong even when it is safe."""
    monkeypatch.delenv("DOSYNC_LIGHTWEIGHT_HEARTBEAT", raising=False)
    assert is_enabled() is False
    monkeypatch.setenv("DOSYNC_LIGHTWEIGHT_HEARTBEAT", "true")
    assert is_enabled() is True


# ── The endpoint ────────────────────────────────────────────────────────────

@pytest.fixture
def hub_client(monkeypatch):
    from fastapi.testclient import TestClient

    import dosync.server as srv
    from dosync.models import (CapabilityManifest, CertTier, DeviceCategory,
                               SensorSpec)

    monkeypatch.setenv("DOSYNC_LIGHTWEIGHT_HEARTBEAT", "true")
    srv.hub.registry.register(CapabilityManifest(
        device_id="mcu-door", device_name="Door sensor", manufacturer="acme",
        model="d1", firmware="1", category=DeviceCategory.SENSOR,
        tags=["security"],
        sensors=[SensorSpec(id="open", type="boolean", description="")],
        events=[], actuators=[], emergency_capable=False,
        cert_tier=CertTier.BASIC))
    client = TestClient(srv.app)
    body = client.post("/v1/devices/provision",
                       json={"device_id": "mcu-door"}).json()
    token = body.get("device_token") or body.get("token")
    return srv, client, signing_key(token)


def test_a_signed_heartbeat_is_accepted_without_a_bearer_token(hub_client):
    """Deliberately not behind require_auth: the entire point is that the caller
    cannot present a bearer over an encrypted channel."""
    srv, client, key = hub_client
    ts = int(time.time())
    r = client.post("/v1/heartbeat/signed", json={
        "device_id": "mcu-door", "timestamp": ts,
        "signature": sign(key, "mcu-door", ts)})

    assert r.status_code == 200
    assert r.json()["acknowledged"] is True
    assert "not encrypted" in r.json()["note"], \
        "the response must not let anyone believe this channel is private"


def test_the_channel_is_recorded_on_the_device(hub_client):
    """Aguirre's non-negotiable: if a device reporting over an unencrypted
    channel looks identical to one on mTLS, the protocol is hiding a real
    difference."""
    srv, client, key = hub_client
    ts = int(time.time())
    client.post("/v1/heartbeat/signed", json={
        "device_id": "mcu-door", "timestamp": ts,
        "signature": sign(key, "mcu-door", ts)})

    assert srv.hub.health.snapshot("mcu-door")["report_channel"] == "signed_plaintext"


def test_a_replayed_heartbeat_is_rejected_by_the_endpoint(hub_client):
    srv, client, key = hub_client
    ts = int(time.time())
    body = {"device_id": "mcu-door", "timestamp": ts,
            "signature": sign(key, "mcu-door", ts)}
    assert client.post("/v1/heartbeat/signed", json=body).status_code == 200
    assert client.post("/v1/heartbeat/signed", json=body).status_code == 401


def test_an_unprovisioned_device_is_told_what_to_do(hub_client):
    """A device with no token cannot sign anything, and the error should say
    that rather than reporting a signature mismatch."""
    srv, client, key = hub_client
    from dosync.models import (CapabilityManifest, CertTier, DeviceCategory)
    srv.hub.registry.register(CapabilityManifest(
        device_id="mcu-notoken", device_name="X", manufacturer="a", model="b",
        firmware="1", category=DeviceCategory.SENSOR, tags=["x"], sensors=[],
        events=[], actuators=[], emergency_capable=False,
        cert_tier=CertTier.BASIC))
    ts = int(time.time())
    r = client.post("/v1/heartbeat/signed", json={
        "device_id": "mcu-notoken", "timestamp": ts, "signature": "x" * 64})
    assert r.status_code == 403 and "provision" in r.json()["detail"]


def test_an_unknown_device_is_refused(hub_client):
    """A heartbeat asserts that a KNOWN device is alive, not that one exists."""
    srv, client, key = hub_client
    ts = int(time.time())
    r = client.post("/v1/heartbeat/signed", json={
        "device_id": "never-registered", "timestamp": ts, "signature": "x" * 64})
    assert r.status_code == 404


def test_the_endpoint_is_absent_unless_enabled(hub_client, monkeypatch):
    srv, client, key = hub_client
    monkeypatch.setenv("DOSYNC_LIGHTWEIGHT_HEARTBEAT", "false")
    ts = int(time.time())
    r = client.post("/v1/heartbeat/signed", json={
        "device_id": "mcu-door", "timestamp": ts,
        "signature": sign(key, "mcu-door", ts)})
    assert r.status_code == 404
    assert "not enabled" in r.json()["detail"]
