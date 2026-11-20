"""Secret storage: OS credential store (Windows Credential Manager, macOS Keychain,
Secret Service) via ``keyring``, falling back to a local file when none is available.

Non-secret settings (email, Key ID, key expiry) stay in ``.env``. Secrets
(password, API key, TOTP key, tokens) live here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import unset_key

from . import config

SERVICE = "wyze-scale-tracker"
# Secrets that used to live in .env and are migrated out of it.
ENV_SECRETS = ("WYZE_PASSWORD", "WYZE_API_KEY", "WYZE_TOTP_KEY")


def _fallback_path() -> Path:
    return config.data_dir() / "secrets.json"


def _keyring():
    """Return the keyring module if a real backend is available, else None."""
    if os.getenv("WST_SECRET_BACKEND", "").lower() == "file":
        return None
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or "fail" in type(backend).__module__:
            return None
        return keyring
    except Exception:
        return None


def backend_name() -> str:
    kr = _keyring()
    return type(kr.get_keyring()).__name__ if kr else "local file (data/secrets.json)"


def _read_file() -> dict[str, str]:
    try:
        return json.loads(_fallback_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_file(data: dict[str, str]) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def get_secret(name: str) -> str | None:
    # An explicit environment variable wins (CI, containers, one-off overrides).
    env_value = os.getenv(name, "").strip() if name.isupper() else ""
    if env_value:
        return env_value
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, name)
            if value:
                return value
        except Exception:
            pass
    return _read_file().get(name) or None


def set_secret(name: str, value: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, name, value)
            data = _read_file()
            if data.pop(name, None) is not None:
                _write_file(data)
            return
        except Exception:
            pass  # e.g. value too large for the credential store; use the file
    data = _read_file()
    data[name] = value
    _write_file(data)


def delete_secret(name: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:
            pass
    data = _read_file()
    if data.pop(name, None) is not None:
        _write_file(data)


def migrate_env_secrets() -> list[str]:
    """Move secrets that were written to .env into the secret store. Returns moved names."""
    env_file = config.env_path()
    if not env_file.exists():
        return []
    from dotenv import dotenv_values

    values = dotenv_values(env_file)
    moved = []
    for name in ENV_SECRETS:
        value = (values.get(name) or "").strip()
        if not value:
            continue
        set_secret(name, value)
        unset_key(str(env_file), name)
        os.environ.pop(name, None)
        moved.append(name)
    return moved
