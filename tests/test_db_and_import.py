import io
import sqlite3

from openpyxl import Workbook

from tracker import config, db
from tracker.importer import import_workbook
from tracker.parsing import cloud_payload


def _row(record_id="r1", ts=1700000000, member="m1", weight=200.0):
    raw = {"id": record_id, "mac": "AA", "family_member_id": member, "measure_ts": ts, "weight": weight}
    return cloud_payload(raw, weight_kg=None, product_model="WL_SCU", profile_name="Me")


def test_upsert_dedupes_on_record_id():
    with db.connect() as conn:
        assert db.upsert_measurement(conn, _row()) is True
        assert db.upsert_measurement(conn, _row()) is False
        assert db.upsert_measurement(conn, _row(record_id="r2", ts=1700000100)) is True
    assert len(db.measurements()) == 2


def test_connect_closes_and_rolls_back():
    try:
        with db.connect() as conn:
            db.upsert_measurement(conn, _row())
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.measurements() == []
    try:
        conn.execute("SELECT 1")
        raise AssertionError("connection should be closed")
    except sqlite3.ProgrammingError:
        pass


def test_profiles_and_latest_cloud_ts():
    with db.connect() as conn:
        db.upsert_measurement(conn, _row("a", 100, "m1"))
        db.upsert_measurement(conn, _row("b", 200, "m1"))
        db.upsert_measurement(conn, _row("c", 150, "m2"))
        assert db.latest_cloud_ts(conn, "m1") == 200
        assert db.latest_cloud_ts(conn, "m2") == 150
    profiles = db.profiles()
    assert profiles[0]["profile_id"] == "m1" and profiles[0]["records"] == 2


def test_migration_adds_columns_to_old_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("WST_DATA_DIR", str(tmp_path / "old"))
    config.data_dir().mkdir(parents=True)
    conn = sqlite3.connect(config.db_path())
    conn.execute(
        """CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL,
        scale_mac TEXT NOT NULL, device_id TEXT, product_model TEXT, measured_at TEXT NOT NULL, measure_ts INTEGER,
        weight_lb REAL, weight_kg REAL, bmi REAL, body_fat REAL, muscle REAL, body_water REAL, protein REAL,
        bone_mineral REAL, body_vfr REAL, bmr REAL, metabolic_age INTEGER, raw_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(record_id, scale_mac))"""
    )
    conn.execute(
        "INSERT INTO measurements (record_id, scale_mac, product_model, measured_at, measure_ts, weight_lb, raw_json)"
        " VALUES ('x', 'AA', 'WL_SCU', '2026-01-01', 1, 200, ?)",
        ('{"family_member_id": "m9", "skeletal_muscle": 35.0, "subcutaneous_fat": -1}',),
    )
    conn.commit()
    conn.close()

    db.ensure_db()
    row = db.measurements()[0]
    assert row["profile_id"] == "m9"
    assert row["skeletal_muscle"] == 77.16
    assert row["subcutaneous_fat"] is None


def _workbook_bytes(rows, sheet="Alex"):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["Wyze export"])  # junk row above the header
    ws.append(["Number", "Date and Time", "Weight(lb)", "Body Fat", "Muscle Mass"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_import_workbook_and_reimport_is_idempotent():
    data = _workbook_bytes(
        [
            [1, "2026.01.02 07:30 AM", "180.2 lb", "22.1%", "130 lb"],
            [2, "2026.01.03 07:31 AM", "179.8 lb", "--", "--"],
            [3, "not a date", "179 lb", "", ""],
        ]
    )
    first = import_workbook(data, filename="export.xlsx")
    assert first["inserted"] == 2 and first["skipped"] == 0
    assert len(first["parse_errors"]) == 1
    second = import_workbook(data, filename="export.xlsx")
    assert second["inserted"] == 0 and second["skipped"] == 2
    rows = db.measurements(profile="import:Alex")
    assert [r["weight_lb"] for r in rows] == [180.2, 179.8]
    assert rows[1]["body_fat"] is None
