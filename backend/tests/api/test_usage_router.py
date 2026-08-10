"""Usage endpoints: team-boundary gating + payload shape (W3c). DB faked."""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_auth

# app.api.__init__ rebinds the name `usage_router` to the router object, so
# import the module via importlib to reach its functions for monkeypatching.
ur = importlib.import_module("app.api.usage_router")


class _AuthStub:
    user_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(ur.router, prefix="/api/v1")

    async def _fake_auth():
        return _AuthStub()

    application.dependency_overrides[get_auth] = _fake_auth
    return application


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_summary_404_for_non_member(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return None  # not a member

    monkeypatch.setattr(ur, "get_team_repository", lambda: _Repo())

    async with await _client(app) as c:
        resp = await c.get("/api/v1/usage/summary?team_id=900&group_by=model")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_summary_bad_group_by_400(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id}

    monkeypatch.setattr(ur, "get_team_repository", lambda: _Repo())

    async with await _client(app) as c:
        resp = await c.get("/api/v1/usage/summary?team_id=900&group_by=nonsense")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_summary_success_shape(app, monkeypatch):
    class _Repo:
        async def get_team_by_id(self, team_id, user_id):
            return {"id": team_id}

    monkeypatch.setattr(ur, "get_team_repository", lambda: _Repo())

    async def fake_summarize(**kwargs):
        return {
            "total": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cached_input_tokens": 0,
                "cost_cents": 1.5,
                "event_count": 2,
            },
            "groups": [
                {
                    "grp": "qwen-max",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost_cents": 1.5,
                    "event_count": 2,
                },
            ],
            "daily": [
                {
                    "day": "2026-07-18",
                    "grp": "qwen-max",
                    "total_tokens": 15,
                    "cost_cents": 1.5,
                },
            ],
        }

    monkeypatch.setattr(ur.usage_repository, "summarize", fake_summarize)

    async with await _client(app) as c:
        resp = await c.get("/api/v1/usage/summary?team_id=900&group_by=model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["team_id"] == "900"
    assert body["total"]["total_tokens"] == 15
    assert body["groups"][0]["key"] == "qwen-max"
    assert body["daily"][0]["day"] == "2026-07-18"
    assert "from" in body and "to" in body


@pytest.mark.asyncio
async def test_issue_usage_404_cross_team(app, monkeypatch):
    class _IssueRepo:
        async def get_by_id(self, issue_id):
            return {
                "id": issue_id,
                "created_by_user_id": "99999999-9999-9999-9999-999999999999",
                "assignee_user_id": None,
                "team_id": 777,
            }

        async def is_team_member(self, user_id, team_id):
            return False

    import app.repositories.issue_repository as ir_mod

    monkeypatch.setattr(ir_mod, "issue_repository", _IssueRepo())

    async with await _client(app) as c:
        resp = await c.get("/api/v1/usage/issues/555")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_issue_usage_success_for_creator(app, monkeypatch):
    class _IssueRepo:
        async def get_by_id(self, issue_id):
            return {
                "id": issue_id,
                "created_by_user_id": _AuthStub.user_id,
                "assignee_user_id": None,
                "team_id": None,
            }

        async def is_team_member(self, user_id, team_id):
            return False

    import app.repositories.issue_repository as ir_mod

    monkeypatch.setattr(ir_mod, "issue_repository", _IssueRepo())

    async def fake_issue_totals(issue_id):
        return {
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70,
            "cost_cents": 0.7,
            "run_count": 2,
        }

    monkeypatch.setattr(ur.usage_repository, "issue_totals", fake_issue_totals)

    async with await _client(app) as c:
        resp = await c.get("/api/v1/usage/issues/555")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issue_id"] == "555" and body["total_tokens"] == 70
