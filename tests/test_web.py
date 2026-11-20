import csv
import io

from tracker import auth, db
from tracker.parsing import cloud_payload


def seed():
    with db.connect() as conn:
        for i, (member, name) in enumerate([("m1", "Me"), ("m1", "Me"), ("m2", "Sam")]):
            ts = 1700000000 + i
            raw = {"id": f"r{i}", "mac": "AA", "family_member_id": member, "measure_ts": ts, "weight": 200 + i}
            db.upsert_measurement(conn, cloud_payload(raw, weight_kg=None, product_model="WL_SCU", profile_name=name))


def test_index_and_data_routes(client):
    seed()
    assert client.get("/").status_code == 200
    data = client.get("/api/measurements?profile=m1").get_json()
    assert data["count"] == 2 and data["data"][0]["measure_ts"] < data["data"][1]["measure_ts"]
    profiles = client.get("/api/profiles").get_json()["data"]
    assert [p["profile_name"] for p in profiles] == ["Me", "Sam"]
    assert client.get("/api/latest?profile=m2").get_json()["data"]["weight_lb"] == 202


def test_export_csv(client):
    seed()
    resp = client.get("/api/export?range=all&profile=m1")
    rows = list(csv.reader(io.StringIO(resp.get_data(as_text=True))))
    assert rows[0][:4] == ["measured_at", "profile_name", "profile_id", "weight_lb"]
    assert len(rows) == 3
    assert client.get("/api/export?range=bogus").status_code == 400
    assert client.get("/api/export?range=custom&start=2026-02-01&end=2026-01-01").status_code == 400


def test_cross_origin_post_blocked(client):
    resp = client.post("/api/sync", json={}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_sync_reports_needs_login(client):
    resp = client.post("/api/sync", json={}, headers={"Origin": "http://localhost"})
    assert resp.status_code == 401 and resp.get_json()["needs_login"] is True


def test_auth_status_shape(client):
    info = client.get("/api/auth/status").get_json()
    assert info["connected"] is False and info["desktop"] is False
    assert "developer_console_url" in info and "WYZE" not in str(info.get("saved_key_id"))


def test_login_route_returns_user_facing_error(client, monkeypatch):
    def reject(**kwargs):
        raise auth.LoginError("Wyze rejected the email/password.")

    monkeypatch.setattr(auth, "start_login", reject)
    resp = client.post("/api/auth/login", json={"email": "a@b.c", "password": "x", "key_id": "k", "api_key": "a"})
    assert resp.status_code == 400 and "rejected" in resp.get_json()["error"]
