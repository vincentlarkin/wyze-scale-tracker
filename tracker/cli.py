"""Command line: desktop (default), serve, login, sync, status, logout."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from datetime import datetime

from . import auth, config, db
from .web import bootstrap, create_app


def _login() -> int:
    saved = auth.saved_credentials()

    def ask(label: str, current: str | None, secret: bool = False) -> str:
        prompt = f"{label}{' [saved, Enter to keep]' if current else ''}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        return value.strip() or (current or "")

    print(f"Get a Key ID + API Key at {config.DEVELOPER_CONSOLE_URL}")
    try:
        result = auth.start_login(
            email=ask("Wyze email", saved["email"]),
            password=ask("Wyze password", saved["password"], secret=True),
            key_id=ask("Key ID", saved["key_id"]),
            api_key=ask("API Key", saved["api_key"], secret=True),
            totp_key=saved["totp_key"],
        )
        if result["status"] == "mfa_required":
            auth.verify_login(result["challenge_id"], input(result["prompt"] + " "))
    except auth.LoginError as exc:
        print(f"Login failed: {exc}")
        return 1
    print("Connected. Secrets are stored in your system credential store.")
    return 0


def _sync(full: bool, as_json: bool) -> int:
    from .sync import sync_from_wyze

    try:
        result = sync_from_wyze(full=full)
    except (auth.NeedsLogin, auth.LoginError) as exc:
        print(json.dumps({"error": str(exc), "needs_login": True}) if as_json else f"{exc}\nRun: python app.py login")
        return 1
    if as_json:
        print(json.dumps(result, indent=1))
        return 0
    for person in result["people"]:
        print(f"  {person['name']}: {person['new']} new / {person['fetched']} fetched")
    for error in result["errors"]:
        print(f"  ! {error}")
    print(f"Sync complete: {result['inserted']} new, {result['skipped']} already stored.")
    return 0


def _status(as_json: bool) -> int:
    info = auth.status()
    last = db.get_meta("last_sync_at")
    people = [
        {
            "name": p["profile_name"],
            "records": p["records"],
            "latest": datetime.fromtimestamp(p["latest_ts"]).isoformat(),
        }
        for p in db.profiles()
        if p["latest_ts"]
    ]
    if as_json:
        print(json.dumps({**info, "last_sync_at": int(last) if last else None, "profiles": people}, indent=1))
        return 0
    print(f"Connected: {info['connected']} ({info['email'] or 'no account'})")
    print(f"Saved credentials for silent re-login: {info['has_saved_credentials']}")
    print(f"Secrets stored in: {info['secret_backend']}")
    if info["api_key_expires"]:
        print(f"API key expires: {datetime.fromtimestamp(info['api_key_expires']):%Y-%m-%d}")
    print(f"Last sync: {datetime.fromtimestamp(int(last)):%Y-%m-%d %H:%M}" if last else "Last sync: never")
    for p in people:
        print(f"  {p['name']}: {p['records']} records, latest {p['latest'][:16].replace('T', ' ')}")
    return 0


def _serve(open_browser: bool) -> None:
    app = create_app()
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    debug = os.getenv("APP_DEBUG", "false").lower() == "true"
    if open_browser and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=(f"http://{host}:{port}",)).start()
    app.run(host=host, port=port, debug=debug)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.py", description="Wyze Scale Tracker")
    sub = parser.add_subparsers(dest="command")
    desk = sub.add_parser("desktop", help="Open the desktop app (default)")
    desk.add_argument("--reconnect", action="store_true", help="Open the Wyze sign-in window to capture a new API key")
    serve = sub.add_parser("serve", help="Run as a web server for your normal browser")
    serve.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    sub.add_parser("login", help="Connect your Wyze account (handles 2FA)")
    sync = sub.add_parser("sync", help="Pull new scale records")
    sync.add_argument("--full", action="store_true", help="Re-fetch complete history")
    sync.add_argument("--json", action="store_true", help="Machine-readable output")
    status = sub.add_parser("status", help="Show connection and data summary")
    status.add_argument("--json", action="store_true", help="Machine-readable output")
    sub.add_parser("logout", help="Forget cached Wyze tokens")
    args = parser.parse_args(argv)

    if args.command == "serve":
        _serve(open_browser=not args.no_browser)
        return 0
    if args.command in (None, "desktop"):
        try:
            from . import desktop
        except ImportError as exc:
            print(f"Desktop mode unavailable ({exc}); falling back to the browser.")
            _serve(open_browser=True)
            return 0
        desktop.run_desktop(create_app(desktop=True), reconnect=getattr(args, "reconnect", False))
        return 0

    bootstrap()
    if args.command == "login":
        return _login()
    if args.command == "sync":
        return _sync(args.full, args.json)
    if args.command == "status":
        return _status(args.json)
    if args.command == "logout":
        auth.clear_session()
        print("Cached Wyze tokens removed.")
        return 0
    parser.print_help()
    return 1
