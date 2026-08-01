"""Signed heartbeats for hardware that cannot do TLS.

A door sensor that runs for a year on a coin cell has to wake, say "I am alive",
and sleep again in milliseconds. A TLS handshake costs it more battery than a
month of operation — the problem is not that TLS is slow, it is that it does not
fit. Requiring it puts the hardware floor at "TLS-capable", which excludes the
cheapest and most numerous tier of IoT.

**Authenticity does not require TLS.** TLS gives channel confidentiality and
channel authenticity; a heartbeat needs neither. It needs MESSAGE authenticity,
and an HMAC-SHA256 of 32 bytes fits comfortably on an 8-bit MCU.

What is given up, stated plainly because the threat model is a published document
here and not a footnote: **confidentiality**. Over plain HTTP the `device_id`,
the timestamp and any `report` travel readable. What is kept is the property that
matters for this endpoint — nobody can forge or replay a heartbeat.

Why that is the right trade for a heartbeat specifically, and would NOT be for an
action (panel, Sosa and Benítez):

  * A heartbeat is a POSITIVE SIGNAL ONLY. It marks a device reachable and never
    marks one unreachable. A forged heartbeat cannot switch anything on; the
    worst it does is make a dead device look healthy.
  * Which is exactly the attack that matters: **suppression, not control**. Keep
    replaying a captured heartbeat and a burnt-out smoke sensor reports healthy
    forever, blinding failure detection precisely when it is needed. That is why
    replay protection here is not optional.

Key derivation. Device tokens are stored HASHED — the hub cannot read them back,
which is deliberate: a compromised hub must not hand over every device's
credential in the clear. So the HMAC key is `sha256(device_token)`, which the
device derives itself and the hub already holds. An attacker who reads the
database gets the hashes, but those already authenticate a device today, so this
adds no exposure that did not exist.
"""
import hashlib
import hmac
import logging
import os
import time

log = logging.getLogger("dosync.lightweight")

#: How far a heartbeat's timestamp may be from the hub's clock. Narrow on
#: purpose: the window is exactly how long a captured heartbeat stays useful to
#: an attacker. Wide enough for cheap hardware with no NTP and some drift,
#: short enough that replay is a matter of seconds, not hours.
DEFAULT_SKEW_S = 120

#: Signatures already accepted, so one cannot be used twice inside the window.
#: Bounded by the window itself — entries older than the skew are unusable
#: anyway, so the set is pruned rather than grown.
_seen: dict[str, float] = {}


class SignatureError(ValueError):
    """A heartbeat that cannot be trusted, with the reason.

    Distinct exception rather than a bare False so the endpoint can answer 401
    with something an integrator can act on: "your clock is off by 400 seconds"
    and "your signature does not match" are very different problems and both
    look like "rejected" from outside.
    """


def is_enabled() -> bool:
    """Whether this deployment accepts unencrypted signed heartbeats.

    Off by default and deliberately so (panel, Ferreyra): a hub that starts
    accepting messages over an unencrypted channel because somebody plugged in a
    cheap sensor — without the operator choosing that — is wrong even when it is
    safe.
    """
    return os.environ.get("DOSYNC_LIGHTWEIGHT_HEARTBEAT", "").lower() in (
        "1", "true", "yes")


def signing_key(device_token: str) -> str:
    """The HMAC key a device derives from its provisioning token.

    Published as a function rather than described in prose so a firmware author
    has something unambiguous to reproduce.
    """
    return hashlib.sha256(device_token.encode()).hexdigest()


def canonical_message(device_id: str, timestamp: int, report_json: str = "") -> str:
    """Exactly what gets signed, in exactly this order.

    A canonical form matters more than its shape: hub and device must build the
    same string byte for byte, and any ambiguity here is a bug that presents as
    "signature does not match" with no way to tell why. Fields are joined with a
    character that cannot appear in a device_id.
    """
    return f"{device_id}\n{timestamp}\n{report_json}"


def sign(key_hex: str, device_id: str, timestamp: int, report_json: str = "") -> str:
    """Produce the signature a device sends. Also used by the tests, so what is
    verified is what is documented."""
    return hmac.new(bytes.fromhex(key_hex),
                    canonical_message(device_id, timestamp, report_json).encode(),
                    hashlib.sha256).hexdigest()


def verify(device_id: str, timestamp: int, signature: str, token_hash: str,
           report_json: str = "", skew_s: int = None, now: float = None) -> None:
    """Raise `SignatureError` unless this heartbeat is authentic and fresh.

    Three checks, and the order is not arbitrary — the cheapest and most
    diagnostic first, so a device with a wrong clock is told about its clock
    rather than about its key.
    """
    skew = DEFAULT_SKEW_S if skew_s is None else skew_s
    now = time.time() if now is None else now

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise SignatureError("timestamp must be an integer number of seconds")

    drift = abs(now - ts)
    if drift > skew:
        raise SignatureError(
            f"timestamp is {int(drift)}s from this hub's clock (limit {skew}s). "
            f"The device's clock is wrong, or this message was captured earlier.")

    expected = sign(token_hash, device_id, ts, report_json)
    # Constant-time: a comparison that returns early leaks how much of the
    # signature was right, and a leak of one byte at a time is a signature
    # recovered in a few hundred attempts.
    if not hmac.compare_digest(expected, str(signature)):
        raise SignatureError("signature does not match this device's key")

    # Replay: within the window the signature is valid forever, and a heartbeat
    # is exactly the thing an attacker replays to keep a dead device looking
    # alive. Same signature twice is refused.
    _prune(now, skew)
    if signature in _seen:
        raise SignatureError(
            "this heartbeat was already accepted — a signature is single-use "
            "within its window")
    _seen[signature] = now


def _prune(now: float, skew: int) -> None:
    """Drop signatures too old to be replayable. The set cannot grow without
    bound, because anything older than the window fails the clock check anyway."""
    cutoff = now - skew - 1
    for sig in [s for s, seen_at in _seen.items() if seen_at < cutoff]:
        _seen.pop(sig, None)


def reset() -> None:
    """Clear replay state. For tests; production has no reason to call it."""
    _seen.clear()
