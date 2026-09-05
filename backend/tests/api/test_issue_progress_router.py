"""GET /issues/{id}/progress — visibility, gate, rollup passthrough."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

r = importlib.import_module("app.api.issue_progress_router")
pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


def _client(monkeypatch, visible=True):
    app = FastAPI()
    app.include_router(r.router, prefix="/api/v1")
    from app.core.deps import get_auth
    from app.services.modules.gate import require_module

    async def _grant():
        return SimpleNamespace(user_id=ME)

    app.dependency_overrides[get_auth] = _grant
    # the gate is a Depends(require_module("todolist")) call result — override by key
    for dep in r.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None
    issue = {"id": 7, "status": "in_progress"}
    monkeypatch.setattr(
        r,
        "assert_issue_visible",
        (
            AsyncMock(return_value=issue)
            if visible
            else AsyncMock(side_effect=HTTPException(404, "not found"))
        ),
    )
    monkeypatch.setattr(
        r, "load_rollup", AsyncMock(return_value={"issue_id": "7", "phase": "running"})
    )
    return TestClient(app)


def test_progress_returns_the_rollup(monkeypatch):
    resp = _client(monkeypatch).get("/api/v1/issues/7/progress")
    assert resp.status_code == 200 and resp.json() == {
        "issue_id": "7",
        "phase": "running",
    }
    r.load_rollup.assert_awaited_once_with({"id": 7, "status": "in_progress"})


def test_progress_is_404_when_not_visible(monkeypatch):
    assert (
        _client(monkeypatch, visible=False).get("/api/v1/issues/7/progress").status_code
        == 404
    )


def test_progress_router_carries_the_todolist_module_gate():
    assert (
        r.router.dependencies
    ), "progress must sit behind the same module gate as /issues"
