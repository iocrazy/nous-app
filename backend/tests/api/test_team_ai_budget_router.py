"""Team AI budget endpoints: membership + owner/admin gating (W3c). DB faked."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import importlib

from app.core.deps import get_auth

# app.api.__init__ rebinds the name `teams_router` to the router object, so
# import the module via importlib to reach its functions for monkeypatching.
tr = importlib.import_module("app.api.teams_router")

OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


class _AuthStub:
    user_id = OWNER


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(tr.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _patch_usage(monkeypatch, budget_row, spend=Decimal("0")):
    async def get_team_budget(team_id):
        return budget_row

    async def get_spend(team_id):
        return spend

    captured = {}

    async def upsert(team_id, *, monthly_budget_cents, updated_by_user_id):
        captured["team_id"] = team_id
        captured["cents"] = monthly_budget_cents
        captured["uid"] = updated_by_user_id
        return {"team_id": team_id, "monthly_budget_cents": monthly_budget_cents}

    import app.services.ai_usage as au

    monkeypatch.setattr(au, "get_team_budget", get_team_budget)
    monkeypatch.setattr(au, "get_team_month_spend_cents", get_spend)
    monkeypatch.setattr(au, "upsert_team_budget", upsert)
    return captured


@pytest.mark.asyncio
async def test_get_budget_404_non_member(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return None

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    async with await _client(app) as c:
        resp = await c.get("/api/v1/teams/900/ai-budget")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_budget_member_ok_with_over_flag(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id, "owner_id": OWNER}

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    _patch_usage(
        monkeypatch,
        {"monthly_budget_cents": Decimal("500"), "updated_by_user_id": None,
         "updated_at": None},
        spend=Decimal("600"),
    )
    async with await _client(app) as c:
        resp = await c.get("/api/v1/teams/900/ai-budget")
    assert resp.status_code == 200
    body = resp.json()
    assert body["over_budget"] is True
    assert body["month_spend_cents"] == 600.0
    assert body["monthly_budget_cents"] == 500.0


@pytest.mark.asyncio
async def test_put_budget_owner_ok(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id, "owner_id": OWNER}

        async def get_member_role(self, team_id, user_id):
            return "editor"

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    captured = _patch_usage(
        monkeypatch, {"monthly_budget_cents": Decimal("1000"), "updated_by_user_id": OWNER,
                      "updated_at": None}, spend=Decimal("0"))
    async with await _client(app) as c:
        resp = await c.put("/api/v1/teams/900/ai-budget", json={"monthly_budget_cents": 1000})
    assert resp.status_code == 200
    assert captured["cents"] == 1000


@pytest.mark.asyncio
async def test_put_budget_non_owner_non_admin_403(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id, "owner_id": OTHER}  # caller is not owner

        async def get_member_role(self, team_id, user_id):
            return "editor"  # not admin

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    _patch_usage(monkeypatch, None)
    async with await _client(app) as c:
        resp = await c.put("/api/v1/teams/900/ai-budget", json={"monthly_budget_cents": 1000})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_put_budget_admin_ok(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id, "owner_id": OTHER}

        async def get_member_role(self, team_id, user_id):
            return "admin"

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    captured = _patch_usage(
        monkeypatch, {"monthly_budget_cents": None, "updated_by_user_id": OWNER,
                      "updated_at": None})
    async with await _client(app) as c:
        # blank/unlimited
        resp = await c.put("/api/v1/teams/900/ai-budget", json={"monthly_budget_cents": None})
    assert resp.status_code == 200
    assert captured["cents"] is None


@pytest.mark.asyncio
async def test_put_budget_negative_400(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id, "owner_id": OWNER}

    monkeypatch.setattr(tr, "get_team_repository", lambda: _Repo())
    _patch_usage(monkeypatch, None)
    async with await _client(app) as c:
        resp = await c.put("/api/v1/teams/900/ai-budget", json={"monthly_budget_cents": -5})
    assert resp.status_code == 400
