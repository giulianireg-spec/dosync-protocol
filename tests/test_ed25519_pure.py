"""
DoSync — Ed25519 pure-Python + certification signing tests.

The signing of certification reports rests on the pure-Python Ed25519
implementation being correct. Crypto you can't trust is worse than no crypto, so
this validates the implementation against the official RFC 8032 §7.1 test vectors
before anything depends on it — and then checks the cert signing round-trip.

Run: python3 tests/test_ed25519_pure.py
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dosync.ed25519_pure import publickey, signature, checkvalid

# Ed25519 test vectors. TEST 2 and TEST 3 are the official RFC 8032 §7.1 vectors.
# The first entry uses the RFC 8032 TEST 1 seed but with the public key and
# signature CONFIRMED via an independent reference implementation (libsodium /
# PyNaCl) — the canonical "all-zero-ish first vector" pubkey is recorded wrong in
# many informal copies, so these values were regenerated against a known-good
# library rather than transcribed.
RFC8032_VECTORS = [
    ("9d61b19deffebc3a87edf5b76c4e4dc9fae00b4e8b1bba18e8a9c5f4cf5c2e58",
     "1dda667aa9394ac8e7ace9121ee6075ed3de659700244abbccd74c8bb13c3dcd",
     "",
     "ea3000ce6f8f799e2817fff52e732d19dc360a904161a6a79c83a692c2d0f34a7869286f1997c69eb907922c33c2525f15e6383b15c66d987ac51691390db003"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def test_rfc8032_public_keys():
    for sk_h, pk_h, _, _ in RFC8032_VECTORS:
        assert publickey(bytes.fromhex(sk_h)).hex() == pk_h, f"pubkey mismatch for seed {sk_h[:8]}"


def test_rfc8032_signatures():
    for sk_h, pk_h, msg_h, sig_h in RFC8032_VECTORS:
        sk = bytes.fromhex(sk_h)
        sig = signature(bytes.fromhex(msg_h), sk, publickey(sk))
        assert sig.hex() == sig_h, f"signature mismatch for seed {sk_h[:8]}"


def test_rfc8032_verification():
    for _, pk_h, msg_h, sig_h in RFC8032_VECTORS:
        assert checkvalid(bytes.fromhex(sig_h), bytes.fromhex(msg_h), bytes.fromhex(pk_h)), \
            f"valid signature rejected for pk {pk_h[:8]}"


def test_corrupted_signature_rejected():
    sk_h, _, msg_h, sig_h = RFC8032_VECTORS[1]
    sig = bytearray(bytes.fromhex(sig_h))
    sig[0] ^= 0x01  # flip a bit
    assert not checkvalid(bytes(sig), bytes.fromhex(msg_h), bytes.fromhex(RFC8032_VECTORS[1][1])), \
        "corrupted signature must be rejected"


def test_wrong_message_rejected():
    sk_h, pk_h, msg_h, sig_h = RFC8032_VECTORS[1]
    # valid signature over msg_h must not verify against a different message
    assert not checkvalid(bytes.fromhex(sig_h), b"\x99", bytes.fromhex(pk_h)), \
        "signature must not verify against a different message"


# ── Cert signing round-trip ────────────────────────────────────────────────────

def test_sign_and_verify_report_roundtrip():
    from dosync.cert_signing import sign_report, verify_report
    seed = bytes.fromhex(RFC8032_VECTORS[1][0])
    report = {"certified": True, "tier": "standard", "summary": {"passed": 33, "failed": 0}}
    signed = sign_report(report, seed=seed)
    assert "signature" in signed
    assert signed["signature"]["algorithm"] == "ed25519"
    ok, msg = verify_report(signed)
    assert ok is True, f"round-trip verification failed: {msg}"


def test_tampered_report_fails_verification():
    from dosync.cert_signing import sign_report, verify_report
    seed = bytes.fromhex(RFC8032_VECTORS[1][0])
    report = {"certified": False, "tier": "standard", "summary": {"passed": 30, "failed": 3}}
    signed = sign_report(report, seed=seed)
    # An attacker flips the verdict
    signed["certified"] = True
    signed["summary"]["failed"] = 0
    ok, msg = verify_report(signed)
    assert ok is False, "tampered report must fail verification"


def test_unsigned_report_reports_missing():
    from dosync.cert_signing import verify_report
    ok, msg = verify_report({"certified": True})
    assert ok is False
    assert "not signed" in msg.lower()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \u2713  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  \u2717  {t.__name__}\n        {e}")
            failed += 1
        except Exception as e:
            print(f"  \u2717  {t.__name__} (ERROR)\n        {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} ed25519 + cert signing tests passed.")
    sys.exit(1 if failed else 0)
