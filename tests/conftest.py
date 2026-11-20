import os

import pytest

# Never touch the real credential store, .env or database from tests.
os.environ["WST_SECRET_BACKEND"] = "file"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WST_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("WST_SECRET_BACKEND", "file")
    for name in (
        "WYZE_EMAIL",
        "WYZE_PASSWORD",
        "WYZE_KEY_ID",
        "WYZE_API_KEY",
        "WYZE_TOTP_KEY",
        "WYZE_API_KEY_EXPIRES",
    ):
        monkeypatch.delenv(name, raising=False)
    from tracker import auth, db

    auth._challenges.clear()
    db.ensure_db()
    yield tmp_path


@pytest.fixture
def client():
    from tracker.web import create_app

    app = create_app()
    app.testing = True
    return app.test_client()
