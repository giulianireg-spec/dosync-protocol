"""
DoSync — Authentication
=======================
API key simple para proteger el hub.

- Las keys se generan con secrets.token_urlsafe(32)
- Solo almacenamos el SHA-256 del token, nunca el token en texto plano
- El primer arranque genera una key automáticamente y la muestra en consola
- Las keys adicionales se crean via API o CLI

Flujo:
    1. Hub arranca → si no hay keys, genera una y la muestra UNA SOLA VEZ
    2. Cliente incluye: Authorization: Bearer <token>
    3. Hub hashea el token y busca en la DB
    4. Si no coincide → 401 Unauthorized

Uso en FastAPI:
    from dosync.auth import require_auth
    
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

# FastAPI imports son lazy — solo se cargan cuando se usa como servidor
try:
    from fastapi import Depends, HTTPException, Security
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    _bearer = HTTPBearer(auto_error=False)
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    _bearer = None


# ── Hashing ───────────────────────────────────────────────────────────────────

def hash_token(token: str) -> str:
    """SHA-256 del token. Nunca almacenamos el token en texto plano."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Auth manager ──────────────────────────────────────────────────────────────

class AuthManager:
    """
    Gestiona API keys para el hub DoSync.
    Se integra con DoSyncDB para persistencia.
    """

    def __init__(self, db, enabled: bool = True):
        """
        Args:
            db:      instancia de DoSyncDB
            enabled: si False, todas las requests pasan sin verificación.
                     Útil para desarrollo local.
        """
        self.db      = db
        self.enabled = enabled

    def generate_key(self, label: str = "default") -> str:
        """
        Genera una nueva API key, la hashea y la guarda en la DB.
        Retorna el token en texto plano — solo se muestra UNA VEZ.
        """
        token    = secrets.token_urlsafe(32)
        key_hash = hash_token(token)
        self.db.save_api_key(key_hash, label)
        log.info("New API key generated: label='%s'", label)
        return token

    def verify(self, token: str) -> bool:
        """Verifica un token. Retorna True si es válido."""
        if not self.enabled:
            return True
        key_hash = hash_token(token)
        return self.db.verify_api_key(key_hash)

    def ensure_default_key(self) -> Optional[str]:
        """
        Si no hay ninguna key, genera una y la retorna para mostrarla.
        Si ya hay keys, retorna None (no genera otra).
        Llamar al iniciar el hub.
        """
        if not self.enabled:
            return None
        if not self.db.has_any_key():
            token = self.generate_key("default")
            return token
        return None

    def list_keys(self) -> list[dict]:
        return self.db.list_api_keys()

    def delete_key(self, key_hash: str) -> bool:
        return self.db.delete_api_key(key_hash)


# ── FastAPI dependency ────────────────────────────────────────────────────────

# Referencia global al auth manager — se setea al iniciar el servidor
_auth_manager: Optional[AuthManager] = None

def set_auth_manager(manager: AuthManager) -> None:
    global _auth_manager
    _auth_manager = manager

def get_auth_manager() -> AuthManager:
    return _auth_manager


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer),
) -> str:
    """
    FastAPI dependency que verifica la API key.
    
    Uso:
        @app.get("/v1/devices")
        async def list_devices(auth=Depends(require_auth)):
            ...
    
    Si auth está deshabilitado (desarrollo), deja pasar todo.
    """
    manager = get_auth_manager()

    # Auth deshabilitado
    if manager is None or not manager.enabled:
        return "dev"

    # Sin token
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Use: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Token inválido
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
    Como require_auth pero no falla si no hay token.
    Útil para endpoints que son públicos pero muestran más info si estás autenticado.
    """
    manager = get_auth_manager()
    if manager is None or not manager.enabled:
        return "dev"
    if not credentials:
        return None
    if manager.verify(credentials.credentials):
        return credentials.credentials
    return None
