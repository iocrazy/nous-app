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


class _TeamOk:
    async def get_team_by_id(self, team_id, user_id):
        return {"id": team_id}


async def test_summary_carries_the_efficiency_counters(monkeypatch):
    """小时表的五个计数列必须一路到 wire——UI 的六枚 tile 全靠它们。"""
    counters = {
        "run_count": 5,
        "failed_runs": 1,
        "tool_calls": 20,
        "tool_errors": 2,
        "deliverables": 8,
    }

    async def _summarize(**kw):
        return {
            "total": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "cached_input_tokens": 0,
                "cost_cents": 40.0,
                "event_count": 3,
                **counters,
            },
            "groups": [
                {
                    "grp": "doubao",
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "cost_cents": 40.0,
                    "event_count": 3,
                    **counters,
                }
            ],
            "daily": [
                {
                    "day": "2026-09-15",
                    "grp": "doubao",
                    "total_tokens": 12,
                    "cost_cents": 40.0,
                    **counters,
                }
            ],
        }

    monkeypatch.setattr(ur.usage_repository, "summarize", _summarize)
    monkeypatch.setattr(ur, "get_team_repository", lambda: _TeamOk())
    out = await ur.usage_summary(_AuthStub(), team_id="1", group_by="model")
    assert (out.total.run_count, out.total.tool_errors) == (5, 2)
    assert out.total.cost_per_deliverable_cents == 5.0
    assert out.groups[0].deliverables == 8
    assert out.daily[0].run_count == 5


async def test_a_window_with_no_outputs_has_a_null_ratio(monkeypatch):
    async def _summarize(**kw):
        return {
            "total": {"cost_cents": 40.0, "deliverables": 0},
            "groups": [],
            "daily": [],
        }

    monkeypatch.setattr(ur.usage_repository, "summarize", _summarize)
    monkeypatch.setattr(ur, "get_team_repository", lambda: _TeamOk())
    assert (
        await ur.usage_summary(_AuthStub(), team_id="1", group_by="model")
    ).total.cost_per_deliverable_cents is None


async def test_a_read_failure_is_a_typed_503_not_a_zeroed_dashboard(monkeypatch):
    """A rollup read that fails must not render as a month with no spend.

    Same failure shape as the two AI-library endpoints: ``details.code`` in the
    ErrorResponse envelope, not prose. The concrete case this guards is a
    42703 during the window between a migration landing and the code that reads
    its new columns — the panel would otherwise show zeros and look calm.
    """

    async def _summarize(**kw):
        raise RuntimeError("column ai_usage_hourly.deliverables does not exist")

    monkeypatch.setattr(ur.usage_repository, "summarize", _summarize)
    monkeypatch.setattr(ur, "get_team_repository", lambda: _TeamOk())
    with pytest.raises(ur.HTTPException) as e:
        await ur.usage_summary(_AuthStub(), team_id="1", group_by="model")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "usage_summary_unavailable"


@pytest.mark.parametrize(
    "frm,to,fragment",
    [
        # Reversed window: the caller asked for nothing, and an empty answer
        # would look like a quiet month rather than a bad request.
        ("2026-09-15", "2026-09-01", "must be before"),
        # Past the shared cap. This is the branch that keeps a full scan of
        # ai_usage_hourly off the connection.
        ("2020-01-01", "2026-01-01", "range exceeds"),
    ],
)
async def test_an_unusable_window_is_refused_before_any_read(
    monkeypatch, frm, to, fragment
):
    """Both rejection branches, through the real endpoint function.

    They are string comparisons against ``window_error``'s codes, so deleting a
    branch or misspelling a code is silent — nothing else in the suite reads
    them. The stub summarize raises to prove the refusal happens BEFORE the
    read, not after it.
    """

    async def _must_not_run(**kw):
        raise AssertionError("the window was rejected too late")

    monkeypatch.setattr(ur.usage_repository, "summarize", _must_not_run)
    monkeypatch.setattr(ur, "get_team_repository", lambda: _TeamOk())
    with pytest.raises(ur.HTTPException) as e:
        await ur.usage_summary(
            _AuthStub(), team_id="1", group_by="model", frm=frm, to=to
        )
    assert e.value.status_code == 400
    assert fragment in str(e.value.detail)
