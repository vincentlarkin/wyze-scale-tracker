"""Desktop shell: the dashboard in a native window, plus a one-time Wyze sign-in window.

On first boot (no saved credentials) a second window opens on the Wyze developer
console. While the user signs in and creates an API key, a small injected script
picks up the email/password submitted on Wyze's sign-in page and the Key ID /
API Key once the console shows them. The window then counts down, closes, and the
app logs in and reloads the dashboard. Nothing is sent anywhere except Wyze.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import webview
from werkzeug.serving import make_server

from . import auth, config

CONSOLE_URL = config.DEVELOPER_CONSOLE_URL
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
API_KEY_RE = re.compile(r"^[A-Za-z0-9]{40,100}$")
DEBUG = os.getenv("WST_CAPTURE_DEBUG", "").lower() in ("1", "true")

# Injected into every page the sign-in window loads (address bar everywhere,
# credential/key capture only on *.wyze.com).
CAPTURE_JS = (Path(__file__).with_name("capture.js")).read_text(encoding="utf-8")


class CaptureApi:
    """Exposed to the sign-in window as window.pywebview.api."""

    def __init__(self, shell: DesktopShell):
        self._shell = shell

    def _current_host(self) -> str:
        window = self._shell.connect_window
        try:
            return urlparse(window.get_current_url() or "").hostname or ""
        except Exception:
            return ""

    def _from_wyze(self) -> bool:
        host = self._current_host()
        return host == "wyze.com" or host.endswith(".wyze.com")

    def get_certificate(self, host: str) -> dict:
        """Independently fetch + verify the TLS certificate of the page's host."""
        if not host or host != self._current_host():
            return {"ok": False, "error": "host mismatch"}
        return fetch_certificate(host)

    def report_credentials(self, email: str, password: str) -> None:
        if not self._from_wyze():
            return
        if email and "@" in email:
            self._shell.captured["email"] = email.strip()
        if password:
            self._shell.captured["password"] = password

    def report_api_key(self, key_id: str, api_key: str, source: str = "", expires: int | None = None) -> None:
        if not self._from_wyze():
            return
        key_id, api_key = (key_id or "").strip(), (api_key or "").strip()
        if not (UUID_RE.match(key_id) and API_KEY_RE.match(api_key)):
            if DEBUG:
                print(f"[capture] ignored candidate from {source}: id={len(key_id)} chars, key={len(api_key)} chars")
            return
        if DEBUG:
            print(f"[capture] API key captured from {source}")
        try:
            expires_ts = int(expires) if expires else None
        except (TypeError, ValueError):
            expires_ts = None
        self._shell.on_api_key(key_id, api_key, expires_ts)

    def debug_shape(self, url: str, shape: str) -> None:
        if DEBUG:
            print(f"[capture] {url} -> {shape[:500]}")


class MainApi:
    """Exposed to the dashboard window as window.pywebview.api."""

    def __init__(self, shell: DesktopShell):
        self._shell = shell

    def open_wyze_login(self) -> bool:
        self._shell.open_connect_window()
        return True


class DesktopShell:
    def __init__(self, flask_app, port: int):
        self.flask_app = flask_app
        self.base_url = f"http://127.0.0.1:{port}"
        self.server = make_server("127.0.0.1", port, flask_app, threaded=True)
        self.main_window = None
        self.connect_window = None
        self.captured: dict[str, str] = {}
        self._finishing = False

    # ---- windows -----------------------------------------------------------
    def open_connect_window(self) -> None:
        if self.connect_window is not None:
            try:
                self.connect_window.restore()
                return
            except Exception:
                self.connect_window = None
        self.captured = {}
        self._finishing = False
        window = webview.create_window(
            "Connect Wyze — sign in and create an API key",
            CONSOLE_URL,
            js_api=CaptureApi(self),
            width=1100,
            height=820,
        )
        window.events.loaded += lambda: self._on_loaded(window)
        window.events.closed += self._on_connect_closed
        self.connect_window = window

    def _on_loaded(self, window) -> None:
        # Real OS window chrome the page can't touch: lock + host in the title bar.
        try:
            url = urlparse(window.get_current_url() or "")
            host = url.hostname or ""
            is_wyze = host == "wyze.com" or host.endswith(".wyze.com")
            mark = "🔒" if url.scheme == "https" and is_wyze else "⚠️"
            window.set_title(f"{mark} {host} — Connect Wyze")
        except Exception:
            pass
        try:
            window.evaluate_js(CAPTURE_JS)
        except Exception as exc:
            if DEBUG:
                print(f"[capture] inject failed: {exc}")

    def _on_connect_closed(self) -> None:
        self.connect_window = None

    # ---- capture finished --------------------------------------------------
    def on_api_key(self, key_id: str, api_key: str, expires: int | None = None) -> None:
        if self._finishing:
            return
        self._finishing = True
        threading.Thread(target=self._finish, args=(key_id, api_key, expires), daemon=True).start()

    def _finish(self, key_id: str, api_key: str, expires: int | None = None) -> None:
        if expires:
            auth.save_key_expiry(expires)
        window = self.connect_window
        if window is not None:
            try:
                window.evaluate_js("window.__wstCountdown && window.__wstCountdown(3, 'API key captured')")
            except Exception:
                pass
            time.sleep(3.2)
            try:
                window.destroy()
            except Exception:
                pass

        email = self.captured.get("email") or auth.saved_credentials()["email"]
        password = self.captured.get("password")
        query: dict[str, str]
        if email and password:
            try:
                result = auth.start_login(
                    email=email, password=password, key_id=key_id, api_key=api_key, remember_password=True
                )
                if result["status"] == "ok":
                    query = {"connected": "1"}
                else:
                    query = {"challenge": result["challenge_id"], "prompt": result["prompt"]}
            except auth.LoginError as exc:
                auth.persist_credentials(email=email, key_id=key_id, api_key=api_key)
                query = {"connect_error": f"Captured your API key, but Wyze login failed: {exc}"}
        else:
            auth.persist_credentials(email=email, key_id=key_id, api_key=api_key)
            query = {
                "connect_error": "API key captured. Enter your Wyze password to finish "
                "(Google/Apple sign-in accounts need a Wyze password set in the app)."
            }
        self.captured = {}
        if self.main_window is not None:
            qs = "&".join(f"{k}={quote(v)}" for k, v in query.items())
            self.main_window.load_url(f"{self.base_url}/?{qs}")

    # ---- run ---------------------------------------------------------------
    def run(self, reconnect: bool = False) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.flask_app.config["DESKTOP"] = True
        self.main_window = webview.create_window(
            "Wyze Scale Tracker", self.base_url, js_api=MainApi(self), width=1280, height=900, min_size=(720, 560)
        )
        status = auth.status()
        first_boot = not status["connected"] and not status["has_saved_credentials"]
        if first_boot or reconnect:
            self.open_connect_window()
        try:
            webview.start(private_mode=False, storage_path=str(config.data_dir() / "webview"))
        finally:
            self.server.shutdown()


_cert_cache: dict[str, dict] = {}


def _issuer_label(org: str | None, common_name: str | None) -> str:
    if org and common_name and common_name.startswith(org):
        return common_name  # "Amazon" + "Amazon RSA 2048 M01" -> "Amazon RSA 2048 M01"
    return " ".join(filter(None, (org, common_name))) or "unknown issuer"


def fetch_certificate(host: str) -> dict:
    """Open our own TLS connection to ``host`` and verify it against the OS trust store."""
    if host in _cert_cache:
        return _cert_cache[host]
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=6) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
        subject = dict(item[0] for item in cert.get("subject", ()))
        issuer = dict(item[0] for item in cert.get("issuer", ()))
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").strftime("%b %d, %Y")
        info = {
            "ok": True,
            "subject": subject.get("commonName", host),
            "issuer": _issuer_label(issuer.get("organizationName"), issuer.get("commonName")),
            "expires": expires,
        }
    except Exception as exc:  # verification failure, DNS, timeout...
        info = {"ok": False, "error": str(exc)[:160]}
    _cert_cache[host] = info
    return info


def free_port(preferred: int) -> int:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]


def run_desktop(flask_app, reconnect: bool = False) -> None:
    port = free_port(int(os.getenv("APP_PORT", "8000")))
    DesktopShell(flask_app, port).run(reconnect=reconnect)
