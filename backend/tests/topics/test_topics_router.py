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

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _FakeRepo())
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
