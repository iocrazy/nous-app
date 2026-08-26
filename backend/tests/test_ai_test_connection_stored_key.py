"""Test Connection must work with the STORED key (2026-08-26 user report:
after secret-at-rest masking, the button was permanently disabled unless
the user re-typed the key — but the server HAS the key; the server should
test with it)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

r = sys.modules["app.api.ai_settings_router"]

FAKE_USER = "00000000-0000-0000-0000-000000000042"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_blank_key_falls_back_to_stored_key(monkeypatch, client):
    async def _fake_stored(user_id: str, provider_key: str):
        assert user_id == FAKE_USER
        return {"api_key": "sk-stored-secret", "base_url": None, "app_id": None}

    monkeypatch.setattr(r, "_stored_provider_config", _fake_stored)
    with patch(
        "app.services.ai.providers.ai_provider.AIProviderFactory.test_connection",
        new=AsyncMock(return_value={"success": True, "models": ["m1"]}),
    ) as tc:
        resp = await client.post(
            "/api/v1/ai/test-connection",
            json={"provider_key": "deepseek", "api_key": ""},
        )
    assert resp.status_code == 200, resp.text
    assert tc.call_args.kwargs["config"]["api_key"] == "sk-stored-secret"


@pytest.mark.asyncio
async def test_explicit_key_wins_over_stored(monkeypatch, client):
    async def _fake_stored(user_id: str, provider_key: str):
        return {"api_key": "sk-stored", "base_url": None, "app_id": None}

    monkeypatch.setattr(r, "_stored_provider_config", _fake_stored)
    with patch(
        "app.services.ai.providers.ai_provider.AIProviderFactory.test_connection",
        new=AsyncMock(return_value={"success": True, "models": []}),
    ) as tc:
        resp = await client.post(
            "/api/v1/ai/test-connection",
            json={"provider_key": "deepseek", "api_key": "sk-typed"},
        )
    assert resp.status_code == 200
    assert tc.call_args.kwargs["config"]["api_key"] == "sk-typed"
