"""Thin adapter over wyze-sdk's scale endpoints.

This is the only module that touches wyze-sdk internals (``_user_id``,
``_scale_client``), so an SDK update that breaks them breaks in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from wyze_sdk import Client
from wyze_sdk.models.devices import DeviceModels


@dataclass
class Reading:
    raw: dict[str, Any]
    weight_kg: float | None


class ScaleApi(Protocol):
    """What sync needs from Wyze. Tests provide a fake."""

    user_id: str | None

    def list_scales(self) -> list[dict[str, Any]]: ...
    def family_members(self, model: str, mac: str) -> list[dict[str, Any]]: ...
    def owner_nickname(self) -> str | None: ...
    def records(self, model: str, profile_id: str | None, start: datetime, end: datetime) -> list[Reading]: ...


def service_key(model: str) -> str:
    """Records live in one of two backends; querying the same backend twice duplicates work."""
    return "scale" if model in DeviceModels.SCALE_ else "pluto"


class WyzeScales:
    def __init__(self, client: Client):
        self._client = client

    @property
    def user_id(self) -> str | None:
        return self._client._user_id

    def list_scales(self) -> list[dict[str, Any]]:
        return [
            {"mac": s.mac, "model": s.product.model if s.product else None, "nickname": s.nickname}
            for s in self._client.scales.list()
        ]

    def family_members(self, model: str, mac: str) -> list[dict[str, Any]]:
        response = self._client.scales._scale_client(model).get_family_member(did=mac)
        return (response.data or {}).get("data") or []

    def owner_nickname(self) -> str | None:
        info = self._client.user_get_info().data.get("data") or {}
        return str(info.get("nickname") or "").strip() or None

    def records(self, model: str, profile_id: str | None, start: datetime, end: datetime) -> list[Reading]:
        records = self._client.scales.get_records(
            device_model=model, user_id=profile_id, start_time=start, end_time=end
        )
        # ScaleRecord.to_dict() reports weight in lb; the kg value is only on the private attribute.
        return [Reading(raw=r.to_dict(), weight_kg=getattr(r, "_weight", None)) for r in records]
