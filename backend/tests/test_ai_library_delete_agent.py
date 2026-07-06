"""Tests for DELETE /api/v1/ai-library/agents/{slug} (2026-07-07).

User-owned agents can be hard-deleted by their creator; system presets
and other users' agents are refused. Before this endpoint existed there
was NO way to delete an agent at all (create/edit/reset only) — test
agents were immortal.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router

_OWNER = "11111111-1111-1111-1111-111111111111"


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


@pytest.fixture
def fake_auth():
    class _FakeAuth:
        user_id = _OWNER
        email = "user@example.com"

    return _FakeAuth()


def _install_auth_override(app: FastAPI, fake_auth) -> None:
    from app.core.deps import get_auth

    async def _grant():
        return fake_auth

    app.dependency_overrides[get_auth] = _grant


def _agent(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": str(uuid4()),
        "slug": "test-analyze",
        "name": "test-analyze",
        "is_system_preset": False,
        "user_id": _OWNER,
    }
    base.update(over)
    return base


def _repo(agent: Dict[str, Any] | None, deleted: bool = True) -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_slug = AsyncMock(return_value=agent)
    repo.delete_agent = AsyncMock(return_value=deleted)
    return repo


def _do_delete(
    client: TestClient, fake_auth, repo: AsyncMock, slug: str = "test-analyze"
):
    _install_auth_override(client.app, fake_auth)
    with patch("app.api.ai_library_router._repos", return_value=(repo, AsyncMock())):
        return client.delete(f"/api/v1/ai-library/agents/{slug}")


def test_owner_deletes_own_agent(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent())
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 204
    repo.delete_agent.assert_awaited_once()


def test_missing_agent_404(client: TestClient, fake_auth) -> None:
    resp = _do_delete(client, fake_auth, _repo(None))
    assert resp.status_code == 404


def test_system_preset_403(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(is_system_preset=True))
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 403
    repo.delete_agent.assert_not_awaited()


def test_non_creator_403(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(user_id=str(uuid4())))
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 403
    repo.delete_agent.assert_not_awaited()


def test_repo_failure_500(client: TestClient, fake_auth) -> None:
    repo = _repo(_agent(), deleted=False)
    resp = _do_delete(client, fake_auth, repo)
    assert resp.status_code == 500
