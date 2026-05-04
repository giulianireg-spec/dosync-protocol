"""
DoSync — Persistence Layer (SQLite)
Todos los datos criticos del hub sobreviven reinicios.

Un solo archivo dosync.db en el directorio del proyecto.
Sin dependencias externas — sqlite3 viene con Python.
"""

from __future__ import annotations
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

log = logging.getLogger("dosync.db")

# Schema SQL
SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    manifest_json   TEXT NOT NULL,
    registered_at   REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS family_profile (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    profile_json    TEXT NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_json      TEXT NOT NULL,
    hash            TEXT NOT NULL,
    timestamp       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS presence_signals (
    device_id       TEXT PRIMARY KEY,
    signal_json     TEXT NOT NULL,
    timestamp       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_presence_timestamp ON presence_signals(timestamp);
"""


class DoSyncDB:
    """
    Capa de persistencia SQLite para el DoSync Hub.

    Uso:
        db = DoSyncDB("dosync.db")
        db.init()

        # Guardar un dispositivo
        db.save_device("lock-01", manifest_dict)

        # Cargar todos los dispositivos al iniciar el hub
        devices = db.load_devices()
    """

    def __init__(self, db_path: str = "dosync.db"):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # ── Conexion ──────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Abre la conexion y crea las tablas si no existen."""
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,   # FastAPI usa multiples threads
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")  # mejor concurrencia
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        log.info("Database initialized: %s", self.db_path.resolve())

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # ── Device registry ───────────────────────────────────────────────────────

    def save_device(self, device_id: str, manifest: dict) -> None:
        """Guarda o actualiza un dispositivo."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO devices (device_id, manifest_json, registered_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    manifest_json = excluded.manifest_json,
                    updated_at    = excluded.updated_at
            """, (device_id, json.dumps(manifest), now, now))
        log.debug("Saved device: %s", device_id)

    def delete_device(self, device_id: str) -> None:
        """Elimina un dispositivo del registry."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        log.debug("Deleted device: %s", device_id)

    def load_devices(self) -> list[dict]:
        """Carga todos los dispositivos registrados."""
        with self._cursor() as cur:
            cur.execute("SELECT manifest_json FROM devices ORDER BY registered_at")
            rows = cur.fetchall()
        manifests = [json.loads(r["manifest_json"]) for r in rows]
        log.info("Loaded %d device(s) from database", len(manifests))
        return manifests

    def device_count(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM devices")
            return cur.fetchone()["n"]

    # ── Family profile ────────────────────────────────────────────────────────

    def save_family_profile(self, profile: dict) -> None:
        """Guarda el perfil familiar (unico, siempre id=1)."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO family_profile (id, profile_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at   = excluded.updated_at
            """, (json.dumps(profile), now))
        log.debug("Saved family profile")

    def load_family_profile(self) -> Optional[dict]:
        """Carga el perfil familiar, o None si no existe."""
        with self._cursor() as cur:
            cur.execute("SELECT profile_json FROM family_profile WHERE id = 1")
            row = cur.fetchone()
        if row:
            log.info("Loaded family profile from database")
            return json.loads(row["profile_json"])
        return None

    # ── Audit log ─────────────────────────────────────────────────────────────

    def append_audit(self, entry: dict) -> None:
        """
        Persiste una entrada del audit log.
        El hash ya viene calculado dentro del entry dict.
        """
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO audit_log (entry_json, hash, timestamp)
                VALUES (?, ?, ?)
            """, (
                json.dumps(entry),
                entry.get("hash", ""),
                entry.get("timestamp", time.time()),
            ))

    def load_audit_log(self) -> list[dict]:
        """Carga el audit log completo ordenado por timestamp."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT entry_json FROM audit_log ORDER BY timestamp, id"
            )
            rows = cur.fetchall()
        entries = [json.loads(r["entry_json"]) for r in rows]
        log.info("Loaded %d audit log entries from database", len(entries))
        return entries

    def audit_count(self) -> int:
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM audit_log")
            return cur.fetchone()["n"]

    # ── Presence signals ──────────────────────────────────────────────────────

    def save_presence_signal(self, device_id: str, signal: dict) -> None:
        """Guarda o actualiza la señal de presencia de un dispositivo."""
        now = signal.get("timestamp", time.time())
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO presence_signals (device_id, signal_json, timestamp)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    signal_json = excluded.signal_json,
                    timestamp   = excluded.timestamp
            """, (device_id, json.dumps(signal), now))

    def load_presence_signals(self) -> list[dict]:
        """Carga todas las señales de presencia activas."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT signal_json FROM presence_signals ORDER BY timestamp"
            )
            rows = cur.fetchall()
        signals = [json.loads(r["signal_json"]) for r in rows]
        if signals:
            log.info("Loaded %d presence signal(s) from database", len(signals))
        return signals

    def delete_presence_signal(self, device_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM presence_signals WHERE device_id = ?", (device_id,)
            )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Resumen del estado de la base de datos."""
        return {
            "db_path":        str(self.db_path.resolve()),
            "db_size_kb":     round(self.db_path.stat().st_size / 1024, 1)
                              if self.db_path.exists() else 0,
            "devices":        self.device_count(),
            "audit_entries":  self.audit_count(),
        }
