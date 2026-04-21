"""Tests for the admin-gated reload-seeds endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ai_library_router import router


def _app_with_router() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_router())


@pytest.fixture
def fake_auth():
    """A fake AuthContext-like object the endpoint can read user_id from."""

    class _FakeAuth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "admin@example.com"

    return _FakeAuth()


def test_reload_seeds_rejects_non_admin(client: TestClient, fake_auth):
    """Authenticated user without admin role → 403."""
    app = client.app

    # AuthDep = Annotated[AuthContext, Depends(get_auth)] — override the
    # inner `get_auth` callable, not the AuthDep alias.
    from app.core.deps import get_auth

    async def _override_auth():
        return fake_auth

    app.dependency_overrides[get_auth] = _override_auth

    # Mock the admin role check to return "no admin row"
    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        new=AsyncMock(return_value=_FakeClient(admin_rows=[])),
    ):
        resp = client.post("/api/v1/ai-library/admin/reload-seeds")
        assert resp.status_code == 403
        assert "Admin" in resp.json()["detail"]

    app.dependency_overrides.clear()


def test_reload_seeds_runs_loader_for_admin(client: TestClient, fake_auth):
    """Admin user → SeedLoader.load_all() called, results returned as JSON."""
    app = client.app

    from app.core.deps import get_auth

    async def _override_auth():
        return fake_auth

    app.dependency_overrides[get_auth] = _override_auth

    fake_results = {
        "agents": 1,
        "skills": 3,
        "agent_skill_bindings": 3,
        "errors": [],
    }

    with patch(
        "app.api.ai_library_router.get_async_supabase_admin",
        new=AsyncMock(return_value=_FakeClient(admin_rows=[{"role": "admin"}])),
    ), patch(
        "app.api.ai_library_router.SeedLoader"
    ) as mock_loader_cls:
        mock_loader_cls.return_value.load_all = AsyncMock(return_value=fake_results)
        resp = client.post("/api/v1/ai-library/admin/reload-seeds")
        assert resp.status_code == 200
        assert resp.json() == fake_results
        mock_loader_cls.return_value.load_all.assert_awaited_once()

    app.dependency_overrides.clear()


class _FakeClient:
    """Minimal fake supabase client that returns a pre-baked team_members result."""

    def __init__(self, admin_rows: list) -> None:
        self._admin_rows = admin_rows

    def table(self, _name):
        return self

    def select(self, _cols):
        return self

    def eq(self, _k, _v):
        return self

    def in_(self, _k, _v):
        return self

    def limit(self, _n):
        return self

    async def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self._admin_rows
        return r
