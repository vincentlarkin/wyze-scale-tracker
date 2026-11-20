"""Paths and constants. Paths are functions so tests can redirect them via env vars."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
POUNDS_PER_KG = 2.20462262185
FULL_HISTORY_DAYS = 3650
DEVELOPER_CONSOLE_URL = "https://developer-api-console.wyze.com/#/apikey/view"
AUTH_API = "https://auth-prod.api.wyze.com"

# Used only when the device list call fails. The model just picks the backend:
# JA.SC/JA.SC2 use the original scale service, every newer scale uses "Pluto".
FALLBACK_MODELS = ("WL_SC2", "JA.SC2")

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


def data_dir() -> Path:
    return Path(os.getenv("WST_DATA_DIR") or BASE_DIR / "data")


def db_path() -> Path:
    return data_dir() / "measurements.db"


def session_path() -> Path:
    return data_dir() / "wyze_session.json"


def env_path() -> Path:
    return Path(os.getenv("WST_ENV_FILE") or BASE_DIR / ".env")


def load_env() -> None:
    load_dotenv(env_path())
