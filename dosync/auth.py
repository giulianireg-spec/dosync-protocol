"""
DoSync — Authentication
=======================
A simple API key that protects the hub.

- Keys are generated with secrets.token_urlsafe(32)
- Only the SHA-256 of a token is stored, never the token itself
- The first start generates a key automatically and prints it once
- Further keys are created through the API or the CLI

Flujo:
    1. Hub starts → with no keys, it generates one and shows it ONCE
    2. Cliente incluye: Authorization: Bearer <token>
    3. Hub hashes the token and looks it up in the DB
    4. Si no coincide → 401 Unauthorized

Uso en FastAPI (las dependencias viven en dosync.auth_fastapi):
    from dosync.auth_fastapi import require_auth

    @app.get("/v1/devices")
    async def list_devices(auth=Depends(require_auth)):
        ...
"""

from __future__ import annotations
import hashlib
import logging
import os
import secrets
from typing import Optional

log = logging.getLogger("dosync.auth")

# NOTE: This module is the framework-agnostic core of DoSync auth.
# It deliberately does NOT import FastAPI (or any web framework). The
# FastAPI request dependencies (require_auth / optional_auth) live in
# dosync/auth_fastapi.py, which is only loaded by the server. Keeping the
# core free of framework imports means hash_token, AuthManager, and
# DeviceAuthManager can be imported and tested in isolation — and a prior
# bug (require_auth failing to import when FastAPI was absent) cannot recur.
# Do not add `from fastapi import ...` here.


# ── Hashing ───────────────────────────────────────────────────────────────────

def hash_token(token: str) -> str:
    """SHA-256 of a token. The token itself is never stored."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Auth manager ──────────────────────────────────────────────────────────────

class AuthManager:
    """
    Manages API keys for the DoSync hub.
    Backed by DoSyncDB for persistence.
    """

    def __init__(self, db, enabled: bool = True):
        """
        Args:
            db:      instancia de DoSyncDB
            enabled: when False, every request passes unverified.
                     Useful for local development.
        """
        self.db      = db
        self.enabled = enabled

    #: Shortest token this hub will store. A bearer token is checked without
    #: rate limiting or lockout, so it is guessed offline at full speed —
    #: which makes a short one materially worse than a short login password.
    #: Twelve is a floor against the trivial ("dosync", "1234"), not a claim
    #: that twelve is strong.
    MIN_TOKEN_LENGTH = 12

    def generate_key(self, label: str = "default", token: str = None) -> str:
        """Create an API key, store its hash, and return the plaintext once.

        `token` lets the operator CHOOSE the value instead of receiving 43 random
        characters. That was not possible before, and the consequence was worse
        than inconvenience: the only way to reach the dashboard was to keep a
        string nobody can memorise, so it ended up in a note, a password manager,
        or a chat message — or was simply lost, which is what happened to this
        project's own author. Software people self-host lets them pick a
        password; there is no reason a hub should not.

        Chosen values are still held to a floor (MIN_TOKEN_LENGTH) and the caller
        is told when a value is weak rather than being silently trusted.
        """
        if token is not None:
            token = token.strip()
            if len(token) < self.MIN_TOKEN_LENGTH:
                raise ValueError(
                    f"Token must be at least {self.MIN_TOKEN_LENGTH} characters. "
                    "It is checked with no rate limit, so a short one is guessed "
                    "offline at full speed.")
        else:
            token = secrets.token_urlsafe(32)
        key_hash = hash_token(token)
        self.db.save_api_key(key_hash, label)
        log.info("New API key generated: label='%s'", label)
        return token

    def verify(self, token: str) -> bool:
        """Verify a token. Returns True if it is valid."""
        if not self.enabled:
            return True
        key_hash = hash_token(token)
        return self.db.verify_api_key(key_hash)

    def ensure_default_key(self) -> Optional[str]:
        """
        With no keys present, generate one and return it to be displayed.
        With keys already present, return None — no second key is generated.
        Call this when the hub starts.

        When DOSYNC_DEMO_TOKEN is set, that value is used as the initial
        token instead of a random one. Useful for Docker deployments
        where the token must be known in advance.
        """
        if not self.enabled:
            return None
        if not self.db.has_any_key():
            demo_token = os.environ.get("DOSYNC_DEMO_TOKEN")
            if demo_token:
                key_hash = hash_token(demo_token)
                self.db.save_api_key(key_hash, "demo")
                log.info("Demo token registered from DOSYNC_DEMO_TOKEN env var")
                return demo_token
            token = self.generate_key("default")
            return token
        return None

    def reset_keys(self) -> int:
        """Delete every API key. Returns how many were removed.

        Lives here rather than as raw SQL at the call site: `manage.py keys
        reset` opened its own sqlite connection to do this, so a second caller
        (the dashboard) would have meant the same deletion written twice, in two
        files, free to drift.
        """
        keys = self.db.list_api_keys() if hasattr(self.db, "list_api_keys") else []
        with self.db._cursor() as cur:
            cur.execute("DELETE FROM api_keys")
            removed = cur.rowcount
        log.warning("All API keys deleted (%s)", removed)
        return removed if removed and removed > 0 else len(keys)

    def list_keys(self) -> list[dict]:
        return self.db.list_api_keys()

    def delete_key(self, key_hash: str) -> bool:
        return self.db.delete_api_key(key_hash)


# ── FastAPI dependency ────────────────────────────────────────────────────────

# Global auth manager reference — set when the server starts
_auth_manager: Optional[AuthManager] = None

def set_auth_manager(manager: AuthManager) -> None:
    global _auth_manager
    _auth_manager = manager

def get_auth_manager() -> AuthManager:
    return _auth_manager


# ── Device token manager ──────────────────────────────────────────────────────

class DeviceAuthManager:
    """
    Manages per-device authentication tokens.

    Flujo:
        1. Operador pre-registra un dispositivo → obtiene device_token
        2. The device includes device_token when registering its manifest
        3. Hub valida el token → solo permite el device_id autorizado

    Backward compatible: when device_token is absent from the manifest,
    registration proceeds unvalidated (legacy mode).
    Configurable via DOSYNC_DEVICE_AUTH=strict para requerir token siempre.
    """

    def __init__(self, db):
        self.db = db
        self.strict = os.environ.get("DOSYNC_DEVICE_AUTH", "permissive") == "strict"

    def provision(self, device_id: str, label: str = "") -> str:
        """
        Pre-register a device_id and generate its access token.
        Returns the plaintext token — show it to the operator ONCE.
        """
        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        self.db.save_device_token(device_id, token_hash, label or device_id)
        log.info("Device token provisioned: device_id='%s'", device_id)
        return token

    def verify(self, device_id: str, token: str) -> tuple[bool, str]:
        """
        Verify the token matches the declared device_id.
        Retorna (valid: bool, reason: str).
        """
        token_hash = hash_token(token)
        if self.db.verify_device_token(device_id, token_hash):
            return True, "ok"
        if self.db.device_is_provisioned(device_id):
            return False, f"Invalid token for device_id '{device_id}'"
        if self.strict:
            return False, f"Device '{device_id}' not provisioned — strict mode enabled"
        return True, "unprovisioned — permissive mode"

    def is_provisioned(self, device_id: str) -> bool:
        return self.db.device_is_provisioned(device_id)

    def revoke(self, device_id: str) -> bool:
        return self.db.delete_device_token(device_id)

    def list_provisioned(self) -> list[dict]:
        return self.db.list_device_tokens()


# Referencia global al device auth manager
_device_auth_manager: Optional[AuthManager] = None

def set_device_auth_manager(manager: "DeviceAuthManager") -> None:
    global _device_auth_manager
    _device_auth_manager = manager

def get_device_auth_manager() -> Optional["DeviceAuthManager"]:
    return _device_auth_manager

