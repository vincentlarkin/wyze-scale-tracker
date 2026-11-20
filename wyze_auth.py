"""Wyze authentication: one-time login, cached tokens, automatic refresh.

Login happens once (from the web UI, the CLI, or .env credentials). The
access/refresh tokens are cached in ``data/wyze_session.json`` so later syncs
never touch the rate-limited login endpoint. When the access token expires it
is refreshed automatically; only if the refresh also fails do we fall back to
logging in again with saved credentials.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

import requests
from dotenv import set_key
from wyze_sdk import Client, version
from wyze_sdk.errors import WyzeApiError
from wyze_sdk.service.auth_service import AuthServiceClient

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SESSION_PATH = DATA_DIR / "wyze_session.json"
ENV_PATH = BASE_DIR / ".env"
AUTH_API = "https://auth-prod.api.wyze.com"
DEVELOPER_CONSOLE_URL = "https://developer-api-console.wyze.com/#/apikey/view"
CHALLENGE_TTL_SECONDS = 600

T = TypeVar("T")


class NeedsLogin(Exception):
    """Raised when there is no usable session and no way to log in silently."""


class LoginError(Exception):
    """Raised when Wyze rejects a login attempt; message is user-facing."""


@dataclass
class _Challenge:
    email: str
    hashed_password: str
    key_id: str
    api_key: str
    mfa_type: str
    verification_id: str
    remember_password: bool
    raw_password: str
    created_at: float = field(default_factory=time.time)


_challenges: dict[str, _Challenge] = {}
_lock = threading.Lock()


# --------------------------------------------------------------------------- session file


def load_session() -> dict[str, Any] | None:
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if data.get("access_token") and data.get("refresh_token") else None


def save_session(**fields: Any) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = load_session() or {}
    session.update({k: v for k, v in fields.items() if v is not None})
    session["updated_at"] = int(time.time())
    tmp = SESSION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(session, indent=1), encoding="utf-8")
    tmp.replace(SESSION_PATH)
    return session


def clear_session() -> None:
    SESSION_PATH.unlink(missing_ok=True)


# --------------------------------------------------------------------------- credentials


def env_credentials() -> dict[str, str | None]:
    def get(name: str) -> str | None:
        value = os.getenv(name, "").strip()
        return value or None

    return {
        "email": get("WYZE_EMAIL"),
        "password": get("WYZE_PASSWORD"),
        "key_id": get("WYZE_KEY_ID"),
        "api_key": get("WYZE_API_KEY"),
        "totp_key": get("WYZE_TOTP_KEY"),
    }


def _persist_credentials(*, email: str | None, key_id: str, api_key: str, password: str | None) -> None:
    """Write credentials to .env so a future re-login can happen without asking."""
    ENV_PATH.touch(exist_ok=True)
    values = {"WYZE_EMAIL": email, "WYZE_KEY_ID": key_id, "WYZE_API_KEY": api_key, "WYZE_PASSWORD": password}
    for key, value in values.items():
        if value is None:
            continue
        set_key(str(ENV_PATH), key, value, quote_mode="never")
        os.environ[key] = value


def save_api_key(*, email: str | None, key_id: str, api_key: str) -> None:
    """Store a captured API key (and email) without logging in."""
    _persist_credentials(email=email, key_id=key_id, api_key=api_key, password=None)


def save_key_expiry(expires: int) -> None:
    """Remember when the Wyze API key expires (Wyze keys last about a year)."""
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "WYZE_API_KEY_EXPIRES", str(int(expires)), quote_mode="never")
    os.environ["WYZE_API_KEY_EXPIRES"] = str(int(expires))


# --------------------------------------------------------------------------- login flow


def _auth_headers(auth: AuthServiceClient, nonce: int, key_id: str, api_key: str) -> dict[str, str]:
    return auth._get_headers(
        request_specific_headers={
            "keyid": key_id,
            "apikey": api_key,
            "user-agent": f"wyze-scale-tracker (wyze-sdk-{version.__version__})",
        },
        nonce=nonce,
    )


def _post_auth(path: str, *, key_id: str, api_key: str, json_body: dict, params: dict | None = None) -> dict:
    auth = AuthServiceClient()
    nonce = auth.request_verifier.clock.nonce()
    body = dict(json_body)
    body.setdefault("nonce", str(nonce))
    try:
        resp = requests.post(
            f"{AUTH_API}{path}",
            json=body,
            params=params,
            headers=_auth_headers(auth, nonce, key_id, api_key),
            timeout=20,
        )
    except requests.RequestException as exc:
        raise LoginError(f"Could not reach Wyze: {exc}") from exc

    if resp.status_code == 429:
        raise LoginError(
            "Wyze is rate-limiting logins for this account right now (HTTP 429). "
            "Wait 10-15 minutes and try again."
        )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400 or data.get("errorCode"):
        description = data.get("description") or data.get("msg") or resp.text[:200]
        if data.get("errorCode") == 1000 or "password" in str(description).lower():
            raise LoginError(
                "Wyze rejected the email/password. If you sign in to Wyze with Google or Apple, "
                "set a Wyze password in the app (Account > Security) and use that."
            )
        raise LoginError(f"Wyze login failed (HTTP {resp.status_code}): {description}")
    return data


def _hash_password(password: str) -> str:
    md5 = AuthServiceClient().request_verifier.md5_string
    return md5(md5(md5(password)))


def _finish(data: dict, *, email: str, key_id: str, api_key: str, password: str, remember_password: bool) -> dict:
    save_session(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user_id=data.get("user_id"),
        email=email,
        logged_in_at=int(time.time()),
    )
    _persist_credentials(
        email=email, key_id=key_id, api_key=api_key, password=password if remember_password else None
    )
    return {"status": "ok", "user_id": data.get("user_id")}


def start_login(
    *,
    email: str,
    password: str,
    key_id: str,
    api_key: str,
    totp_key: str | None = None,
    remember_password: bool = True,
) -> dict[str, Any]:
    """First login step. Returns {'status': 'ok'} or {'status': 'mfa_required', ...}."""
    email, password, key_id, api_key = (v.strip() for v in (email, password, key_id, api_key))
    if not all((email, password, key_id, api_key)):
        raise LoginError("Email, password, Key ID and API Key are all required.")

    hashed = _hash_password(password)
    data = _post_auth(
        "/api/user/login",
        key_id=key_id,
        api_key=api_key,
        json_body={"email": email, "password": hashed},
    )
    if data.get("access_token"):
        return _finish(data, email=email, key_id=key_id, api_key=api_key, password=password, remember_password=remember_password)

    options = data.get("mfa_options") or []
    if "TotpVerificationCode" in options:
        mfa_type = "TotpVerificationCode"
        verification_id = data["mfa_details"]["totp_apps"][0]["app_id"]
        prompt = "Enter the 6-digit code from your authenticator app."
    elif "PrimaryPhone" in options:
        mfa_type = "PrimaryPhone"
        sent = _post_auth(
            "/user/login/sendSmsCode",
            key_id=key_id,
            api_key=api_key,
            json_body={},
            params={"mfaPhoneType": "Primary", "sessionId": data.get("sms_session_id"), "userId": data.get("user_id")},
        )
        verification_id = sent["session_id"]
        prompt = "Wyze sent a code to your phone by SMS. Enter it here."
    elif "Email" in options:
        mfa_type = "Email"
        sent = _post_auth(
            "/v2/user/login/sendEmailCode",
            key_id=key_id,
            api_key=api_key,
            json_body={},
            params={"sessionId": data.get("email_session_id"), "userId": data.get("user_id")},
        )
        verification_id = sent["session_id"]
        prompt = "Wyze emailed you a verification code. Enter it here."
    else:
        raise LoginError(f"Wyze asked for an unsupported verification method: {options}")

    challenge = _Challenge(
        email=email,
        hashed_password=hashed,
        key_id=key_id,
        api_key=api_key,
        mfa_type=mfa_type,
        verification_id=verification_id,
        remember_password=remember_password,
        raw_password=password,
    )

    if mfa_type == "TotpVerificationCode" and totp_key:
        from mintotp import totp  # dependency of wyze-sdk

        return _verify(challenge, totp(totp_key))

    challenge_id = secrets.token_urlsafe(16)
    with _lock:
        now = time.time()
        for key in [k for k, c in _challenges.items() if now - c.created_at > CHALLENGE_TTL_SECONDS]:
            del _challenges[key]
        _challenges[challenge_id] = challenge
    return {"status": "mfa_required", "challenge_id": challenge_id, "mfa_type": mfa_type, "prompt": prompt}


def _verify(challenge: _Challenge, code: str) -> dict[str, Any]:
    data = _post_auth(
        "/user/login",
        key_id=challenge.key_id,
        api_key=challenge.api_key,
        json_body={
            "email": challenge.email,
            "password": challenge.hashed_password,
            "mfa_type": challenge.mfa_type,
            "verification_id": challenge.verification_id,
            "verification_code": code.strip(),
        },
    )
    if not data.get("access_token"):
        raise LoginError("Wyze did not accept that verification code.")
    return _finish(
        data,
        email=challenge.email,
        key_id=challenge.key_id,
        api_key=challenge.api_key,
        password=challenge.raw_password,
        remember_password=challenge.remember_password,
    )


def verify_login(challenge_id: str, code: str) -> dict[str, Any]:
    with _lock:
        challenge = _challenges.pop(challenge_id, None)
    if challenge is None or time.time() - challenge.created_at > CHALLENGE_TTL_SECONDS:
        raise LoginError("That verification request expired. Start the login again.")
    return _verify(challenge, code)


def login_from_env() -> None:
    """Silent login with .env credentials. Raises NeedsLogin if impossible."""
    creds = env_credentials()
    if not all((creds["email"], creds["password"], creds["key_id"], creds["api_key"])):
        raise NeedsLogin("Not connected to Wyze yet. Click Connect Wyze to sign in.")
    result = start_login(
        email=creds["email"],
        password=creds["password"],
        key_id=creds["key_id"],
        api_key=creds["api_key"],
        totp_key=creds["totp_key"],
        remember_password=True,
    )
    if result["status"] != "ok":
        with _lock:
            _challenges.pop(result.get("challenge_id", ""), None)
        raise NeedsLogin("Wyze needs a verification code. Click Connect Wyze to finish signing in.")


# --------------------------------------------------------------------------- client access


def _is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, WyzeApiError):
        resp = exc.response if isinstance(exc.response, dict) else {}
        code = resp.get("code")
        msg = str(resp.get("msg") or resp.get("message") or "")
        return str(code) == "2001" or "AccessToken" in msg or "token" in msg.lower()
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 401
    return False


def _client_from_session(session: dict[str, Any]) -> Client:
    timeout = int(os.getenv("WYZE_TIMEOUT_SECONDS") or 15)
    client = Client(token=session["access_token"], refresh_token=session["refresh_token"], timeout=timeout)
    # wyze-sdk only sets user_id during login(); scale calls need it.
    client._user_id = session.get("user_id")
    return client


def _refresh(session: dict[str, Any]) -> dict[str, Any]:
    client = _client_from_session(session)
    client.refresh_token()
    return save_session(
        access_token=client._token,
        refresh_token=client._refresh_token,
        refreshed_at=int(time.time()),
    )


def with_client(fn: Callable[[Client], T]) -> T:
    """Run ``fn`` with an authenticated client, refreshing or re-logging in once if needed."""
    session = load_session()
    if session is None:
        login_from_env()
        session = load_session()
        assert session is not None

    try:
        return fn(_client_from_session(session))
    except Exception as exc:
        if not _is_auth_error(exc):
            raise

    try:
        session = _refresh(session)
    except Exception:
        clear_session()
        login_from_env()
        session = load_session()
        assert session is not None
    return fn(_client_from_session(session))


def status() -> dict[str, Any]:
    session = load_session()
    creds = env_credentials()
    try:
        key_expires = int(os.getenv("WYZE_API_KEY_EXPIRES", "") or 0) or None
    except ValueError:
        key_expires = None
    return {
        "api_key_expires": key_expires,
        "connected": session is not None,
        "email": (session or {}).get("email") or creds["email"],
        "logged_in_at": (session or {}).get("logged_in_at"),
        "has_saved_credentials": all((creds["email"], creds["password"], creds["key_id"], creds["api_key"])),
        "saved_key_id": creds["key_id"],
        "developer_console_url": DEVELOPER_CONSOLE_URL,
    }
