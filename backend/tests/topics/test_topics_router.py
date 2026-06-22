import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    calls = {}

    def _row(rid, title):
        return {
            "id": rid,
            "title": title,
            "url": "u",
            "origin_url": "u",
            "source_label": "S",
            "summary": None,
            "ai_summary": None,
            "reason": None,
            "score": None,
            "tags": [],
            "category": "model",
            "media_url": None,
            "cover_url": None,
            "captured_at": "2026-06-20T06:00:00Z",
        }

    class _FakeRepo:
        async def list_for_date(self, day, category, limit=100, q=None):
            calls["day"] = day
            calls["category"] = category
            calls["q"] = q
            return [_row("1", "Hello"), _row("2", "World")]

        async def list_by_ids(self, ids, limit=100):
            calls["list_by_ids"] = list(ids)
            return [_row(i, f"Saved {i}") for i in ids]

        async def get_by_id(self, hotspot_id):
            if calls.get("missing"):
                return None
            row = _row(hotspot_id, "Detail")
            row["content_original"] = "full body text"
            return row

        async def distinct_dates(self, limit_days=60):
            return ["2026-06-20", "2026-06-19"]

    # Tests preset calls['states'] / calls['ids'] to steer the fake state repo.
    class _FakeStateRepo:
        async def get_states(self, user_id, hotspot_ids):
            return calls.get("states", {})

        async def list_ids_where(self, user_id, *, flag):
            calls["flag"] = flag
            return calls.get("ids", [])

        async def set_state(
            self, user_id, hotspot_id, *, is_read=None, is_saved=None, is_hidden=None
        ):
            calls["set"] = {
                "hotspot_id": hotspot_id,
                "is_read": is_read,
                "is_saved": is_saved,
                "is_hidden": is_hidden,
            }
            return {
                "is_read": bool(is_read),
                "is_saved": bool(is_saved),
                "is_hidden": bool(is_hidden),
            }

    class _FakeSourcesRepo:
        async def list_all(self):
            return [
                {
                    "id": "10",
                    "name": "Weibo Hot",
                    "kind": "newsnow",
                    "category": "industry",
                    "enabled": True,
                    "health": "dead",
                    "consecutive_failures": 5,
                    "last_error": "timeout",
                    "last_fetched_at": "2026-06-22T06:00:00Z",
                    "last_ok_at": "2026-06-20T06:00:00Z",
                },
                {
                    "id": "11",
                    "name": "Hacker News",
                    "kind": "rss",
                    "category": None,
                    "enabled": False,
                    "health": "ok",
                    "consecutive_failures": 0,
                    "last_error": None,
                    "last_fetched_at": None,
                    "last_ok_at": None,
                },
            ]

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _FakeRepo())
    monkeypatch.setattr(tr, "SignalSourcesRepository", lambda: _FakeSourcesRepo())
    monkeypatch.setattr(tr, "HotspotUserStateRepository", lambda: _FakeStateRepo())
    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    tc = TestClient(app)
    tc.calls = calls  # expose captured repo args to tests
    return tc


def test_list_hotspots(client):
    r = client.get("/api/v1/topics?category=model")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 2
    assert body["hotspots"][0]["title"] == "Hello"
    # no state rows → all flags default false
    assert body["hotspots"][0]["is_saved"] is False


def test_list_hotspots_all_view_excludes_hidden(client):
    client.calls["states"] = {"2": {"is_hidden": True}}
    r = client.get("/api/v1/topics")
    body = r.json()
    ids = [h["id"] for h in body["hotspots"]]
    assert ids == ["1"]  # hidden id 2 dropped from default view


def test_list_hotspots_merges_state_flags(client):
    client.calls["states"] = {"1": {"is_read": True, "is_saved": True}}
    r = client.get("/api/v1/topics")
    h1 = next(h for h in r.json()["hotspots"] if h["id"] == "1")
    assert h1["is_read"] is True and h1["is_saved"] is True


def test_list_hotspots_saved_view_uses_state_ids(client):
    client.calls["ids"] = ["7", "8"]
    r = client.get("/api/v1/topics?view=saved")
    assert r.status_code == 200
    assert client.calls["flag"] == "is_saved"
    assert client.calls["list_by_ids"] == ["7", "8"]


def test_list_hotspots_hidden_view_shows_hidden(client):
    client.calls["ids"] = ["9"]
    client.calls["states"] = {"9": {"is_hidden": True}}
    r = client.get("/api/v1/topics?view=hidden")
    body = r.json()
    assert client.calls["flag"] == "is_hidden"
    # hidden items are NOT excluded in the hidden view
    assert [h["id"] for h in body["hotspots"]] == ["9"]


def test_set_hotspot_state(client):
    r = client.patch("/api/v1/topics/42/state", json={"is_saved": True})
    assert r.status_code == 200
    assert r.json()["is_saved"] is True
    assert client.calls["set"] == {
        "hotspot_id": "42",
        "is_read": None,
        "is_saved": True,
        "is_hidden": None,
    }


def test_list_hotspots_passes_search_and_ignores_day(client):
    r = client.get("/api/v1/topics?q=gpt&day=2026-06-20")
    assert r.status_code == 200
    # search spans all dates: day is dropped when q is present
    assert client.calls["q"] == "gpt"
    assert client.calls["day"] is None


def test_list_hotspots_keeps_day_when_no_search(client):
    r = client.get("/api/v1/topics?day=2026-06-20")
    assert r.status_code == 200
    assert client.calls["day"] == "2026-06-20"
    assert client.calls["q"] is None


def test_get_hotspot_detail_includes_content(client):
    client.calls["states"] = {"55": {"is_saved": True}}
    r = client.get("/api/v1/topics/55")
    assert r.status_code == 200
    h = r.json()["hotspot"]
    assert h["id"] == "55"
    assert h["content_original"] == "full body text"
    assert h["is_saved"] is True


def test_get_hotspot_detail_404(client):
    client.calls["missing"] = True
    r = client.get("/api/v1/topics/999")
    assert r.status_code == 404


def test_dates(client):
    r = client.get("/api/v1/topics/dates")
    assert r.status_code == 200
    assert r.json()["dates"] == ["2026-06-20", "2026-06-19"]


def test_list_does_not_leak_content(client):
    # list rows omit the heavy content_original field
    r = client.get("/api/v1/topics")
    assert r.json()["hotspots"][0]["content_original"] is None


def test_source_health(client):
    r = client.get("/api/v1/topics/sources/health")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 2
    dead = body["sources"][0]
    assert dead["name"] == "Weibo Hot"
    assert dead["health"] == "dead"
    assert dead["consecutive_failures"] == 5
    assert dead["last_error"] == "timeout"
    disabled = body["sources"][1]
    assert disabled["enabled"] is False
    assert disabled["last_error"] is None
