from datetime import datetime

import pytest

from tracker import parsing


@pytest.mark.parametrize(
    ("value", "expected"),
    [("72.4 kg", 72.4), ("18.2%", 18.2), ("1,713", 1713.0), ("--", None), ("N/A", None), ("", None), (5, 5.0)],
)
def test_strip_numeric(value, expected):
    assert parsing.strip_numeric(value) == expected


def test_epoch_seconds_accepts_ms_and_s():
    assert parsing.epoch_seconds(1700000000123) == 1700000000
    assert parsing.epoch_seconds(1700000000) == 1700000000
    assert parsing.epoch_seconds(None) is None


def test_positive_treats_zero_and_negative_as_missing():
    assert parsing.positive(-2.5) is None
    assert parsing.positive(0) is None
    assert parsing.positive("30.5") == 30.5


def test_cloud_payload_converts_masses_to_lb():
    raw = {
        "id": "r1",
        "mac": "AA:BB",
        "family_member_id": "member-1",
        "measure_ts": 1700000000123,
        "weight": 180.0,  # lb, as wyze-sdk reports it
        "muscle": 50.0,  # kg
        "bone_mineral": 3.0,  # kg
        "skeletal_muscle": 30.0,  # kg
        "subcutaneous_fat": 20.0,
        "body_fat": 25.0,
        "metabolic_age": 30,
    }
    row = parsing.cloud_payload(raw, weight_kg=81.65, product_model="WL_SCU")
    assert row["weight_lb"] == 180.0
    assert row["weight_kg"] == 81.65
    assert row["muscle"] == pytest.approx(110.23, abs=0.01)
    assert row["bone_mineral"] == pytest.approx(6.61, abs=0.01)
    assert row["skeletal_muscle"] == pytest.approx(66.14, abs=0.01)
    assert row["profile_id"] == "member-1"
    assert row["measure_ts"] == 1700000000


def test_cloud_payload_weight_only_reading():
    raw = {"id": "r2", "measure_ts": 1700000000, "weight": 190.0, "skeletal_muscle": -2.5}
    row = parsing.cloud_payload(raw, weight_kg=None, product_model="WL_SCU", profile_id="p")
    assert row["skeletal_muscle"] is None
    assert row["weight_kg"] == pytest.approx(86.18, abs=0.01)
    assert row["profile_id"] == "p"


def test_import_payload_requires_weight():
    when = datetime(2026, 1, 2, 7, 30)
    assert parsing.import_payload(sheet_name="A", row_index=2, row_data={"bmi": "22"}, measured_at=when) is None
    row = parsing.import_payload(sheet_name="A", row_index=2, row_data={"weight_kg": "80"}, measured_at=when)
    assert row["weight_lb"] == pytest.approx(176.37, abs=0.01)
    assert row["profile_id"] == "import:A"
