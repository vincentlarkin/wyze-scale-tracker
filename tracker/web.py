"""Flask app factory and JSON/CSV routes."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request

from . import __version__, auth, config, db, secret_store
from .importer import import_workbook
from .sync import sync_from_wyze

EXPORT_RANGES: dict[str, int | None] = {"day": 1, "week": 7, "month": 30, "year": 365, "all": None, "custom": None}

EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("measured_at", "measured_at"),
    ("profile_name", "profile_name"),
    ("profile_id", "profile_id"),
    ("weight_lb", "weight_lb"),
    ("weight_kg", "weight_kg"),
    ("bmi", "bmi"),
    ("body_fat", "body_fat"),
    ("muscle", "muscle_lb"),
    ("skeletal_muscle", "skeletal_muscle_lb"),
    ("body_water", "body_water"),
    ("protein", "protein"),
    ("bone_mineral", "bone_mineral_lb"),
    ("subcutaneous_fat", "subcutaneous_fat"),
    ("body_vfr", "body_vfr"),
    ("bmr", "bmr"),
    ("metabolic_age", "metabolic_age"),
    ("body_score", "body_score"),
    ("product_model", "product_model"),
)


def bootstrap() -> None:
    """Load settings, move any plaintext secrets out of .env, make sure the DB exists."""
    config.load_env()
    secret_store.migrate_env_secrets()
    db.ensure_db()


def _date_to_epoch(text: str, *, end_of_day: bool = False) -> int:
    parsed = datetime.strptime(text, "%Y-%m-%d")
    if end_of_day:
        parsed += timedelta(days=1) - timedelta(seconds=1)
    return int(parsed.timestamp())


def create_app(*, desktop: bool = False) -> Flask:
    bootstrap()
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )
    app.config["DESKTOP"] = desktop

    @app.before_request
    def same_origin_only():
        """Block cross-site POSTs (e.g. some web page posting to localhost)."""
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                return jsonify({"error": "Cross-origin request blocked"}), 403
        return None

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/api/measurements")
    def api_measurements():
        limit = min(max(request.args.get("limit", type=int, default=500), 1), 5000)
        days = request.args.get("days", type=int)
        profile = (request.args.get("profile") or "").strip() or None
        rows = db.measurements(days=days, profile=profile, limit=limit)
        return jsonify({"count": len(rows), "data": rows})

    @app.get("/api/profiles")
    def api_profiles():
        data = db.profiles()
        return jsonify({"count": len(data), "data": data})

    @app.get("/api/latest")
    def api_latest():
        profile = (request.args.get("profile") or "").strip() or None
        return jsonify({"data": db.latest(profile)})

    @app.get("/api/export")
    def api_export():
        range_key = (request.args.get("range") or "all").strip().lower()
        if range_key not in EXPORT_RANGES:
            return jsonify({"error": f"range must be one of: {', '.join(EXPORT_RANGES)}"}), 400
        profile = (request.args.get("profile") or "").strip() or None
        start_ts = end_ts = None
        if range_key == "custom":
            start, end = (request.args.get("start") or "").strip(), (request.args.get("end") or "").strip()
            if not start or not end:
                return jsonify({"error": "custom export requires start and end dates (YYYY-MM-DD)"}), 400
            try:
                start_ts, end_ts = _date_to_epoch(start), _date_to_epoch(end, end_of_day=True)
            except ValueError:
                return jsonify({"error": "invalid custom date format, expected YYYY-MM-DD"}), 400
            if end_ts < start_ts:
                return jsonify({"error": "end date must be on or after start date"}), 400

        rows = db.export_rows(
            [col for col, _ in EXPORT_COLUMNS],
            days=EXPORT_RANGES[range_key],
            profile=profile,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow([header for _, header in EXPORT_COLUMNS])
        for row in rows:
            writer.writerow([row[col] for col, _ in EXPORT_COLUMNS])

        parts = ["wyze-scale-export", range_key]
        if profile:
            parts.append(re.sub(r"[^A-Za-z0-9_-]+", "_", profile))
        parts.append(datetime.now().strftime("%Y%m%d-%H%M%S"))
        return Response(
            stream.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{"-".join(parts)}.csv"'},
        )

    @app.get("/api/auth/status")
    def api_auth_status():
        info = auth.status()
        last_sync = db.get_meta("last_sync_at")
        info["last_sync_at"] = int(last_sync) if last_sync else None
        info["desktop"] = bool(app.config.get("DESKTOP"))
        return jsonify(info)

    @app.post("/api/auth/login")
    def api_auth_login():
        body = request.get_json(silent=True) or {}
        saved = auth.saved_credentials()
        try:
            result = auth.start_login(
                email=str(body.get("email") or saved["email"] or ""),
                # A blank password/API key in the form means "use the saved one".
                password=str(body.get("password") or saved["password"] or ""),
                key_id=str(body.get("key_id") or saved["key_id"] or ""),
                api_key=str(body.get("api_key") or saved["api_key"] or ""),
                totp_key=saved["totp_key"],
                remember_password=bool(body.get("remember_password", True)),
            )
        except auth.LoginError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/auth/verify")
    def api_auth_verify():
        body = request.get_json(silent=True) or {}
        try:
            result = auth.verify_login(str(body.get("challenge_id") or ""), str(body.get("code") or ""))
        except auth.LoginError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.post("/api/auth/logout")
    def api_auth_logout():
        auth.clear_session()
        return jsonify({"ok": True})

    @app.post("/api/sync")
    def api_sync():
        body = request.get_json(silent=True) or {}
        try:
            result = sync_from_wyze(full=bool(body.get("full")))
        except (auth.NeedsLogin, auth.LoginError) as exc:
            return jsonify({"error": str(exc), "needs_login": True}), 401
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True, "result": result})

    @app.post("/api/import")
    def api_import():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "No file uploaded"}), 400
        if not upload.filename.lower().endswith((".xlsx", ".xlsm", ".xltx", ".xltm")):
            return jsonify({"error": "Please upload a .xlsx file"}), 400
        sheet_name = request.form.get("sheet_name", "").strip() or None
        try:
            result = import_workbook(upload.read(), filename=upload.filename, sheet_name=sheet_name)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"ok": True, "result": result})

    return app
