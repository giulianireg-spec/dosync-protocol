"""
DoSync — Certification report signing & verification.
=====================================================

Signs a certification report with Ed25519 so a third party can confirm the report
was not ALTERED after it was issued. Uses the pure-Python Ed25519 (zero deps).

WHAT THE SIGNATURE PROVES — AND DOESN'T
---------------------------------------
A valid signature proves the report content is byte-for-byte what the holder of
the signing key produced: no third party edited "failed" into "passed" or changed
the host. It does NOT prove the tests reflect production, nor that the operator
didn't tune the hub for the test run — the operator holds the key and controls the
environment. The signature defends against tampering by others, not against
self-declaration. The report's own `attestation` block states this in plain words.

KEY MANAGEMENT
--------------
The signing key is a 32-byte seed stored at the path given by DOSYNC_CERT_KEY
(default: ~/.dosync/cert_signing_key). If absent, `sign_report` can generate one.
The PUBLIC key is embedded in every signed report so a verifier needs nothing but
the report itself (and this code, or any Ed25519 library).

The key identifies the ISSUER, not an authority. Anyone can generate a key and
sign their own reports — which is exactly right for self-certification. Trust in a
key is a social fact (you know whose key it is), established outside this protocol.
"""

from __future__ import annotations
import json
import os
import hashlib
from pathlib import Path

from .ed25519_pure import publickey, signature, checkvalid


def _key_path() -> Path:
    return Path(os.environ.get("DOSYNC_CERT_KEY", str(Path.home() / ".dosync" / "cert_signing_key")))


def load_or_create_key() -> bytes:
    """Return the 32-byte signing seed, creating one if it does not exist."""
    path = _key_path()
    if path.exists():
        seed = path.read_bytes()
        if len(seed) != 32:
            raise ValueError(f"signing key at {path} is not 32 bytes")
        return seed
    seed = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return seed


def _canonical(report_dict: dict) -> bytes:
    """Canonical byte representation of the report for signing/verifying.

    The `signature` block itself is excluded — you cannot sign a document that
    contains its own signature. Everything else is serialized deterministically
    (sorted keys, no whitespace) so signer and verifier hash identical bytes.
    """
    payload = {k: v for k, v in report_dict.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_report(report_dict: dict, seed: bytes | None = None) -> dict:
    """Return a copy of the report with a `signature` block attached.

    The block carries the algorithm, the public key (hex), and the signature
    (hex), so the report is self-contained for verification.
    """
    if seed is None:
        seed = load_or_create_key()
    pk = publickey(seed)
    msg = _canonical(report_dict)
    sig = signature(msg, seed, pk)
    signed = dict(report_dict)
    signed["signature"] = {
        "algorithm": "ed25519",
        "public_key": pk.hex(),
        "signature": sig.hex(),
        "signed_fields": "all fields except 'signature'",
        "note": (
            "Proves this report was not altered after issuance by the holder of "
            "the public key. Does not prove production parity or independent review "
            "— see the 'attestation' block."
        ),
    }
    return signed


def verify_report(report_dict: dict) -> tuple[bool, str]:
    """Verify a signed report. Returns (ok, message).

    Recomputes the canonical bytes over everything except the signature block and
    checks the Ed25519 signature against the embedded public key.
    """
    sig_block = report_dict.get("signature")
    if not sig_block:
        return False, "report is not signed (no 'signature' block)"
    if sig_block.get("algorithm") != "ed25519":
        return False, f"unsupported algorithm: {sig_block.get('algorithm')}"
    try:
        pk = bytes.fromhex(sig_block["public_key"])
        sig = bytes.fromhex(sig_block["signature"])
    except (KeyError, ValueError) as e:
        return False, f"malformed signature block: {e}"

    msg = _canonical(report_dict)
    if checkvalid(sig, msg, pk):
        return True, f"signature valid — issued by key {pk.hex()[:16]}…"
    return False, "signature INVALID — report was altered or key mismatch"
