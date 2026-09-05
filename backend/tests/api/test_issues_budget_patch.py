"""PATCH /issues/{id}: budget_cents is validated non-negative; clear_budget writes NULL."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

r = importlib.import_module("app.api.issues_router")
pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


def _issue(**kw):
    base = dict(
        id=7,
        issue_number=7,
        identifier="NOUS-7",
        title="t",
        status="todo",
        priority="medium",
        created_by_user_id=ME,
        assignee_user_id=None,
        team_id=None,
        project_id=None,
        origin_kind="manual",
        created_at="2026-09-05T00:00:00+00:00",
        updated_at="2026-09-05T00:00:00+00:00",
        budget_cents=None,
    )
    base.update(kw)
    return base


def _client(monkeypatch, update: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(r.router, prefix="/api/v1")
    from app.core.deps import get_auth

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    monkeypatch.setattr(
        r.issue_repository, "get_by_id", AsyncMock(return_value=_issue())
    )
    monkeypatch.setattr(r.issue_repository, "update", update)
    return TestClient(app)


def test_budget_is_written_as_int(monkeypatch):
    update = AsyncMock(return_value=_issue(budget_cents=500))
    resp = _client(monkeypatch, update).patch(
        "/api/v1/issues/7", json={"budget_cents": 500}
    )
    assert resp.status_code == 200, resp.text
    assert update.await_args.args == (7, {"budget_cents": 500})
    assert resp.json()["budget_cents"] == 500


def test_negative_budget_is_422(monkeypatch):
    update = AsyncMock()
    resp = _client(monkeypatch, update).patch(
        "/api/v1/issues/7", json={"budget_cents": -1}
    )
    assert resp.status_code == 422
    update.assert_not_awaited()


def test_clear_budget_writes_null(monkeypatch):
    update = AsyncMock(return_value=_issue())
    resp = _client(monkeypatch, update).patch(
        "/api/v1/issues/7", json={"clear_budget": True}
    )
    assert resp.status_code == 200, resp.text
    assert update.await_args.args == (7, {"budget_cents": None})
