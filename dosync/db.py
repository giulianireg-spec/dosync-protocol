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

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash        TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_used_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_presence_timestamp ON presence_signals(timestamp);
CREATE TABLE IF NOT EXISTS device_health (
    device_id       TEXT NOT NULL,
    action          TEXT NOT NULL,
    success         INTEGER NOT NULL,  -- 1 = ok, 0 = fail
    error           TEXT,
    timestamp       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_device ON device_health(device_id, timestamp);
CREATE TABLE IF NOT EXISTS device_state (
    device_id       TEXT PRIMARY KEY,
    state_json      TEXT NOT NULL,
    updated_at      REAL NOT NULL
);
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

    # ── API Keys ──────────────────────────────────────────────────────────────

    def save_api_key(self, key_hash: str, label: str) -> None:
        """Guarda una API key (almacenamos el hash, nunca la key en texto plano)."""
        with self._cursor() as cur:
            cur.execute("""
                INSERT OR IGNORE INTO api_keys (key_hash, label, created_at)
                VALUES (?, ?, ?)
            """, (key_hash, label, time.time()))

    def verify_api_key(self, key_hash: str) -> bool:
        """Verifica si una API key existe y actualiza last_used_at."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT key_hash FROM api_keys WHERE key_hash = ?", (key_hash,)
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE key_hash = ?",
                    (time.time(), key_hash)
                )
                return True
        return False

    def list_api_keys(self) -> list[dict]:
        """Lista todas las API keys registradas (sin el hash completo)."""
        with self._cursor() as cur:
            cur.execute("SELECT key_hash, label, created_at, last_used_at FROM api_keys")
            rows = cur.fetchall()
        return [
            {
                "key_preview": r["key_hash"][:8] + "...",
                "label":       r["label"],
                "created_at":  r["created_at"],
                "last_used_at": r["last_used_at"],
            }
            for r in rows
        ]

    def delete_api_key(self, key_hash: str) -> bool:
        """Elimina una API key. Retorna True si existía."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM api_keys WHERE key_hash = ?", (key_hash,))
            return cur.rowcount > 0

    def has_any_key(self) -> bool:
        """True si hay al menos una API key registrada."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as n FROM api_keys")
            return cur.fetchone()["n"] > 0

    # ── Device State (StateAwareResolver persistence) ─────────────────────────

    def save_device_state(self, device_id: str, state: dict) -> None:
        """Persiste el estado de un dispositivo. Upsert por device_id."""
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO device_state (device_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (device_id, json.dumps(state), time.time()),
            )

    def load_device_state(self, device_id: str) -> dict:
        """Carga el estado de un dispositivo. Retorna {} si no existe."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT state_json FROM device_state WHERE device_id = ?",
                (device_id,),
            )
            row = cur.fetchone()
        if row:
            return json.loads(row[0])
        return {}

    def load_all_device_states(self) -> dict:
        """Carga todos los estados persistidos. Retorna {device_id: state_dict}."""
        with self._cursor() as cur:
            cur.execute("SELECT device_id, state_json FROM device_state")
            rows = cur.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    # ── Device Health Monitor ─────────────────────────────────────────────────

    def record_execution(self, device_id: str, action: str,
                         success: bool, error: str = None) -> None:
        """Registra el resultado de una ejecución. Llamar tras cada adapter.execute()."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO device_health (device_id, action, success, error, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (device_id, action, 1 if success else 0, error, time.time()),
            )

    def get_device_health(self, device_id: str, last_n: int = 100) -> dict:
        """
        Estadísticas de salud de un dispositivo.
        Retorna: {device_id, total, success, failed, success_rate, last_error, last_seen}
        """
        with self._cursor() as cur:
            cur.execute(
                """SELECT action, success, error, timestamp
                   FROM device_health
                   WHERE device_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (device_id, last_n),
            )
            rows = cur.fetchall()

        if not rows:
            return {
                "device_id": device_id,
                "total": 0,
                "success": 0,
                "failed": 0,
                "success_rate": None,
                "last_error": None,
                "last_seen": None,
            }

        total   = len(rows)
        success = sum(1 for r in rows if r[1] == 1)
        failed  = total - success
        last_error = next((r[2] for r in rows if r[1] == 0), None)
        last_seen  = rows[0][3] if rows else None

        return {
            "device_id":    device_id,
            "total":        total,
            "success":      success,
            "failed":       failed,
            "success_rate": round(success / total, 3) if total else None,
            "last_error":   last_error,
            "last_seen":    last_seen,
        }

    def get_all_health(self, last_n: int = 100, min_executions: int = 1) -> list:
        """
        Estadísticas de salud de todos los dispositivos con al menos min_executions.
        Ordenado por tasa de éxito ascendente (peores primero).
        """
        with self._cursor() as cur:
            cur.execute("SELECT DISTINCT device_id FROM device_health")
            device_ids = [r[0] for r in cur.fetchall()]

        results = []
        for device_id in device_ids:
            health = self.get_device_health(device_id, last_n)
            if health["total"] >= min_executions:
                results.append(health)

        # Peores primero (tasa de éxito ascendente), None al final
        results.sort(key=lambda x: x["success_rate"] if x["success_rate"] is not None else 1.1)
        return results

    def get_health_alerts(self, threshold: float = 0.7, last_n: int = 100) -> list:
        """
        Dispositivos cuya tasa de éxito está por debajo del umbral.
        threshold=0.7 significa alertar cuando menos del 70% de las ejecuciones son exitosas.
        """
        all_health = self.get_all_health(last_n=last_n, min_executions=3)
        return [
            h for h in all_health
            if h["success_rate"] is not None and h["success_rate"] < threshold
        ]

    # ── Device tokens ─────────────────────────────────────────────────────────

    def init_device_tokens_table(self) -> None:
        """Crear tabla device_tokens si no existe."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS device_tokens (
                device_id   TEXT PRIMARY KEY,
                token_hash  TEXT NOT NULL,
                label       TEXT DEFAULT '',
                created_at  REAL NOT NULL
            )
        """)
        self._conn.commit()

    def save_device_token(self, device_id: str, token_hash: str, label: str = "") -> None:
        import time
        self._conn.execute("""
            INSERT OR REPLACE INTO device_tokens (device_id, token_hash, label, created_at)
            VALUES (?, ?, ?, ?)
        """, (device_id, token_hash, label, time.time()))
        self._conn.commit()

    def verify_device_token(self, device_id: str, token_hash: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM device_tokens WHERE device_id=? AND token_hash=?",
            (device_id, token_hash)
        )
        return cur.fetchone() is not None

    def device_is_provisioned(self, device_id: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM device_tokens WHERE device_id=?", (device_id,)
        )
        return cur.fetchone() is not None

    def delete_device_token(self, device_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM device_tokens WHERE device_id=?", (device_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_device_tokens(self) -> list[dict]:
        cur = self._conn.execute(
            "SELECT device_id, label, created_at FROM device_tokens ORDER BY created_at DESC"
        )
        return [{"device_id": r[0], "label": r[1], "created_at": r[2]} for r in cur.fetchall()]

    # ── Emergency snapshots ───────────────────────────────────────────────────

    def init_emergency_snapshots_table(self) -> None:
        """Tabla para persistir intents de emergencia activos."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS emergency_snapshots (
                intent_id   TEXT PRIMARY KEY,
                intent_class TEXT NOT NULL,
                urgency     TEXT NOT NULL,
                context     TEXT NOT NULL,
                fired_at    REAL NOT NULL,
                resolved_at REAL,
                status      TEXT DEFAULT 'active'
            )
        """)
        self._conn.commit()

    def save_emergency_snapshot(self, intent_id: str, intent_class: str,
                                 urgency: str, context: dict) -> None:
        import time, json
        self._conn.execute("""
            INSERT OR REPLACE INTO emergency_snapshots
                (intent_id, intent_class, urgency, context, fired_at, status)
            VALUES (?, ?, ?, ?, ?, 'active')
        """, (intent_id, intent_class, urgency, json.dumps(context), time.time()))
        self._conn.commit()

    def resolve_emergency_snapshot(self, intent_id: str) -> None:
        import time
        self._conn.execute("""
            UPDATE emergency_snapshots
            SET resolved_at=?, status='resolved'
            WHERE intent_id=?
        """, (time.time(), intent_id))
        self._conn.commit()

    def get_active_emergency_snapshots(self) -> list[dict]:
        import json
        cur = self._conn.execute("""
            SELECT intent_id, intent_class, urgency, context, fired_at
            FROM emergency_snapshots
            WHERE status='active'
            ORDER BY fired_at DESC
        """)
        return [
            {"intent_id": r[0], "intent_class": r[1], "urgency": r[2],
             "context": json.loads(r[3]), "fired_at": r[4]}
            for r in cur.fetchall()
        ]

    def clear_old_snapshots(self, max_age_hours: int = 24) -> int:
        """Limpia snapshots resueltos o muy antiguos."""
        import time
        cutoff = time.time() - (max_age_hours * 3600)
        cur = self._conn.execute("""
            DELETE FROM emergency_snapshots
            WHERE status='resolved' OR fired_at < ?
        """, (cutoff,))
        self._conn.commit()
        return cur.rowcount

