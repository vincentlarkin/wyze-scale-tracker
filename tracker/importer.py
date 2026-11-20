"""Import a Wyze app .xlsx export (one worksheet per person)."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from typing import Any

from openpyxl import load_workbook

from . import db
from .parsing import import_payload, parse_export_datetime

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
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


def _normalize_header(value: Any) -> str:
    return "" if value is None else re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def find_header_map(sheet) -> tuple[dict[str, int], int] | tuple[None, None]:
    """Locate the header row (within the first 25 rows) and map our fields to columns."""
    for row_idx in range(1, min(25, sheet.max_row) + 1):
        by_name = {}
        for col_idx in range(1, sheet.max_column + 1):
            key = _normalize_header(sheet.cell(row=row_idx, column=col_idx).value)
            if key:
                by_name[key] = col_idx
        mapping = {}
        for target, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in by_name:
                    mapping[target] = by_name[alias]
                    break
        if "date_time" in mapping and ("weight_lb" in mapping or "weight_kg" in mapping):
            return mapping, row_idx
    return None, None


def import_workbook(
    file_bytes: bytes, *, filename: str = "upload.xlsx", sheet_name: str | None = None
) -> dict[str, Any]:
    workbook = load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheets = [workbook[sheet_name]] if sheet_name else workbook.worksheets
    inserted = skipped = 0
    parse_errors: list[str] = []
    summaries: list[dict[str, Any]] = []

    with db.connect() as conn:
        for sheet in sheets:
            header_map, header_row = find_header_map(sheet)
            if header_map is None:
                summaries.append({"sheet": sheet.title, "rows_seen": 0, "rows_loaded": 0, "header_found": False})
                continue
            rows_seen = rows_loaded = 0
            for row_idx in range(header_row + 1, sheet.max_row + 1):
                date_value = sheet.cell(row=row_idx, column=header_map["date_time"]).value
                if date_value is None or str(date_value).strip() == "":
                    continue
                rows_seen += 1
                measured_at = parse_export_datetime(date_value)
                if measured_at is None:
                    parse_errors.append(f"{sheet.title}: row {row_idx} has invalid date '{date_value}'")
                    continue
                row_data = {key: sheet.cell(row=row_idx, column=col).value for key, col in header_map.items()}
                payload = import_payload(
                    sheet_name=sheet.title, row_index=row_idx, row_data=row_data, measured_at=measured_at
                )
                if payload is None:
                    continue
                rows_loaded += 1
                if db.upsert_measurement(conn, payload):
                    inserted += 1
                else:
                    skipped += 1
            summaries.append(
                {"sheet": sheet.title, "rows_seen": rows_seen, "rows_loaded": rows_loaded, "header_found": True}
            )

    return {
        "filename": filename,
        "inserted": inserted,
        "skipped": skipped,
        "sheets_processed": len(sheets),
        "sheet_summaries": summaries,
        "parse_errors": parse_errors[:20],
        "imported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
