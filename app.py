from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from openpyxl import load_workbook
from wyze_sdk import Client

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "measurements.db"
POUNDS_PER_KG = 2.20462262185
FALLBACK_MODELS = ("WL_SC2", "WL_SC3", "JA.SC2", "JA.SC")

TRACKED_METRICS = (
    "weight_lb",
    "weight_kg",
    "bmi",
    "body_fat",
    "muscle",
    "body_water",
    "protein",
    "bone_mineral",
    "body_vfr",
    "bmr",
    "metabolic_age",
)

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
}

EXPORT_RANGE_TO_DAYS: dict[str, int | None] = {
    "day": 1,
    "week": 7,
    "year": 365,
    "all": None,
    "custom": None,
}

load_dotenv(BASE_DIR / ".env")
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
        columns = _table_columns(conn, "measurements")
        if "profile_id" not in columns:
            conn.execute("ALTER TABLE measurements ADD COLUMN profile_id TEXT")
        if "profile_name" not in columns:
            conn.execute("ALTER TABLE measurements ADD COLUMN profile_name TEXT")
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


def _latest_measure_ts(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(measure_ts) AS ts FROM measurements").fetchone()
    if row is None:
        return None
    return _safe_int(row["ts"])


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _make_client() -> Client:
    email = _require_env("WYZE_EMAIL")
    password = _require_env("WYZE_PASSWORD")
    key_id = _require_env("WYZE_KEY_ID")
    api_key = _require_env("WYZE_API_KEY")
    totp_key = os.getenv("WYZE_TOTP_KEY", "").strip() or None
    timeout = _safe_int(os.getenv("WYZE_TIMEOUT_SECONDS")) or 15
    return Client(
        email=email,
        password=password,
        key_id=key_id,
        api_key=api_key,
        totp_key=totp_key,
        timeout=timeout,
    )


def _measurement_payload(
    record: Any,
    *,
    product_model: str,
    scale_mac: str | None = None,
    profile_name_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw = record.to_dict()

    measure_ts = _normalize_epoch_seconds(raw.get("measure_ts"))
    measured_at = datetime.fromtimestamp(measure_ts).isoformat() if measure_ts else datetime.utcnow().isoformat()
    resolved_scale_mac = str(scale_mac or raw.get("mac") or f"model:{product_model}")
    record_id = str(raw.get("id") or raw.get("data_id") or f"{resolved_scale_mac}-{measure_ts or 'na'}")
    profile_id = str(raw.get("family_member_id") or raw.get("user_id") or "")
    if profile_id == "":
        profile_id = None
    profile_name = profile_name_map.get(profile_id) if (profile_id and profile_name_map) else None

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
        "raw_json": json.dumps(raw, separators=(",", ":"), sort_keys=True),
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
            bone_mineral, body_vfr, bmr, metabolic_age, raw_json
        ) VALUES (
            :record_id, :scale_mac, :device_id, :product_model, :measured_at, :measure_ts,
            :profile_id, :profile_name,
            :weight_lb, :weight_kg, :bmi, :body_fat, :muscle, :body_water, :protein,
            :bone_mineral, :body_vfr, :bmr, :metabolic_age, :raw_json
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
        "imported_at": datetime.utcnow().isoformat() + "Z",
    }


def sync_from_wyze(*, lookback_days: int = 365) -> dict[str, Any]:
    client = _make_client()
    profile_name_map: dict[str, str] = {}
    skip_scale_list = os.getenv("WYZE_SKIP_SCALE_LIST", "true").strip().lower() == "true"

    scales: list[Any] = []
    scale_list_error: str | None = None
    if not skip_scale_list:
        try:
            scales = list(client.scales.list())
        except Exception as exc:
            scale_list_error = str(exc)

    scale_descriptors = [
        {
            "mac": scale.mac,
            "model": scale.product.model if scale.product else None,
            "nickname": scale.nickname,
        }
        for scale in scales
    ]

    now = datetime.now()
    with _connect() as conn:
        latest_ts = _latest_measure_ts(conn)
        start_time = now - timedelta(days=lookback_days)
        if latest_ts:
            start_time = datetime.fromtimestamp(latest_ts) - timedelta(days=2)
        for profile_id, profile_name in profile_name_map.items():
            conn.execute(
                """
                UPDATE measurements
                SET profile_name = ?
                WHERE profile_id = ? AND (profile_name IS NULL OR profile_name = '')
                """,
                (profile_name, profile_id),
            )

        result = DbResult()
        records_seen = 0
        scale_errors: list[str] = []
        records_by_model: dict[str, int] = {}

        listed_models = [item["model"] for item in scale_descriptors if item.get("model")]
        models_attempted = list(dict.fromkeys(listed_models or list(FALLBACK_MODELS)))
        fallback_used = len(listed_models) == 0

        for model in models_attempted:
            try:
                records = client.scales.get_records(
                    device_model=model,
                    start_time=start_time,
                    end_time=now,
                )
            except Exception as exc:
                records_by_model[model] = 0
                scale_errors.append(f"{model}: {exc}")
                continue

            records_by_model[model] = len(records)
            for record in records:
                records_seen += 1
                payload = _measurement_payload(
                    record,
                    product_model=model,
                    profile_name_map=profile_name_map,
                )
                if _upsert_measurement(conn, payload):
                    result.inserted += 1
                else:
                    result.skipped += 1

            # Avoid calling the same endpoint variants repeatedly when device listing is empty.
            if fallback_used and records:
                break

        conn.commit()

    hints: list[str] = []
    if skip_scale_list:
        hints.append("Device list step skipped; using direct model queries for faster/more reliable sync.")
    if not scales and records_seen > 0:
        hints.append("Scale list endpoint returned 0 devices, but fallback model query returned records.")
    if records_seen == 0:
        hints.append("Wyze returned no records. If export has data, use Import Excel and keep Sync for future checks.")

    return {
        "inserted": result.inserted,
        "skipped": result.skipped,
        "records_seen": records_seen,
        "scales_found": len(scales),
        "scales_listed": scale_descriptors,
        "models_attempted": models_attempted,
        "records_by_model": records_by_model,
        "fallback_used": fallback_used,
        "scale_errors": scale_errors,
        "scale_list_error": scale_list_error,
        "hints": hints,
        "lookback_days": lookback_days,
        "synced_at": datetime.utcnow().isoformat() + "Z",
    }


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


@app.get("/api/measurements")
def api_measurements():
    limit = min(max(request.args.get("limit", type=int, default=500), 1), 5000)
    days = request.args.get("days", type=int)
    profile = (request.args.get("profile") or "").strip() or None

    inner_query = """
        SELECT id, record_id, scale_mac, device_id, product_model, profile_id, profile_name, measured_at, measure_ts,
               weight_lb, weight_kg, bmi, body_fat, muscle, body_water, protein,
               bone_mineral, body_vfr, bmr, metabolic_age
        FROM measurements
    """
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
                COALESCE(NULLIF(profile_name, ''), NULLIF(profile_id, ''), 'Unknown') AS profile_name,
                COUNT(*) AS records,
                MAX(measure_ts) AS latest_ts
            FROM measurements
            GROUP BY profile_id, COALESCE(NULLIF(profile_name, ''), NULLIF(profile_id, ''), 'Unknown')
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
            f"""
            SELECT id, record_id, profile_id, profile_name, measured_at, measure_ts, weight_lb, weight_kg, bmi,
                   body_fat, muscle, body_water, protein, bone_mineral, body_vfr, bmr, metabolic_age
            FROM measurements
            {where_clause}
            ORDER BY measure_ts DESC
            LIMIT 1
            """,
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

    where_clause, params = _build_filter_clauses(
        days=days,
        profile=profile,
        start_ts=start_ts,
        end_ts=end_ts,
    )

    query = f"""
        SELECT measured_at, profile_name, profile_id, weight_lb, weight_kg, bmi, body_fat, muscle,
               body_water, protein, bone_mineral, body_vfr, bmr, metabolic_age, product_model
        FROM measurements
        {where_clause}
        ORDER BY measure_ts ASC
    """

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "measured_at",
            "profile_name",
            "profile_id",
            "weight_lb",
            "weight_kg",
            "bmi",
            "body_fat",
            "muscle_lb",
            "body_water",
            "protein",
            "bone_mineral_lb",
            "body_vfr",
            "bmr",
            "metabolic_age",
            "product_model",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["measured_at"],
                row["profile_name"],
                row["profile_id"],
                row["weight_lb"],
                row["weight_kg"],
                row["bmi"],
                row["body_fat"],
                row["muscle"],
                row["body_water"],
                row["protein"],
                row["bone_mineral"],
                row["body_vfr"],
                row["bmr"],
                row["metabolic_age"],
                row["product_model"],
            ]
        )

    filename_parts = ["wyze-scale-export", range_key]
    if profile:
        filename_parts.append(profile.replace(":", "_"))
    filename_parts.append(datetime.now().strftime("%Y%m%d-%H%M%S"))
    filename = "-".join(filename_parts) + ".csv"

    return Response(
        stream.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sync")
def api_sync():
    body = request.get_json(silent=True) or {}
    lookback_days = body.get("lookback_days", 365)
    try:
        lookback_days = max(1, int(lookback_days))
    except (TypeError, ValueError):
        return jsonify({"error": "lookback_days must be an integer"}), 400

    try:
        result = sync_from_wyze(lookback_days=lookback_days)
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


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    debug = os.getenv("APP_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
