import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from app.api import topics_router as tr

    class _Repo:
        async def get_by_id(self, hid, source_ids=None):
            if hid == "news1":
                return {
                    "id": "news1",
                    "title": "News",
                    "summary": "s",
                    "ai_summary": None,
                    "url": "https://n/1",
                }
            return None  # unknown id -> 404

    class _Sources:
        async def feed_source_ids(self, user_id, hidden_ids):
            return ["s1"]

    class _Hidden:
        async def list_hidden_ids(self, user_id):
            return []

    monkeypatch.setattr(tr, "HotspotsRepository", lambda: _Repo())
    monkeypatch.setattr(tr, "SignalSourcesRepository", lambda: _Sources())
    monkeypatch.setattr(tr, "UserHiddenSourcesRepository", lambda: _Hidden())

    async def fake_script(title, summary, user_id):
        return [{"title": "Chapter 1", "summary": "..."}]

    monkeypatch.setattr(tr, "_generate_script_for", fake_script)

    app = FastAPI()
    app.dependency_overrides[tr.get_auth] = lambda: type("A", (), {"user_id": "u1"})()
    app.include_router(tr.router, prefix="/api/v1")
    return TestClient(app)


def test_generate_script_any_item(client):
    r = client.post("/api/v1/topics/news1/generate-script")
    assert r.status_code == 200
    assert r.json()["script"] == [{"title": "Chapter 1", "summary": "..."}]


def test_generate_script_404_for_unknown(client):
    r = client.post("/api/v1/topics/missing/generate-script")
    assert r.status_code == 404
