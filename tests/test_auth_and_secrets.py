import json

import pytest
import requests

from tracker import auth, config, secret_store


@pytest.fixture
def wyze(monkeypatch):
    """Fake Wyze auth endpoints. Set .mode to 'plain' or 'sms'."""

    class Fake:
        mode = "plain"
        calls: list[str] = []

        def __call__(self, path, *, key_id, api_key, json_body, params=None):
            self.calls.append(path)
            if path == "/api/user/login" and self.mode == "plain":
                return {"access_token": "A1", "refresh_token": "R1", "user_id": "U1"}
            if path == "/api/user/login" and self.mode == "sms":
                return {"access_token": None, "mfa_options": ["PrimaryPhone"], "sms_session_id": "S", "user_id": "U1"}
            if path == "/user/login/sendSmsCode":
                return {"session_id": "V1"}
            if path == "/user/login":
                if json_body["verification_code"] != "123456":
                    raise auth.LoginError("bad code")
                return {"access_token": "A2", "refresh_token": "R2", "user_id": "U1"}
            raise AssertionError(path)

    fake = Fake()
    fake.calls = []
    monkeypatch.setattr(auth, "_post_auth", fake)
    return fake


def login(**kw):
    args = {"email": "me@example.com", "password": "pw", "key_id": "K", "api_key": "AK"}
    return auth.start_login(**{**args, **kw})


def test_plain_login_stores_secrets_outside_env(wyze):
    assert login()["status"] == "ok"
    env = config.env_path().read_text()
    assert "WYZE_EMAIL=me@example.com" in env and "WYZE_KEY_ID=K" in env
    assert "pw" not in env and "AK" not in env and "A1" not in env
    session_file = json.loads(config.session_path().read_text())
    assert "access_token" not in session_file and session_file["user_id"] == "U1"
    assert auth.load_session()["access_token"] == "A1"
    assert secret_store.get_secret("WYZE_PASSWORD") == "pw"


def test_remember_password_false(wyze):
    login(remember_password=False)
    assert secret_store.get_secret("WYZE_PASSWORD") is None
    assert auth.status()["has_saved_credentials"] is False


def test_sms_2fa_flow(wyze):
    wyze.mode = "sms"
    first = login()
    assert first["status"] == "mfa_required" and first["mfa_type"] == "PrimaryPhone"
    with pytest.raises(auth.LoginError):
        auth.verify_login(first["challenge_id"], "000000")
    # A used challenge is gone; start again.
    second = login()
    assert auth.verify_login(second["challenge_id"], " 123456 ")["status"] == "ok"
    assert auth.load_session()["access_token"] == "A2"


def test_unknown_challenge_rejected():
    with pytest.raises(auth.LoginError, match="expired"):
        auth.verify_login("nope", "1")


def test_needs_login_without_credentials():
    with pytest.raises(auth.NeedsLogin):
        auth.with_client(lambda c: None)


def test_with_client_refreshes_on_auth_error(wyze, monkeypatch):
    login()
    calls = []

    def fake_refresh(session):
        calls.append("refresh")
        return auth.save_session(access_token="A9", refresh_token="R9")

    monkeypatch.setattr(auth, "refresh", fake_refresh)

    def fn(client):
        calls.append(client._token)
        if client._token == "A1":
            resp = requests.Response()
            resp.status_code = 401
            raise requests.HTTPError(response=resp)
        return "ok"

    assert auth.with_client(fn) == "ok"
    assert calls == ["A1", "refresh", "A9"]


def test_with_client_relogs_in_when_refresh_fails(wyze, monkeypatch):
    login()
    monkeypatch.setattr(auth, "refresh", lambda s: (_ for _ in ()).throw(RuntimeError("refresh dead")))
    wyze.calls.clear()

    def fn(client):
        if client._token == "A1" and "/api/user/login" not in wyze.calls:
            resp = requests.Response()
            resp.status_code = 401
            raise requests.HTTPError(response=resp)
        return client._token

    assert auth.with_client(fn) == "A1"
    assert wyze.calls == ["/api/user/login"]


def test_legacy_tokens_in_session_json_are_migrated():
    config.data_dir().mkdir(parents=True, exist_ok=True)
    config.session_path().write_text(json.dumps({"access_token": "OLD-A", "refresh_token": "OLD-R", "user_id": "U"}))
    session = auth.load_session()
    assert session["access_token"] == "OLD-A"
    assert "access_token" not in json.loads(config.session_path().read_text())


def test_env_secrets_migrate_out_of_dotenv(monkeypatch):
    config.env_path().write_text("WYZE_EMAIL=me@example.com\nWYZE_PASSWORD=pw\nWYZE_API_KEY=AK\nAPP_PORT=8000\n")
    moved = secret_store.migrate_env_secrets()
    assert sorted(moved) == ["WYZE_API_KEY", "WYZE_PASSWORD"]
    text = config.env_path().read_text()
    assert "WYZE_PASSWORD" not in text and "WYZE_API_KEY" not in text and "APP_PORT=8000" in text
    assert secret_store.get_secret("WYZE_PASSWORD") == "pw"


def test_explicit_env_var_overrides_store(monkeypatch):
    secret_store.set_secret("WYZE_PASSWORD", "stored")
    monkeypatch.setenv("WYZE_PASSWORD", "from-env")
    assert secret_store.get_secret("WYZE_PASSWORD") == "from-env"


def test_clear_session_removes_tokens(wyze):
    login()
    auth.clear_session()
    assert auth.load_session() is None
    assert secret_store.get_secret("access_token") is None
