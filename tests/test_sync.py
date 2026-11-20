from datetime import datetime, timedelta

from tracker import db
from tracker.sync import run_sync
from tracker.wyze_api import Reading

NOW = datetime(2026, 11, 20, 12, 0)
OWNER = "owner-id"


def reading(rid, member, days_ago, weight=200.0):
    ts = int((NOW - timedelta(days=days_ago)).timestamp() * 1000)
    raw = {"id": rid, "mac": "AA:BB", "family_member_id": member, "measure_ts": ts, "weight": weight}
    return Reading(raw=raw, weight_kg=None)


class FakeScales:
    def __init__(self, records, *, list_fails=False, member_fails="<nobody>"):
        self.user_id = OWNER
        self._records = records
        self.list_fails = list_fails
        self.member_fails = member_fails
        self.calls = []

    def list_scales(self):
        if self.list_fails:
            raise RuntimeError("list down")
        return [{"mac": "WL_SCU_TEST", "model": "WL_SCU", "nickname": "Bathroom"}]

    def family_members(self, model, mac):
        return [
            {"id": "kid-id", "nick_name": "Sam"},
            {"id": OWNER, "nick_name": "owner@example.com"},
        ]

    def owner_nickname(self):
        return "owner@example.com"  # email-like -> falls back to "Me"

    def records(self, model, profile_id, start, end):
        self.calls.append((model, profile_id, start))
        if profile_id == self.member_fails:
            raise RuntimeError("member endpoint down")
        return [r for r in self._records.get(profile_id, []) if r.raw["measure_ts"] / 1000 >= start.timestamp()]


def test_first_sync_pulls_everyone_with_names():
    api = FakeScales({OWNER: [reading("a", OWNER, 30), reading("b", OWNER, 1)], "kid-id": [reading("c", "kid-id", 5)]})
    result = run_sync(api, now=NOW)
    assert result["inserted"] == 3
    names = {p["name"]: p["new"] for p in result["people"]}
    assert names == {"Sam": 1, "Me": 2}
    assert {p["profile_name"] for p in db.profiles()} == {"Sam", "Me"}
    # First sync goes back the full history window.
    assert all(start < NOW - timedelta(days=3000) for _, _, start in api.calls)


def test_second_sync_is_incremental_per_person():
    records = {OWNER: [reading("a", OWNER, 30), reading("b", OWNER, 1)], "kid-id": [reading("c", "kid-id", 10)]}
    run_sync(FakeScales(records), now=NOW)
    api = FakeScales(records)
    result = run_sync(api, now=NOW)
    assert result["inserted"] == 0
    starts = {pid: start for _, pid, start in api.calls}
    assert starts[OWNER] == datetime.fromtimestamp(int((NOW - timedelta(days=1)).timestamp())) - timedelta(days=2)
    assert starts["kid-id"] == datetime.fromtimestamp(int((NOW - timedelta(days=10)).timestamp())) - timedelta(days=2)


def test_full_sync_ignores_stored_data():
    records = {OWNER: [reading("a", OWNER, 30)], "kid-id": []}
    run_sync(FakeScales(records), now=NOW)
    api = FakeScales(records)
    run_sync(api, full=True, now=NOW)
    assert all(start < NOW - timedelta(days=3000) for _, _, start in api.calls)


def test_one_failing_member_does_not_stop_others():
    api = FakeScales({OWNER: [reading("a", OWNER, 1)]}, member_fails="kid-id")
    result = run_sync(api, now=NOW)
    assert result["inserted"] == 1
    assert any("member endpoint down" in e for e in result["errors"])


def test_scale_list_failure_falls_back_to_owner_queries():
    api = FakeScales({None: [reading("a", OWNER, 1)]}, list_fails=True)
    result = run_sync(api, now=NOW)
    assert result["inserted"] == 1
    assert {model for model, _, _ in api.calls} == {"WL_SC2", "JA.SC2"}
    assert db.get_meta("last_sync_at") == str(int(NOW.timestamp()))
