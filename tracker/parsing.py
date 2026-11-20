"""Pure helpers that turn raw Wyze records / spreadsheet cells into DB rows."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from .config import POUNDS_PER_KG


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def positive(value: Any) -> float | None:
    """Composition values <= 0 mean 'not measured' (e.g. weight-only readings)."""
    number = safe_float(value)
    return number if number is not None and number > 0 else None


def epoch_seconds(value: Any) -> int | None:
    ts = safe_int(value)
    if ts is None:
        return None
    return ts // 1000 if ts > 10_000_000_000 else ts  # Wyze sends milliseconds


def strip_numeric(value: Any) -> float | None:
    """Parse '72.4 kg', '18.2%', '1,713' etc. Placeholders ('-', '--', 'N/A') -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "--", "- -", "N/A", "n/a"}:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text.replace(",", ""))
    return safe_float(match.group(0)) if match else None


def kg_to_lb(value: float | None) -> float | None:
    return None if value is None else round(value * POUNDS_PER_KG, 2)


def lb_to_kg(value: float | None) -> float | None:
    return None if value is None else round(value / POUNDS_PER_KG, 4)


def parse_export_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y.%m.%d %I:%M %p", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def cloud_payload(
    raw: dict[str, Any],
    *,
    weight_kg: float | None,
    product_model: str,
    profile_id: str | None = None,
    profile_name: str | None = None,
) -> dict[str, Any]:
    """Row for a Wyze cloud record. ``raw`` is ScaleRecord.to_dict(); ``weight`` there is lb."""
    measure_ts = epoch_seconds(raw.get("measure_ts"))
    measured_at = datetime.fromtimestamp(measure_ts).isoformat() if measure_ts else datetime.now().isoformat()
    scale_mac = str(raw.get("mac") or f"model:{product_model}")
    record_id = str(raw.get("id") or raw.get("data_id") or f"{scale_mac}-{measure_ts or 'na'}")
    profile_id = str(raw.get("family_member_id") or raw.get("user_id") or profile_id or "") or None

    weight_lb = safe_float(raw.get("weight"))
    if weight_kg is None:
        weight_kg = lb_to_kg(weight_lb)
    if weight_lb is None:
        weight_lb = kg_to_lb(weight_kg)

    return {
        "record_id": record_id,
        "scale_mac": scale_mac,
        "device_id": raw.get("device_id"),
        "product_model": product_model,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "measure_ts": measure_ts,
        "measured_at": measured_at,
        "weight_lb": weight_lb,
        "weight_kg": weight_kg,
        "bmi": safe_float(raw.get("bmi")),
        "body_fat": safe_float(raw.get("body_fat")),
        # Mass values arrive in kg; store lb to match the Wyze app export.
        "muscle": kg_to_lb(safe_float(raw.get("muscle"))),
        "body_water": safe_float(raw.get("body_water")),
        "protein": safe_float(raw.get("protein")),
        "bone_mineral": kg_to_lb(safe_float(raw.get("bone_mineral"))),
        "body_vfr": safe_float(raw.get("body_vfr")),
        "bmr": safe_float(raw.get("bmr")),
        "metabolic_age": safe_int(raw.get("metabolic_age")),
        "skeletal_muscle": kg_to_lb(positive(raw.get("skeletal_muscle"))),
        "subcutaneous_fat": positive(raw.get("subcutaneous_fat")),
        "body_score": safe_int(raw.get("body_score")),
        "raw_json": json.dumps(raw, separators=(",", ":"), sort_keys=True, default=str),
    }


def import_payload(
    *, sheet_name: str, row_index: int, row_data: dict[str, Any], measured_at: datetime
) -> dict[str, Any] | None:
    """Row for one line of a Wyze app .xlsx export (already in lb)."""
    weight_lb = strip_numeric(row_data.get("weight_lb"))
    weight_kg = strip_numeric(row_data.get("weight_kg"))
    if weight_lb is None and weight_kg is None:
        return None
    if weight_lb is None:
        weight_lb = kg_to_lb(weight_kg)
    if weight_kg is None:
        weight_kg = lb_to_kg(weight_lb)

    measure_ts = int(measured_at.timestamp())
    seq = safe_int(strip_numeric(row_data.get("row_number"))) or row_index
    num = lambda key: strip_numeric(row_data.get(key))  # noqa: E731
    return {
        "record_id": f"import-{sheet_name}-{measure_ts}-{seq}",
        "scale_mac": f"import:{sheet_name}",
        "device_id": None,
        "product_model": "IMPORT_XLSX",
        "profile_id": f"import:{sheet_name}",
        "profile_name": sheet_name,
        "measure_ts": measure_ts,
        "measured_at": measured_at.isoformat(),
        "weight_lb": weight_lb,
        "weight_kg": weight_kg,
        "bmi": num("bmi"),
        "body_fat": num("body_fat"),
        "muscle": num("muscle"),
        "body_water": num("body_water"),
        "protein": num("protein"),
        "bone_mineral": num("bone_mineral"),
        "body_vfr": num("body_vfr"),
        "bmr": num("bmr"),
        "metabolic_age": safe_int(num("metabolic_age")),
        "skeletal_muscle": num("skeletal_muscle"),
        "subcutaneous_fat": num("subcutaneous_fat"),
        "body_score": safe_int(num("body_score")),
        "raw_json": json.dumps(
            {"source": "xlsx", "sheet": sheet_name, "row_index": row_index, "row": row_data},
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
    }
