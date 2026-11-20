from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from openpyxl import load_workbook
from wyze_sdk import Client
from wyze_sdk.models.devices import DeviceModels

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "measurements.db"
POUNDS_PER_KG = 2.20462262185
# Used only when the device list call fails. The model just picks the backend:
# JA.SC/JA.SC2 use the original scale service, every newer scale uses "Pluto".
FALLBACK_MODELS = ("WL_SC2", "JA.SC2")
FULL_HISTORY_DAYS = 3650

TRACKED_METRICS = (
    "weight_lb",
    "weight_kg",
    "bmi",
    "body_fat",
    "muscle",
    "skeletal_muscle",
    "body_water",
    "protein",
    "bone_mineral",
    "subcutaneous_fat",
    "body_vfr",
    "bmr",
    "metabolic_age",
    "body_score",
)

# Columns added after the original schema: name -> SQL type.
EXTRA_COLUMNS = {
    "profile_id": "TEXT",
    "profile_name": "TEXT",
    "skeletal_muscle": "REAL",
    "subcutaneous_fat": "REAL",
    "body_score": "INTEGER",
}

EXCEL_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "row_number": ("number",),
    "date_time": ("dateandtime", "datetime", "time"),
    "weight_lb": ("weightlb",),
    "weight_kg": ("weightkg",),
    "bmi": ("bmi",),
    "body_fat": ("bodyfat",),
    "muscle": ("musclemass", "muscle"),
    "body_water": ("bodywater",),
    "protein": ("protein",),
    "bone_mineral": ("bonemass", "bonemineral"),
    "body_vfr": ("visceralfat",),
    "bmr": ("bmr",),
    "metabolic_age": ("metabolicage",),
    "skeletal_muscle": ("skeletalmuscle", "skeletalmusclemass"),
    "subcutaneous_fat": ("subcutaneousfat",),
    "body_score": ("bodyscore",),
}

EXPORT_RANGE_TO_DAYS: dict[str, int | None] = {
    "day": 1,
    "week": 7,
    "year": 365,
    "all": None,
    "custom": None,
}

load_dotenv(BASE_DIR / ".env")

import wyze_auth  # noqa: E402  (needs .env loaded first)

app = Flask(__name__)


@dataclass
class DbResult:
    inserted: int = 0
    skipped: int = 0


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> float | None:
    """Composition values <= 0 mean 'not measured' (e.g. weight-only readings)."""
    number = _safe_float(value)
    return number if number is not None and number > 0 else None


def _normalize_epoch_seconds(value: Any) -> int | None:
    ts = _safe_int(value)
    if ts is None:
        return None
    # Wyze scale records usually provide ms since epoch.
    if ts > 10_000_000_000:
        ts = ts // 1000
    return ts


def _strip_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "- -", "N/A", "n/a"}:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text.replace(",", ""))
    if not match:
        return None
    return _safe_float(match.group(0))


def _kg_to_lb(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * POUNDS_PER_KG, 2)


def _parse_export_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    formats = (
        "%Y.%m.%d %I:%M %p",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _normalize_header(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _find_header_map(sheet) -> tuple[dict[str, int], int] | tuple[None, None]:
    max_scan_row = min(25, sheet.max_row)
    for row_idx in range(1, max_scan_row + 1):
        normalized_to_col: dict[str, int] = {}
        for col_idx in range(1, sheet.max_column + 1):
            key = _normalize_header(sheet.cell(row=row_idx, column=col_idx).value)
            if key:
                normalized_to_col[key] = col_idx

        mapping: dict[str, int] = {}
        for target, aliases in EXCEL_HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized_to_col:
                    mapping[target] = normalized_to_col[alias]
                    break

        if "date_time" in mapping and ("weight_lb" in mapping or "weight_kg" in mapping):
            return mapping, row_idx

    return None, None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _range_to_days(value: str | None) -> int | None:
    if value is None:
        return None
    key = value.strip().lower()
    return EXPORT_RANGE_TO_DAYS.get(key)


def _build_filter_clauses(
    *,
    days: int | None = None,
    profile: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if days is not None and days > 0:
        min_ts = int((datetime.now() - timedelta(days=days)).timestamp())
        where.append("measure_ts >= ?")
        params.append(min_ts)

    if profile:
        where.append("profile_id = ?")
        params.append(profile)

    if start_ts is not None:
        where.append("measure_ts >= ?")
        params.append(start_ts)

    if end_ts is not None:
        where.append("measure_ts <= ?")
        params.append(end_ts)

    clause = f" WHERE {' AND '.join(where)} " if where else ""
    return clause, params


def _date_to_epoch(date_text: str, *, end_of_day: bool = False) -> int:
    parsed = datetime.strptime(date_text, "%Y-%m-%d")
    if end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
    return int(parsed.timestamp())


def _backfill_profile_fields(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE measurements
        SET
            profile_id = COALESCE(profile_id, scale_mac),
            profile_name = COALESCE(profile_name, SUBSTR(scale_mac, 8))
        WHERE product_model = 'IMPORT_XLSX' AND scale_mac LIKE 'import:%'
        """
    )

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, scale_mac, raw_json, profile_id, profile_name
        FROM measurements
        WHERE profile_id IS NULL OR profile_name IS NULL
        """
    ).fetchall()

    updates: list[tuple[str | None, str | None, int]] = []
    for row in rows:
        profile_id = row["profile_id"]
        profile_name = row["profile_name"]

        if profile_id and profile_name:
            continue

        if not profile_name and row["scale_mac"] and str(row["scale_mac"]).startswith("import:"):
            profile_name = str(row["scale_mac"]).split(":", 1)[1]
        if not profile_id and row["scale_mac"] and str(row["scale_mac"]).startswith("import:"):
            profile_id = str(row["scale_mac"])

        raw_json = row["raw_json"]
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            raw = {}

        if not profile_id:
            parsed_id = raw.get("family_member_id") or raw.get("user_id")
            profile_id = str(parsed_id) if parsed_id else None

        if profile_id or profile_name:
            updates.append((profile_id, profile_name, int(row["id"])))

    if updates:
        conn.executemany(
            """
            UPDATE measurements
            SET profile_id = COALESCE(?, profile_id),
                profile_name = COALESCE(?, profile_name)
            WHERE id = ?
            """,
            updates,
        )


def _backfill_new_metrics(conn: sqlite3.Connection) -> None:
    """Fill newly added metric columns from raw_json of previously synced cloud records."""
    rows = conn.execute(
        "SELECT id, raw_json FROM measurements WHERE product_model != 'IMPORT_XLSX'"
    ).fetchall()
    updates = []
    for row_id, raw_json in rows:
        try:
            raw = json.loads(raw_json) if raw_json else {}
        except json.JSONDecodeError:
            continue
        updates.append(
            (
                _kg_to_lb(_positive(raw.get("skeletal_muscle"))),
                _positive(raw.get("subcutaneous_fat")),
                _safe_int(raw.get("body_score")),
                row_id,
            )
        )
    conn.executemany(
        "UPDATE measurements SET skeletal_muscle = ?, subcutaneous_fat = ?, body_score = ? WHERE id = ?",
        updates,
    )


def _get_meta(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _ensure_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
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
        columns = _table_columns(conn, "measurements")
        added = [name for name in EXTRA_COLUMNS if name not in columns]
        for name in added:
            conn.execute(f"ALTER TABLE measurements ADD COLUMN {name} {EXTRA_COLUMNS[name]}")
        if {"skeletal_muscle", "subcutaneous_fat", "body_score"} & set(added):
            _backfill_new_metrics(conn)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_measurements_measure_ts
            ON measurements(measure_ts DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_measurements_profile_id
            ON measurements(profile_id)
            """
        )
        _backfill_profile_fields(conn)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_cloud_ts(conn: sqlite3.Connection, profile_id: str | None) -> int | None:
    query = "SELECT MAX(measure_ts) AS ts FROM measurements WHERE product_model != 'IMPORT_XLSX'"
    params: list[Any] = []
    if profile_id:
        query += " AND profile_id = ?"
        params.append(profile_id)
    row = conn.execute(query, params).fetchone()
    return _safe_int(row["ts"]) if row else None


def _measurement_payload(
    record: Any,
    *,
    product_model: str,
    profile_id: str | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    raw = record.to_dict()

    measure_ts = _normalize_epoch_seconds(raw.get("measure_ts"))
    measured_at = datetime.fromtimestamp(measure_ts).isoformat() if measure_ts else datetime.now().isoformat()
    resolved_scale_mac = str(raw.get("mac") or f"model:{product_model}")
    record_id = str(raw.get("id") or raw.get("data_id") or f"{resolved_scale_mac}-{measure_ts or 'na'}")
    profile_id = str(raw.get("family_member_id") or raw.get("user_id") or profile_id or "") or None

    # wyze-sdk returns `weight` in lb while internal `_weight` is kg.
    weight_lb = _safe_float(raw.get("weight"))
    weight_kg = _safe_float(getattr(record, "_weight", None))
    if weight_kg is None and weight_lb is not None:
        weight_kg = round(weight_lb / POUNDS_PER_KG, 4)
    if weight_lb is None and weight_kg is not None:
        weight_lb = _kg_to_lb(weight_kg)

    # Cloud responses expose these in kg for Scale S/X, convert to lb for consistency with exports/UI.
    muscle_lb = _kg_to_lb(_safe_float(raw.get("muscle")))
    bone_mass_lb = _kg_to_lb(_safe_float(raw.get("bone_mineral")))

    return {
        "record_id": record_id,
        "scale_mac": resolved_scale_mac,
        "device_id": raw.get("device_id"),
        "product_model": product_model,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "measure_ts": measure_ts,
        "measured_at": measured_at,
        "weight_lb": weight_lb,
        "weight_kg": weight_kg,
        "bmi": _safe_float(raw.get("bmi")),
        "body_fat": _safe_float(raw.get("body_fat")),
        "muscle": muscle_lb,
        "body_water": _safe_float(raw.get("body_water")),
        "protein": _safe_float(raw.get("protein")),
        "bone_mineral": bone_mass_lb,
        "body_vfr": _safe_float(raw.get("body_vfr")),
        "bmr": _safe_float(raw.get("bmr")),
        "metabolic_age": _safe_int(raw.get("metabolic_age")),
        # Scale Ultra / BodyScan extras (skeletal muscle is kg in the API).
        "skeletal_muscle": _kg_to_lb(_positive(raw.get("skeletal_muscle"))),
        "subcutaneous_fat": _positive(raw.get("subcutaneous_fat")),
        "body_score": _safe_int(raw.get("body_score")),
        "raw_json": json.dumps(raw, separators=(",", ":"), sort_keys=True, default=str),
    }


def _payload_from_import_row(
    *,
    sheet_name: str,
    row_index: int,
    row_data: dict[str, Any],
    measured_at: datetime,
) -> dict[str, Any] | None:
    weight_lb = _strip_numeric(row_data.get("weight_lb"))
    weight_kg = _strip_numeric(row_data.get("weight_kg"))
    if weight_lb is None and weight_kg is None:
        return None
    if weight_lb is None and weight_kg is not None:
        weight_lb = _kg_to_lb(weight_kg)
    if weight_kg is None and weight_lb is not None:
        weight_kg = round(weight_lb / POUNDS_PER_KG, 4)

    measure_ts = int(measured_at.timestamp())
    seq = _safe_int(_strip_numeric(row_data.get("row_number"))) or row_index
    record_id = f"import-{sheet_name}-{measure_ts}-{seq}"

    return {
        "record_id": record_id,
        "scale_mac": f"import:{sheet_name}",
        "device_id": None,
        "product_model": "IMPORT_XLSX",
        "profile_id": f"import:{sheet_name}",
        "profile_name": sheet_name,
        "measure_ts": measure_ts,
        "measured_at": measured_at.isoformat(),
        "weight_lb": weight_lb,
        "weight_kg": weight_kg,
        "bmi": _strip_numeric(row_data.get("bmi")),
        "body_fat": _strip_numeric(row_data.get("body_fat")),
        "muscle": _strip_numeric(row_data.get("muscle")),
        "body_water": _strip_numeric(row_data.get("body_water")),
        "protein": _strip_numeric(row_data.get("protein")),
        "bone_mineral": _strip_numeric(row_data.get("bone_mineral")),
        "body_vfr": _strip_numeric(row_data.get("body_vfr")),
        "bmr": _strip_numeric(row_data.get("bmr")),
        "metabolic_age": _safe_int(_strip_numeric(row_data.get("metabolic_age"))),
        "skeletal_muscle": _strip_numeric(row_data.get("skeletal_muscle")),
        "subcutaneous_fat": _strip_numeric(row_data.get("subcutaneous_fat")),
        "body_score": _safe_int(_strip_numeric(row_data.get("body_score"))),
        "raw_json": json.dumps(
            {"source": "xlsx", "sheet": sheet_name, "row_index": row_index, "row": row_data},
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
    }


def _upsert_measurement(conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
    if payload.get("product_model") == "IMPORT_XLSX":
        existing = conn.execute(
            """
            SELECT 1
            FROM measurements
            WHERE measure_ts = :measure_ts
              AND COALESCE(profile_id, '') = COALESCE(:profile_id, '')
              AND ABS(COALESCE(weight_lb, 0) - COALESCE(:weight_lb, 0)) <= 0.05
            LIMIT 1
            """,
            payload,
        ).fetchone()
        if existing:
            return False

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO measurements (
            record_id, scale_mac, device_id, product_model, measured_at, measure_ts,
            profile_id, profile_name,
            weight_lb, weight_kg, bmi, body_fat, muscle, body_water, protein,
            bone_mineral, body_vfr, bmr, metabolic_age,
            skeletal_muscle, subcutaneous_fat, body_score, raw_json
        ) VALUES (
            :record_id, :scale_mac, :device_id, :product_model, :measured_at, :measure_ts,
            :profile_id, :profile_name,
            :weight_lb, :weight_kg, :bmi, :body_fat, :muscle, :body_water, :protein,
            :bone_mineral, :body_vfr, :bmr, :metabolic_age,
            :skeletal_muscle, :subcutaneous_fat, :body_score, :raw_json
        )
        """,
        payload,
    )
    return cursor.rowcount > 0


def import_from_excel(file_bytes: bytes, *, filename: str = "upload.xlsx", sheet_name: str | None = None) -> dict[str, Any]:
    workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets = [workbook[sheet_name]] if sheet_name else workbook.worksheets

    with _connect() as conn:
        result = DbResult()
        parse_errors: list[str] = []
        sheet_summaries: list[dict[str, Any]] = []

        for sheet in sheets:
            header_map, header_row = _find_header_map(sheet)
            if header_map is None or header_row is None:
                sheet_summaries.append({"sheet": sheet.title, "rows_seen": 0, "rows_loaded": 0, "header_found": False})
                continue

            rows_seen = 0
            rows_loaded = 0
            for row_idx in range(header_row + 1, sheet.max_row + 1):
                date_value = sheet.cell(row=row_idx, column=header_map["date_time"]).value
                if date_value is None or str(date_value).strip() == "":
                    continue

                rows_seen += 1
                measured_at = _parse_export_datetime(date_value)
                if measured_at is None:
                    parse_errors.append(f"{sheet.title}: row {row_idx} has invalid date '{date_value}'")
                    continue

                row_data = {
                    key: sheet.cell(row=row_idx, column=column).value
                    for key, column in header_map.items()
                }
                payload = _payload_from_import_row(
                    sheet_name=sheet.title,
                    row_index=row_idx,
                    row_data=row_data,
                    measured_at=measured_at,
                )
                if payload is None:
                    continue

                rows_loaded += 1
                if _upsert_measurement(conn, payload):
                    result.inserted += 1
                else:
                    result.skipped += 1

            sheet_summaries.append(
                {
                    "sheet": sheet.title,
                    "rows_seen": rows_seen,
                    "rows_loaded": rows_loaded,
                    "header_found": True,
                }
            )

        conn.commit()

    return {
        "filename": filename,
        "inserted": result.inserted,
        "skipped": result.skipped,
        "sheets_processed": len(sheets),
        "sheet_summaries": sheet_summaries,
        "parse_errors": parse_errors[:20],
        "imported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _service_key(model: str) -> str:
    return "scale" if model in DeviceModels.SCALE_ else "pluto"


def _owner_display_name(client: Client, fallback: str | None) -> str:
    if fallback and "@" not in fallback:
        return fallback
    try:
        info = client.user_get_info().data.get("data") or {}
        nickname = str(info.get("nickname") or "").strip()
        if nickname and "@" not in nickname:
            return nickname
    except Exception:
        pass
    return "Me"


def _discover_sync_targets(client: Client) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Find scales and every family member on them.

    Returns (scales, targets, errors). Each target is one person to pull records for.
    """
    errors: list[str] = []
    scales: list[dict[str, Any]] = []
    try:
        for scale in client.scales.list():
            scales.append(
                {
                    "mac": scale.mac,
                    "model": scale.product.model if scale.product else None,
                    "nickname": scale.nickname,
                }
            )
    except Exception as exc:
        errors.append(f"Scale list failed ({exc}); falling back to direct queries.")

    targets: dict[tuple[str, str | None], dict[str, Any]] = {}
    for scale in scales:
        model = scale["model"] or FALLBACK_MODELS[0]
        members: list[dict[str, Any]] = []
        try:
            response = client.scales._scale_client(model).get_family_member(did=scale["mac"])
            members = (response.data or {}).get("data") or []
        except Exception as exc:
            errors.append(f"{scale['nickname'] or scale['mac']}: could not list family members ({exc})")

        if not members:
            targets.setdefault((_service_key(model), None), {"model": model, "profile_id": None, "name": None})
        for member in members:
            member_id = str(member.get("id") or "") or None
            if member_id is None:
                continue
            name = str(member.get("nick_name") or "").strip() or None
            if member_id == client._user_id:
                name = _owner_display_name(client, name)
            targets.setdefault(
                (_service_key(model), member_id),
                {"model": model, "profile_id": member_id, "name": name, "scale": scale["nickname"]},
            )

    if not scales:
        for model in FALLBACK_MODELS:
            targets.setdefault((_service_key(model), None), {"model": model, "profile_id": None, "name": None})

    return scales, list(targets.values()), errors


def sync_from_wyze(*, full: bool = False) -> dict[str, Any]:
    """Pull new scale records for every person on every scale.

    Incremental by default: each person is fetched from their newest stored record
    (minus a 2-day overlap). The first sync for a person, or ``full=True``, pulls
    the complete history.
    """

    def run(client: Client) -> dict[str, Any]:
        scales, targets, errors = _discover_sync_targets(client)
        now = datetime.now()
        result = DbResult()
        records_seen = 0
        people: list[dict[str, Any]] = []

        with _connect() as conn:
            for target in targets:
                profile_id = target["profile_id"]
                latest = None if full else _latest_cloud_ts(conn, profile_id)
                start = (
                    datetime.fromtimestamp(latest) - timedelta(days=2)
                    if latest
                    else now - timedelta(days=FULL_HISTORY_DAYS)
                )
                label = target["name"] or profile_id or target["model"]
                try:
                    records = client.scales.get_records(
                        device_model=target["model"], user_id=profile_id, start_time=start, end_time=now
                    )
                except Exception as exc:
                    errors.append(f"{label}: {exc}")
                    people.append({"name": label, "profile_id": profile_id, "fetched": 0, "new": 0, "error": True})
                    continue

                new_for_person = 0
                for record in records:
                    records_seen += 1
                    payload = _measurement_payload(
                        record,
                        product_model=target["model"],
                        profile_id=profile_id,
                        profile_name=target["name"],
                    )
                    if _upsert_measurement(conn, payload):
                        result.inserted += 1
                        new_for_person += 1
                    else:
                        result.skipped += 1

                if profile_id and target["name"]:
                    conn.execute(
                        "UPDATE measurements SET profile_name = ? WHERE profile_id = ? AND product_model != 'IMPORT_XLSX'",
                        (target["name"], profile_id),
                    )
                people.append(
                    {
                        "name": label,
                        "profile_id": profile_id,
                        "fetched": len(records),
                        "new": new_for_person,
                        "since": start.date().isoformat(),
                    }
                )

            summary = {
                "inserted": result.inserted,
                "skipped": result.skipped,
                "records_seen": records_seen,
                "scales": scales,
                "people": people,
                "errors": errors,
                "full": full,
                "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            _set_meta(conn, "last_sync_at", str(int(now.timestamp())))
            _set_meta(conn, "last_sync_result", json.dumps(summary))
            conn.commit()
        return summary

    return wyze_auth.with_client(run)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for metric in TRACKED_METRICS:
            if item.get(metric) is not None:
                item[metric] = round(float(item[metric]), 4)
        output.append(item)
    return output


@app.get("/")
def index() -> str:
    return render_template("index.html")


MEASUREMENT_COLUMNS = (
    "id, record_id, scale_mac, device_id, product_model, profile_id, profile_name, measured_at, measure_ts, "
    + ", ".join(TRACKED_METRICS)
)

EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("measured_at", "measured_at"),
    ("profile_name", "profile_name"),
    ("profile_id", "profile_id"),
    ("weight_lb", "weight_lb"),
    ("weight_kg", "weight_kg"),
    ("bmi", "bmi"),
    ("body_fat", "body_fat"),
    ("muscle", "muscle_lb"),
    ("skeletal_muscle", "skeletal_muscle_lb"),
    ("body_water", "body_water"),
    ("protein", "protein"),
    ("bone_mineral", "bone_mineral_lb"),
    ("subcutaneous_fat", "subcutaneous_fat"),
    ("body_vfr", "body_vfr"),
    ("bmr", "bmr"),
    ("metabolic_age", "metabolic_age"),
    ("body_score", "body_score"),
    ("product_model", "product_model"),
)


@app.before_request
def _same_origin_only():
    """Block cross-site POSTs (e.g. a random web page posting to localhost)."""
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "Cross-origin request blocked"}), 403
    return None


@app.get("/api/measurements")
def api_measurements():
    limit = min(max(request.args.get("limit", type=int, default=500), 1), 5000)
    days = request.args.get("days", type=int)
    profile = (request.args.get("profile") or "").strip() or None

    inner_query = f"SELECT {MEASUREMENT_COLUMNS} FROM measurements"
    where_clause, params = _build_filter_clauses(days=days, profile=profile)
    inner_query += where_clause
    inner_query += " ORDER BY measure_ts DESC LIMIT ?"
    params.append(limit)
    query = f"SELECT * FROM ({inner_query}) ORDER BY measure_ts ASC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    return jsonify({"count": len(rows), "data": _rows_to_dicts(rows)})


@app.get("/api/profiles")
def api_profiles():
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                profile_id,
                COALESCE(MAX(NULLIF(profile_name, '')), NULLIF(profile_id, ''), 'Unknown') AS profile_name,
                COUNT(*) AS records,
                MAX(measure_ts) AS latest_ts
            FROM measurements
            GROUP BY profile_id
            ORDER BY latest_ts DESC
            """
        ).fetchall()
    data = [dict(row) for row in rows]
    return jsonify({"count": len(data), "data": data})


@app.get("/api/latest")
def api_latest():
    profile = (request.args.get("profile") or "").strip() or None
    where_clause, params = _build_filter_clauses(profile=profile)
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {MEASUREMENT_COLUMNS} FROM measurements {where_clause} ORDER BY measure_ts DESC LIMIT 1",
            params,
        ).fetchone()
    return jsonify({"data": dict(row) if row else None})


@app.get("/api/export")
def api_export():
    range_key = (request.args.get("range") or "all").strip().lower()
    if range_key not in EXPORT_RANGE_TO_DAYS:
        return jsonify({"error": "range must be one of: day, week, year, all, custom"}), 400

    profile = (request.args.get("profile") or "").strip() or None
    days = _range_to_days(range_key)
    start_ts: int | None = None
    end_ts: int | None = None
    if range_key == "custom":
        start_date = (request.args.get("start") or "").strip()
        end_date = (request.args.get("end") or "").strip()
        if not start_date or not end_date:
            return jsonify({"error": "custom export requires start and end dates (YYYY-MM-DD)"}), 400
        try:
            start_ts = _date_to_epoch(start_date, end_of_day=False)
            end_ts = _date_to_epoch(end_date, end_of_day=True)
        except ValueError:
            return jsonify({"error": "invalid custom date format, expected YYYY-MM-DD"}), 400
        if end_ts < start_ts:
            return jsonify({"error": "end date must be on or after start date"}), 400

    where_clause, params = _build_filter_clauses(days=days, profile=profile, start_ts=start_ts, end_ts=end_ts)
    select_cols = ", ".join(col for col, _ in EXPORT_COLUMNS)
    query = f"SELECT {select_cols} FROM measurements {where_clause} ORDER BY measure_ts ASC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow([header for _, header in EXPORT_COLUMNS])
    for row in rows:
        writer.writerow([row[col] for col, _ in EXPORT_COLUMNS])

    filename_parts = ["wyze-scale-export", range_key]
    if profile:
        filename_parts.append(re.sub(r"[^A-Za-z0-9_-]+", "_", profile))
    filename_parts.append(datetime.now().strftime("%Y%m%d-%H%M%S"))
    filename = "-".join(filename_parts) + ".csv"

    return Response(
        stream.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/auth/status")
def api_auth_status():
    info = wyze_auth.status()
    last_sync = _get_meta("last_sync_at")
    info["last_sync_at"] = int(last_sync) if last_sync else None
    info["desktop"] = bool(app.config.get("DESKTOP"))
    return jsonify(info)


@app.post("/api/auth/login")
def api_auth_login():
    body = request.get_json(silent=True) or {}
    creds = wyze_auth.env_credentials()
    try:
        result = wyze_auth.start_login(
            email=str(body.get("email") or creds["email"] or ""),
            # A blank password/API key in the form means "use the saved one".
            password=str(body.get("password") or creds["password"] or ""),
            key_id=str(body.get("key_id") or creds["key_id"] or ""),
            api_key=str(body.get("api_key") or creds["api_key"] or ""),
            totp_key=creds["totp_key"],
            remember_password=bool(body.get("remember_password", True)),
        )
    except wyze_auth.LoginError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/auth/verify")
def api_auth_verify():
    body = request.get_json(silent=True) or {}
    try:
        result = wyze_auth.verify_login(str(body.get("challenge_id") or ""), str(body.get("code") or ""))
    except wyze_auth.LoginError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/auth/logout")
def api_auth_logout():
    wyze_auth.clear_session()
    return jsonify({"ok": True})


@app.post("/api/sync")
def api_sync():
    body = request.get_json(silent=True) or {}
    try:
        result = sync_from_wyze(full=bool(body.get("full")))
    except (wyze_auth.NeedsLogin, wyze_auth.LoginError) as exc:
        return jsonify({"error": str(exc), "needs_login": True}), 401
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "result": result})


@app.post("/api/import")
def api_import():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "No file uploaded"}), 400
    if not upload.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
        return jsonify({"error": "Please upload a .xlsx file"}), 400

    sheet_name = request.form.get("sheet_name", "").strip() or None
    try:
        result = import_from_excel(upload.read(), filename=upload.filename, sheet_name=sheet_name)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "result": result})


def create_app() -> Flask:
    _ensure_db()
    return app


create_app()


# --------------------------------------------------------------------------- CLI


def _cli_login() -> int:
    import getpass

    creds = wyze_auth.env_credentials()

    def ask(label: str, current: str | None, secret: bool = False) -> str:
        hint = " [saved, Enter to keep]" if current else ""
        prompt = f"{label}{hint}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        return value.strip() or (current or "")

    print(f"Get a Key ID + API Key at {wyze_auth.DEVELOPER_CONSOLE_URL}")
    try:
        result = wyze_auth.start_login(
            email=ask("Wyze email", creds["email"]),
            password=ask("Wyze password", creds["password"], secret=True),
            key_id=ask("Key ID", creds["key_id"]),
            api_key=ask("API Key", creds["api_key"], secret=True),
            totp_key=creds["totp_key"],
        )
        if result["status"] == "mfa_required":
            result = wyze_auth.verify_login(result["challenge_id"], input(result["prompt"] + " "))
    except wyze_auth.LoginError as exc:
        print(f"Login failed: {exc}")
        return 1
    print("Connected. Tokens cached in data/wyze_session.json; credentials saved to .env.")
    return 0


def _cli_sync(full: bool) -> int:
    try:
        result = sync_from_wyze(full=full)
    except (wyze_auth.NeedsLogin, wyze_auth.LoginError) as exc:
        print(f"{exc}\nRun: python app.py login")
        return 1
    for person in result["people"]:
        print(f"  {person['name']}: {person['new']} new / {person['fetched']} fetched")
    for error in result["errors"]:
        print(f"  ! {error}")
    print(f"Sync complete: {result['inserted']} new, {result['skipped']} already stored.")
    return 0


def _cli_status() -> int:
    info = wyze_auth.status()
    last = _get_meta("last_sync_at")
    print(f"Connected: {info['connected']} ({info['email'] or 'no account'})")
    print(f"Saved credentials for silent re-login: {info['has_saved_credentials']}")
    if info["api_key_expires"]:
        print(f"API key expires: {datetime.fromtimestamp(info['api_key_expires']):%Y-%m-%d}")
    print(f"Last sync: {datetime.fromtimestamp(int(last)).isoformat(sep=' ') if last else 'never'}")
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(MAX(profile_name), profile_id) AS name, COUNT(*) AS n, MAX(measure_ts) AS ts
            FROM measurements GROUP BY profile_id ORDER BY ts DESC
            """
        ).fetchall()
    for row in rows:
        print(f"  {row['name']}: {row['n']} records, latest {datetime.fromtimestamp(row['ts']):%Y-%m-%d %H:%M}")
    return 0


def _serve(open_browser: bool) -> None:
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    debug = os.getenv("APP_DEBUG", "false").lower() == "true"
    if open_browser and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=(f"http://{host}:{port}",)).start()
    app.run(host=host, port=port, debug=debug)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Wyze Scale Tracker")
    sub = parser.add_subparsers(dest="command")
    desk = sub.add_parser("desktop", help="Open the desktop app (default)")
    desk.add_argument("--reconnect", action="store_true", help="Open the Wyze sign-in window to capture a new API key")
    serve = sub.add_parser("serve", help="Run as a web server for your normal browser")
    serve.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    sub.add_parser("login", help="Connect your Wyze account (handles 2FA)")
    sync = sub.add_parser("sync", help="Pull new scale records")
    sync.add_argument("--full", action="store_true", help="Re-fetch complete history")
    sub.add_parser("status", help="Show connection and data summary")
    sub.add_parser("logout", help="Forget cached Wyze tokens")
    args = parser.parse_args(argv)

    if args.command == "login":
        return _cli_login()
    if args.command == "sync":
        return _cli_sync(args.full)
    if args.command == "status":
        return _cli_status()
    if args.command == "logout":
        wyze_auth.clear_session()
        print("Cached Wyze tokens removed.")
        return 0
    if args.command == "serve":
        _serve(open_browser=not args.no_browser)
        return 0
    try:
        import desktop
    except ImportError as exc:
        print(f"Desktop mode unavailable ({exc}); falling back to the browser.")
        _serve(open_browser=True)
        return 0
    desktop.run_desktop(app, reconnect=getattr(args, "reconnect", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
