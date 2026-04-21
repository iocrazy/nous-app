"""Tests for the admin-gated reload-seeds endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
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
    class _FakeAuth:
        user_id = "11111111-1111-1111-1111-111111111111"
        email = "admin@example.com"

    return _FakeAuth()


def test_reload_seeds_rejects_non_admin(client: TestClient):
    """When AdminAuthDep raises 403, the endpoint returns 403."""
    app = client.app
    from app.core.admin_deps import get_admin_auth

    async def _deny():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    app.dependency_overrides[get_admin_auth] = _deny
    try:
        resp = client.post("/api/v1/ai-library/admin/reload-seeds")
        assert resp.status_code == 403
        assert "Admin" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_reload_seeds_runs_loader_for_admin(
    client: TestClient, fake_auth
):
    """When AdminAuthDep grants access, SeedLoader.load_all() runs and its
    result is returned as the response body."""
    app = client.app
    from app.core.admin_deps import get_admin_auth

    async def _grant():
        return fake_auth

    app.dependency_overrides[get_admin_auth] = _grant

    fake_results = {
        "agents": 1,
        "skills": 3,
        "agent_skill_bindings": 3,
        "errors": [],
    }

    try:
        with patch("app.api.ai_library_router.SeedLoader") as mock_loader_cls:
            mock_loader_cls.return_value.load_all = AsyncMock(return_value=fake_results)
            resp = client.post("/api/v1/ai-library/admin/reload-seeds")
            assert resp.status_code == 200
            assert resp.json() == fake_results
            mock_loader_cls.return_value.load_all.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
