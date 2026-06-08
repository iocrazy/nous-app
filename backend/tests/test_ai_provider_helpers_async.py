"""Unit tests for the §2.4b async-hoisted AI-provider config helpers.

Pins the async contract (the helpers are coroutine functions now, no
``run_async`` shim) and the resolution logic, using mocked repos so the
suite runs without a DB. A companion integration test
(``tests/integration/test_ai_provider_helpers_integration.py``) exercises
the real async repo reads against the dev Postgres.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.providers import ai_provider_helpers as helpers

pytestmark = pytest.mark.asyncio


async def test_helpers_are_coroutine_functions():
    # §2.4b: the DB-reading helpers must be async (awaited from the async
    # workflow), and the run_async shim must be gone.
    assert inspect.iscoroutinefunction(helpers.get_ai_settings)
    assert inspect.iscoroutinefunction(helpers.resolve_analyze_provider_config)
    assert not hasattr(helpers, "_run_async")


async def test_get_ai_settings_extracts_nested_ai_settings():
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value={"settings_json": {"ai_settings": {"k": "v"}}}
    )
    with patch(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        return_value=repo,
    ):
        out = await helpers.get_ai_settings("u-1")
    assert out == {"k": "v"}
    repo.get_by_user_id.assert_awaited_once_with("u-1")


async def test_get_ai_settings_empty_when_no_row():
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(return_value=None)
    with patch(
        "app.repositories.user_settings_repository.UserSettingsRepository",
        return_value=repo,
    ):
        assert await helpers.get_ai_settings("u-1") == {}


async def test_resolve_returns_empty_when_agent_missing():
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=None)
    with patch(
        "app.repositories.agent_repository.get_agent_repository",
        return_value=agent_repo,
    ):
        key, cfg, model = await helpers.resolve_analyze_provider_config("u-1")
    assert (key, cfg, model) == ("", {}, "")


async def test_resolve_no_user_returns_model_only():
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value={"model": "qwen-max"})
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.services.ai.adapters.factory.provider_key_for_model",
            return_value="qwen",
        ),
    ):
        key, cfg, model = await helpers.resolve_analyze_provider_config(None)
    assert key == "qwen"
    assert cfg == {"model": "qwen-max"}
    assert model == "qwen-max"


async def test_resolve_merges_user_byo_provider_config():
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value={"model": "qwen-max"})
    settings_repo = MagicMock()
    settings_repo.get_by_user_id = AsyncMock(
        return_value={
            "settings_json": {
                "ai_settings": {"ai_providers": {"qwen": {"api_key": "sk-x"}}}
            }
        }
    )
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            return_value=settings_repo,
        ),
        patch(
            "app.services.ai.adapters.factory.provider_key_for_model",
            return_value="qwen",
        ),
    ):
        key, cfg, model = await helpers.resolve_analyze_provider_config("u-1")
    assert key == "qwen"
    # BYO api_key merged + model stamped on top.
    assert cfg == {"api_key": "sk-x", "model": "qwen-max"}
    assert model == "qwen-max"
