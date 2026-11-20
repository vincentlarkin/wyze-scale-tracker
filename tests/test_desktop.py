"""Capture-flow logic of the desktop shell, with windows and Wyze faked."""

import threading

import pytest

desktop = pytest.importorskip("tracker.desktop")

KEY_ID = "00000000-1111-4222-8333-444444444444"


class FakeWindow:
    def __init__(self, url):
        self.url, self.js, self.loaded, self.destroyed = url, [], [], False

    def get_current_url(self):
        return self.url

    def evaluate_js(self, js):
        self.js.append(js)

    def destroy(self):
        self.destroyed = True

    def load_url(self, url):
        self.loaded.append(url)


@pytest.fixture
def shell(monkeypatch):
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    s = desktop.DesktopShell.__new__(desktop.DesktopShell)
    s.base_url = "http://127.0.0.1:9999"
    s.captured, s._finishing = {}, False
    s.main_window = FakeWindow("http://127.0.0.1:9999/")
    s.connect_window = FakeWindow("https://developer-api-console.wyze.com/#/apikey/view")
    return s


def capture(shell, key="A" * 60, expires=None):
    done = threading.Event()
    original = shell._finish

    def wrapped(*args):
        original(*args)
        done.set()

    shell._finish = wrapped
    desktop.CaptureApi(shell).report_api_key(KEY_ID, key, "test", expires)
    assert done.wait(5)


def test_full_capture_logs_in_and_reloads(shell, monkeypatch):
    logins = []
    monkeypatch.setattr(desktop.auth, "start_login", lambda **kw: logins.append(kw) or {"status": "ok"})
    api = desktop.CaptureApi(shell)
    shell.connect_window.url = "https://auth.wyze.com/login"
    api.report_credentials("me@example.com", "pw123")
    shell.connect_window.url = "https://developer-api-console.wyze.com/#/apikey/view"
    capture(shell, expires=1900000000)
    assert logins[0]["password"] == "pw123" and logins[0]["key_id"] == KEY_ID
    assert shell.connect_window.destroyed and "Countdown" in shell.connect_window.js[0]
    assert shell.main_window.loaded == ["http://127.0.0.1:9999/?connected=1"]
    assert desktop.auth.status()["api_key_expires"] == 1900000000


def test_non_wyze_pages_cannot_report(shell):
    api = desktop.CaptureApi(shell)
    shell.connect_window.url = "https://evil.example.com/"
    api.report_credentials("x@y.com", "stolen")
    api.report_api_key(KEY_ID, "C" * 60)
    assert shell.captured == {} and not shell._finishing


def test_masked_or_malformed_keys_ignored(shell):
    api = desktop.CaptureApi(shell)
    api.report_api_key(KEY_ID, "x" * 16)  # getApiKey returns a 16-char masked key for existing keys
    api.report_api_key("not-a-uuid", "A" * 60)
    assert not shell._finishing


def test_no_password_saves_key_and_asks(shell):
    capture(shell)
    assert "connect_error=" in shell.main_window.loaded[0]
    assert desktop.auth.saved_credentials()["api_key"] == "A" * 60


def test_mfa_hands_off_to_dashboard(shell, monkeypatch):
    shell.captured = {"email": "me@example.com", "password": "pw"}
    challenge = {"status": "mfa_required", "challenge_id": "CH1", "prompt": "SMS code"}
    monkeypatch.setattr(desktop.auth, "start_login", lambda **kw: challenge)
    capture(shell)
    assert "challenge=CH1" in shell.main_window.loaded[0] and "prompt=SMS%20code" in shell.main_window.loaded[0]


def test_certificate_requires_current_host(shell):
    api = desktop.CaptureApi(shell)
    assert api.get_certificate("other.example.com")["ok"] is False


def test_issuer_label():
    assert desktop._issuer_label("Amazon", "Amazon RSA 2048 M01") == "Amazon RSA 2048 M01"
    assert desktop._issuer_label("Let's Encrypt", "R11") == "Let's Encrypt R11"
