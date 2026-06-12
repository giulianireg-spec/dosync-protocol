"""
DoSync — FastAPI Auth Dependencies
==================================
FastAPI request dependencies that enforce the hub's API-key authentication.

This module is the web-framework glue layer. It is the ONLY auth module that
imports FastAPI, and it imports it unconditionally — because this module is
only ever loaded by the server, which requires FastAPI by definition.

The framework-agnostic core (hash_token, AuthManager, DeviceAuthManager) lives
in dosync/auth.py and must never import a web framework. This separation keeps
the core testable in isolation and prevents the class of bug where a
module-level FastAPI symbol fails to resolve when FastAPI is absent.

Usage:
    from dosync.auth_fastapi import require_auth

    @app.get("/v1/devices")
    async def list_devices(auth=Depends(require_auth)):
        ...
"""

from __future__ import annotations
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dosync.auth import get_auth_manager

# Bearer scheme — auto_error=False so we can return our own 401 payloads.
_bearer = HTTPBearer(auto_error=False)


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> str:
    """
    FastAPI dependency that verifies the API key.

    Usage:
        @app.get("/v1/devices")
        async def list_devices(auth=Depends(require_auth)):
            ...

    If auth is disabled (development), it lets everything through.
    """
    manager = get_auth_manager()

    # Auth disabled
    if manager is None or not manager.enabled:
        return "dev"

    # No token
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Invalid token
    if not manager.verify(credentials.credentials):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


async def optional_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> Optional[str]:
    """
    Like require_auth but does not fail when no token is present.
    Useful for endpoints that are public but show more info when authenticated.
    """
    manager = get_auth_manager()
    if manager is None or not manager.enabled:
        return "dev"
    if not credentials:
        return None
    if manager.verify(credentials.credentials):
        return credentials.credentials
    return None
