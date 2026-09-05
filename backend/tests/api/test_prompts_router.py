"""/prompts: envelope, filter pass-through, scope gate (no DB)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import prompts_router as pr
from app.core.deps import get_auth

USER = "11111111-1111-1111-1111-111111111111"


class _Auth:
    user_id = USER


class _FakeService:
    def __init__(self):
        self.calls = []

    async def list(self, scope_id, **kw):
        self.calls.append(("list", scope_id, kw))
        return {
            "items": [],
            "total": 0,
            "by_form": {"template": 0, "image": 0, "album": 0},
            "by_origin": {"typed": 0, "extracted": 0, "captioned": 0},
        }

    async def counts(self, scope_id, **kw):
        self.calls.append(("counts", scope_id, kw))
        return {"mine": 3, "project": None, "system": 0}


@pytest.fixture
def app(monkeypatch):
    application = FastAPI()
    application.include_router(pr.router, prefix="/api/v1")

    async def _fake_auth():
        return _Auth()

    async def _member_ok(scope_id, user_id):
        return scope_id != "666"

    application.dependency_overrides[get_auth] = _fake_auth
    monkeypatch.setattr(pr, "_is_member", _member_ok)
    fake = _FakeService()
    monkeypatch.setattr(pr, "_service", lambda: fake)
    application.state.fake = fake
    return application


@pytest.mark.asyncio
async def test_list_passes_filters(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/prompts?scope_id=9000&segment=project&project_id=55"
            "&form=album&origin=captioned&q=rain&limit=20&offset=40"
        )
    assert r.status_code == 200 and r.json()["success"] is True
    _, scope, kw = app.state.fake.calls[0]
    assert scope == 9000 and kw == {
        "segment": "project",
        "project_id": 55,
        "form": "album",
        "origin": "captioned",
        "q": "rain",
        "limit": 20,
        "offset": 40,
    }


@pytest.mark.asyncio
async def test_bad_segment_is_422(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts?scope_id=9000&segment=everything")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_non_member_403_in_envelope(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts?scope_id=666")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_a_member"


@pytest.mark.asyncio
async def test_counts(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/prompts/counts?scope_id=9000")
    assert r.status_code == 200 and r.json()["data"] == {
        "mine": 3,
        "project": None,
        "system": 0,
    }
