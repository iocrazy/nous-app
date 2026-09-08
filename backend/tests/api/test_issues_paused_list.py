"""Phase 2a Task 8: GET /issues/paused — the caller's visible issues with
paused_at set (and not hidden), same family as /issues/needs-input."""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

r = importlib.import_module("app.api.issues_router")
from app.repositories import issue_repository as repo_mod  # noqa: E402

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
AUTH = SimpleNamespace(user_id=UUID(ME))
T0 = dt.datetime(2026, 9, 8, 10, 0, tzinfo=dt.timezone.utc)


def test_paused_predicate_and_list_stmt():
    sql = str(repo_mod.paused_predicate().compile(dialect=postgresql.dialect())).lower()
    assert "paused_at is not null" in sql


async def test_list_paused_endpoint_maps_rows(monkeypatch):
    rows = [
        {
            "id": 7,
            "identifier": "N-7",
            "title": "Cut the trailer",
            "paused_at": T0,
            "team_id": 2002,
            "project_id": None,
            "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }
    ]
    monkeypatch.setattr(
        r, "issue_repository", SimpleNamespace(list_paused=AsyncMock(return_value=rows))
    )
    out = await r.list_paused(AUTH)
    assert [i.model_dump() for i in out.items] == [
        {
            "issue_id": "7",
            "identifier": "N-7",
            "title": "Cut the trailer",
            "paused_at": T0,
            "team_id": "2002",
            "project_id": None,
            "assignee_agent_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        }
    ]
    r.issue_repository.list_paused.assert_awaited_once_with(ME)


def test_paused_route_is_declared_before_the_id_route():
    """FastAPI matches in declaration order: /{issue_id}'s int converter would
    422 the literal segment "paused" if that route came first."""
    src = inspect.getsource(r)
    assert src.index('@router.get("/paused"') < src.index('@router.get("/{issue_id}"')


async def test_repository_list_paused_uses_visibility_and_hidden_filters(monkeypatch):
    captured = {}

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _Session:
        async def execute(self, stmt):
            captured["sql"] = str(stmt.compile(dialect=postgresql.dialect())).lower()
            return _Res()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(repo_mod, "read_scope", lambda: _Session())
    assert await repo_mod.IssueRepository().list_paused(ME) == []
    sql = captured["sql"]
    assert "paused_at is not null" in sql and "hidden_at is null" in sql
    assert "public.team_members" in sql
    assert "order by public.issues.paused_at desc" in sql
