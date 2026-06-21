# backend/tests/test_admin_nous_probe.py
"""Admin nous probe-models endpoint: self-check a provider key → list models."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.nous import NousProbeRequest


@pytest.mark.asyncio
async def test_probe_models_returns_provider_models():
    """probe_nous_models forwards provider/key/base_url to test_connection and
    returns the fetched model list (so admin selects instead of hand-typing)."""
    from app.api.admin.nous_router import probe_nous_models

    body = NousProbeRequest(
        provider_key="deepseek",
        api_key="sk-x",
        base_url="https://api.deepseek.com",
    )
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    captured = {}

    async def _fake_test_connection(*, provider_key, config):
        captured["provider_key"] = provider_key
        captured["config"] = config
        return {
            "success": True,
            "models": ["deepseek-chat", "deepseek-reasoner"],
            "error": None,
        }

    with patch(
        "app.api.admin.nous_router.AIProviderFactory.test_connection",
        new=AsyncMock(side_effect=_fake_test_connection),
    ):
        resp = await probe_nous_models(body, fake_auth)

    assert resp.success is True
    assert "deepseek-chat" in resp.models
    assert captured["provider_key"] == "deepseek"
    assert captured["config"]["api_key"] == "sk-x"
    assert captured["config"]["base_url"] == "https://api.deepseek.com"


@pytest.mark.asyncio
async def test_probe_models_surfaces_failure():
    """A provider that can't list models (e.g. volcengine ASR) returns
    success=False + error; the endpoint passes it through (UI falls back to
    manual entry)."""
    from app.api.admin.nous_router import probe_nous_models

    body = NousProbeRequest(provider_key="volcengine", api_key="k", app_id="a")
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    with patch(
        "app.api.admin.nous_router.AIProviderFactory.test_connection",
        new=AsyncMock(
            return_value={"success": False, "models": None, "error": "no /models"}
        ),
    ):
        resp = await probe_nous_models(body, fake_auth)

    assert resp.success is False
    assert resp.models is None
    assert resp.error == "no /models"


@pytest.mark.asyncio
async def test_probe_blank_key_falls_back_to_stored_key():
    """Edit form sends a blank api_key (stored key never leaves the server).
    When ``name`` references an existing model, the probe reuses its stored
    key + app_id — so 'Test & Load Models' works without re-typing the key."""
    from app.api.admin.nous_router import probe_nous_models

    body = NousProbeRequest(
        provider_key="openai",
        api_key="",  # blank — edit form doesn't carry the stored key
        base_url="http://10.0.0.10:8000/v1",
        name="nous-qwen3-llm",
    )
    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    repo = MagicMock()
    repo.get_by_name = AsyncMock(
        return_value={"api_key": "stored-secret", "app_id": "stored-app"}
    )

    captured = {}

    async def _fake_test_connection(*, provider_key, config):
        captured["config"] = config
        return {"success": True, "models": ["qwen3-6-35b"], "error": None}

    with patch("app.api.admin.nous_router.get_nous_repository", return_value=repo):
        with patch(
            "app.api.admin.nous_router.AIProviderFactory.test_connection",
            new=AsyncMock(side_effect=_fake_test_connection),
        ):
            resp = await probe_nous_models(body, fake_auth)

    assert resp.success is True
    assert captured["config"]["api_key"] == "stored-secret"
    assert captured["config"]["app_id"] == "stored-app"
    repo.get_by_name.assert_awaited_once_with("nous-qwen3-llm")
