"""Pull new readings for every person on every scale."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from . import auth, db
from .config import FALLBACK_MODELS, FULL_HISTORY_DAYS
from .parsing import cloud_payload
from .wyze_api import ScaleApi, WyzeScales, service_key


def _display_name(api: ScaleApi, member_id: str, nick_name: str | None) -> str | None:
    if member_id != api.user_id:
        return nick_name
    # The owner's family-member nickname is often their email; prefer a real name.
    if nick_name and "@" not in nick_name:
        return nick_name
    try:
        nickname = api.owner_nickname()
        if nickname and "@" not in nickname:
            return nickname
    except Exception:
        pass
    return "Me"


def discover_targets(api: ScaleApi) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Return (scales, people-to-sync, errors)."""
    errors: list[str] = []
    try:
        scales = api.list_scales()
    except Exception as exc:
        scales = []
        errors.append(f"Scale list failed ({exc}); falling back to direct queries.")

    targets: dict[tuple[str, str | None], dict[str, Any]] = {}
    for scale in scales:
        model = scale.get("model") or FALLBACK_MODELS[0]
        try:
            members = api.family_members(model, scale["mac"])
        except Exception as exc:
            members = []
            errors.append(f"{scale.get('nickname') or scale['mac']}: could not list family members ({exc})")
        if not members:
            targets.setdefault((service_key(model), None), {"model": model, "profile_id": None, "name": None})
        for member in members:
            member_id = str(member.get("id") or "") or None
            if member_id is None:
                continue
            name = _display_name(api, member_id, str(member.get("nick_name") or "").strip() or None)
            targets.setdefault((service_key(model), member_id), {"model": model, "profile_id": member_id, "name": name})

    if not scales:
        for model in FALLBACK_MODELS:
            targets.setdefault((service_key(model), None), {"model": model, "profile_id": None, "name": None})
    return scales, list(targets.values()), errors


def run_sync(api: ScaleApi, *, full: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """Incremental per person (newest stored record minus 2 days); first sync or full=True pulls everything."""
    scales, targets, errors = discover_targets(api)
    now = now or datetime.now()
    inserted = skipped = seen = 0
    people: list[dict[str, Any]] = []

    with db.connect() as conn:
        for target in targets:
            profile_id = target["profile_id"]
            latest = None if full else db.latest_cloud_ts(conn, profile_id)
            start = (
                datetime.fromtimestamp(latest) - timedelta(days=2)
                if latest
                else now - timedelta(days=FULL_HISTORY_DAYS)
            )
            label = target["name"] or profile_id or target["model"]
            try:
                readings = api.records(target["model"], profile_id, start, now)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                people.append({"name": label, "profile_id": profile_id, "fetched": 0, "new": 0, "error": True})
                continue

            new = 0
            for reading in readings:
                seen += 1
                payload = cloud_payload(
                    reading.raw,
                    weight_kg=reading.weight_kg,
                    product_model=target["model"],
                    profile_id=profile_id,
                    profile_name=target["name"],
                )
                if db.upsert_measurement(conn, payload):
                    inserted += 1
                    new += 1
                else:
                    skipped += 1
            if profile_id and target["name"]:
                db.rename_profile(conn, profile_id, target["name"])
            people.append(
                {
                    "name": label,
                    "profile_id": profile_id,
                    "fetched": len(readings),
                    "new": new,
                    "since": start.date().isoformat(),
                }
            )

        summary = {
            "inserted": inserted,
            "skipped": skipped,
            "records_seen": seen,
            "scales": scales,
            "people": people,
            "errors": errors,
            "full": full,
            "synced_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        db.set_meta(conn, "last_sync_at", str(int(now.timestamp())))
        db.set_meta(conn, "last_sync_result", json.dumps(summary))
    return summary


def sync_from_wyze(*, full: bool = False) -> dict[str, Any]:
    return auth.with_client(lambda client: run_sync(WyzeScales(client), full=full))
