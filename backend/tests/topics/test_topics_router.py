import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    class _FakeRepo:
        async def list_for_date(self, day, category, limit=100):
            return [
                {
                    "id": "1",
                    "title": "Hello",
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
            ]

        async def distinct_dates(self, limit_days=60):
            return ["2026-06-20", "2026-06-19"]

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
    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    return TestClient(app)


def test_list_hotspots(client):
    r = client.get("/api/v1/topics?category=model")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] and body["count"] == 1
    assert body["hotspots"][0]["title"] == "Hello"


def test_dates(client):
    r = client.get("/api/v1/topics/dates")
    assert r.status_code == 200
    assert r.json()["dates"] == ["2026-06-20", "2026-06-19"]


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
