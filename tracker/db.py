"""SQLite storage: schema/migrations, upserts and queries."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from . import config
from .parsing import kg_to_lb, positive, safe_int

# Columns added after the original schema: name -> SQL type.
EXTRA_COLUMNS = {
    "profile_id": "TEXT",
    "profile_name": "TEXT",
    "skeletal_muscle": "REAL",
    "subcutaneous_fat": "REAL",
    "body_score": "INTEGER",
}

INSERT_COLUMNS = (
    "record_id",
    "scale_mac",
    "device_id",
    "product_model",
    "measured_at",
    "measure_ts",
    "profile_id",
    "profile_name",
    *config.TRACKED_METRICS,
    "raw_json",
)

MEASUREMENT_COLUMNS = (
    "id, record_id, scale_mac, device_id, product_model, profile_id, profile_name, measured_at, measure_ts, "
    + ", ".join(config.TRACKED_METRICS)
)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = sqlite3.connect(config.db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_db() -> None:
    config.data_dir().mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL,
                scale_mac TEXT NOT NULL,
                device_id TEXT,
                product_model TEXT,
                profile_id TEXT,
                profile_name TEXT,
                measured_at TEXT NOT NULL,
                measure_ts INTEGER,
                weight_lb REAL,
                weight_kg REAL,
                bmi REAL,
                body_fat REAL,
                muscle REAL,
                body_water REAL,
                protein REAL,
                bone_mineral REAL,
                body_vfr REAL,
                bmr REAL,
                metabolic_age INTEGER,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(record_id, scale_mac)
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT)")
        existing = {row[1] for row in conn.execute("PRAGMA table_info(measurements)")}
        added = [name for name in EXTRA_COLUMNS if name not in existing]
        for name in added:
            conn.execute(f"ALTER TABLE measurements ADD COLUMN {name} {EXTRA_COLUMNS[name]}")
        if {"skeletal_muscle", "subcutaneous_fat", "body_score"} & set(added):
            _backfill_new_metrics(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_measure_ts ON measurements(measure_ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_measurements_profile_id ON measurements(profile_id)")
        _backfill_profile_fields(conn)


def _backfill_profile_fields(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE measurements
        SET profile_id = COALESCE(profile_id, scale_mac),
            profile_name = COALESCE(profile_name, SUBSTR(scale_mac, 8))
        WHERE product_model = 'IMPORT_XLSX' AND scale_mac LIKE 'import:%'
        """
    )
    updates = []
    for row in conn.execute("SELECT id, raw_json FROM measurements WHERE profile_id IS NULL"):
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        parsed = raw.get("family_member_id") or raw.get("user_id")
        if parsed:
            updates.append((str(parsed), row["id"]))
    conn.executemany("UPDATE measurements SET profile_id = ? WHERE id = ?", updates)


def _backfill_new_metrics(conn: sqlite3.Connection) -> None:
    """Fill newly added metric columns from raw_json of previously synced cloud records."""
    updates = []
    for row in conn.execute("SELECT id, raw_json FROM measurements WHERE product_model != 'IMPORT_XLSX'"):
        try:
            raw = json.loads(row["raw_json"] or "{}")
        except json.JSONDecodeError:
            continue
        updates.append(
            (
                kg_to_lb(positive(raw.get("skeletal_muscle"))),
                positive(raw.get("subcutaneous_fat")),
                safe_int(raw.get("body_score")),
                row["id"],
            )
        )
    conn.executemany(
        "UPDATE measurements SET skeletal_muscle = ?, subcutaneous_fat = ?, body_score = ? WHERE id = ?",
        updates,
    )


# --------------------------------------------------------------------------- meta


def get_meta(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --------------------------------------------------------------------------- writes


def upsert_measurement(conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
    """Insert a row; returns False if it already exists."""
    if payload.get("product_model") == "IMPORT_XLSX":
        # Spreadsheet rows have no stable id; treat same time + profile + weight as a duplicate.
        existing = conn.execute(
            """
            SELECT 1 FROM measurements
            WHERE measure_ts = :measure_ts
              AND COALESCE(profile_id, '') = COALESCE(:profile_id, '')
              AND ABS(COALESCE(weight_lb, 0) - COALESCE(:weight_lb, 0)) <= 0.05
            LIMIT 1
            """,
            payload,
        ).fetchone()
        if existing:
            return False
    cols = ", ".join(INSERT_COLUMNS)
    params = ", ".join(f":{c}" for c in INSERT_COLUMNS)
    cursor = conn.execute(f"INSERT OR IGNORE INTO measurements ({cols}) VALUES ({params})", payload)
    return cursor.rowcount > 0


def rename_profile(conn: sqlite3.Connection, profile_id: str, name: str) -> None:
    conn.execute(
        "UPDATE measurements SET profile_name = ? WHERE profile_id = ? AND product_model != 'IMPORT_XLSX'",
        (name, profile_id),
    )


# --------------------------------------------------------------------------- reads


def latest_cloud_ts(conn: sqlite3.Connection, profile_id: str | None) -> int | None:
    query = "SELECT MAX(measure_ts) AS ts FROM measurements WHERE product_model != 'IMPORT_XLSX'"
    params: list[Any] = []
    if profile_id:
        query += " AND profile_id = ?"
        params.append(profile_id)
    row = conn.execute(query, params).fetchone()
    return safe_int(row["ts"]) if row else None


def filter_clause(
    *, days: int | None = None, profile: str | None = None, start_ts: int | None = None, end_ts: int | None = None
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if days is not None and days > 0:
        where.append("measure_ts >= ?")
        params.append(int((datetime.now() - timedelta(days=days)).timestamp()))
    if profile:
        where.append("profile_id = ?")
        params.append(profile)
    if start_ts is not None:
        where.append("measure_ts >= ?")
        params.append(start_ts)
    if end_ts is not None:
        where.append("measure_ts <= ?")
        params.append(end_ts)
    return (f" WHERE {' AND '.join(where)} " if where else ""), params


def _round_metrics(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for metric in config.TRACKED_METRICS:
        if item.get(metric) is not None:
            item[metric] = round(float(item[metric]), 4)
    return item


def measurements(*, days: int | None = None, profile: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    where, params = filter_clause(days=days, profile=profile)
    inner = f"SELECT {MEASUREMENT_COLUMNS} FROM measurements{where} ORDER BY measure_ts DESC LIMIT ?"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM ({inner}) ORDER BY measure_ts ASC", [*params, limit]).fetchall()
    return [_round_metrics(r) for r in rows]


def latest(profile: str | None = None) -> dict[str, Any] | None:
    where, params = filter_clause(profile=profile)
    with connect() as conn:
        row = conn.execute(
            f"SELECT {MEASUREMENT_COLUMNS} FROM measurements{where} ORDER BY measure_ts DESC LIMIT 1", params
        ).fetchone()
    return _round_metrics(row) if row else None


def profiles() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT profile_id,
                   COALESCE(MAX(NULLIF(profile_name, '')), NULLIF(profile_id, ''), 'Unknown') AS profile_name,
                   COUNT(*) AS records,
                   MAX(measure_ts) AS latest_ts
            FROM measurements
            GROUP BY profile_id
            ORDER BY records DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def export_rows(columns: list[str], **filters: Any) -> list[sqlite3.Row]:
    where, params = filter_clause(**filters)
    with connect() as conn:
        return conn.execute(
            f"SELECT {', '.join(columns)} FROM measurements{where} ORDER BY measure_ts ASC", params
        ).fetchall()
