"""509: POST / PATCH /issues stamp acceptance_criteria_source='user';
clear_acceptance_criteria writes NULL to both columns."""

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
        created_at="2026-09-26T00:00:00+00:00",
        updated_at="2026-09-26T00:00:00+00:00",
    )
    base.update(kw)
    return base


def _client(monkeypatch, **repo: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(r.router, prefix="/api/v1")
    from app.core.deps import get_auth

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    monkeypatch.setattr(
        r.issue_repository, "get_by_id", AsyncMock(return_value=_issue())
    )
    for name, mock in repo.items():
        monkeypatch.setattr(r.issue_repository, name, mock)
    return TestClient(app)


def test_create_with_criteria_stamps_user_source(monkeypatch):
    create = AsyncMock(
        return_value=_issue(
            acceptance_criteria="two shots", acceptance_criteria_source="user"
        )
    )
    resp = _client(monkeypatch, atomic_create=create).post(
        "/api/v1/issues/", json={"title": "t", "acceptance_criteria": "two shots"}
    )
    assert resp.status_code == 201, resp.text
    body = create.await_args.args[0]
    assert body["acceptance_criteria"] == "two shots"
    assert body["acceptance_criteria_source"] == "user"
    assert resp.json()["acceptance_criteria_source"] == "user"


def test_create_without_criteria_leaves_source_unset(monkeypatch):
    create = AsyncMock(return_value=_issue())
    resp = _client(monkeypatch, atomic_create=create).post(
        "/api/v1/issues/", json={"title": "t"}
    )
    assert resp.status_code == 201, resp.text
    assert "acceptance_criteria_source" not in create.await_args.args[0]


def test_create_rejects_criteria_over_the_cap(monkeypatch):
    create = AsyncMock()
    resp = _client(monkeypatch, atomic_create=create).post(
        "/api/v1/issues/", json={"title": "t", "acceptance_criteria": "x" * 4001}
    )
    assert resp.status_code == 422
    create.assert_not_awaited()


def test_patch_criteria_stamps_user_source(monkeypatch):
    update = AsyncMock(return_value=_issue(acceptance_criteria="two shots"))
    resp = _client(monkeypatch, update=update).patch(
        "/api/v1/issues/7", json={"acceptance_criteria": "two shots"}
    )
    assert resp.status_code == 200, resp.text
    assert update.await_args.args == (
        7,
        {"acceptance_criteria": "two shots", "acceptance_criteria_source": "user"},
    )


def test_clear_wins_over_criteria_sent_alongside(monkeypatch):
    update = AsyncMock(return_value=_issue())
    resp = _client(monkeypatch, update=update).patch(
        "/api/v1/issues/7",
        json={"acceptance_criteria": "ignored", "clear_acceptance_criteria": True},
    )
    assert resp.status_code == 200, resp.text
    assert update.await_args.args == (
        7,
        {"acceptance_criteria": None, "acceptance_criteria_source": None},
    )


def test_read_lifts_verification_from_execution_state(monkeypatch):
    verdict = {"verdict": "fail", "attempt": 1, "unmet": ["two shots"]}
    client = _client(monkeypatch)
    monkeypatch.setattr(
        r.issue_repository,
        "get_by_id",
        AsyncMock(return_value=_issue(execution_state={"verification": verdict})),
    )
    monkeypatch.setattr(r, "_assert_visibility", AsyncMock())
    resp = client.get("/api/v1/issues/7")
    assert resp.status_code == 200, resp.text
    assert resp.json()["verification"] == verdict
