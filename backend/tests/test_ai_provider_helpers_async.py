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


def _settings_repo(ai_settings: dict):
    repo = MagicMock()
    repo.get_by_user_id = AsyncMock(
        return_value={"settings_json": {"ai_settings": ai_settings}}
    )
    return repo


async def test_resolve_returns_empty_when_agent_missing():
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(return_value=None)
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            return_value=_settings_repo({}),
        ),
    ):
        key, cfg, model, slug = await helpers.resolve_analyze_provider_config("u-1")
    assert (key, cfg, model) == ("", {}, "")
    assert slug == "analyze"


async def test_resolve_honors_assigned_visual_analysis_agent():
    """Regression: the resolver must use the user's task_assignment.visual_analysis
    agent slug, NOT a hardcoded 'analyze'. A user who assigned 'test-analyze'
    (doubao) and only configured doubao as BYO must get the doubao model + their
    doubao config — not the default 'analyze' agent's qwen-max with empty config.
    """
    agents = {
        "test-analyze": {"model": "doubao-seed-2-0-pro-260215"},
        "analyze": {"model": "qwen-max"},
    }
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(side_effect=lambda slug: agents.get(slug))
    settings_repo = _settings_repo(
        {
            "task_assignment": {"visual_analysis": "test-analyze"},
            "ai_providers": {
                "doubao": {"api_key": "sk-d", "base_url": "https://ark/api/v3"}
            },
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
    ):
        key, cfg, model, slug = await helpers.resolve_analyze_provider_config("u-1")
    assert key == "doubao"
    assert model == "doubao-seed-2-0-pro-260215"
    assert cfg == {
        "api_key": "sk-d",
        "base_url": "https://ark/api/v3",
        "model": "doubao-seed-2-0-pro-260215",
    }
    # The resolved slug must be the assigned one — so the caller composes the
    # SAME agent's prompt (else composed.model overrides the resolved model).
    assert slug == "test-analyze"


async def test_resolve_falls_back_to_analyze_when_assigned_slug_missing():
    """If the assigned slug doesn't resolve to an agent, fall back to the
    built-in 'analyze' agent rather than returning empty."""
    agents = {"analyze": {"model": "qwen-max"}}
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(side_effect=lambda slug: agents.get(slug))
    settings_repo = _settings_repo(
        {
            "task_assignment": {"visual_analysis": "deleted-agent"},
            "ai_providers": {"qwen": {"api_key": "sk-q"}},
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
    ):
        key, cfg, model, slug = await helpers.resolve_analyze_provider_config("u-1")
    assert key == "qwen"
    assert model == "qwen-max"
    assert cfg == {"api_key": "sk-q", "model": "qwen-max"}
    # Fell back to the built-in agent, so the caller composes 'analyze'.
    assert slug == "analyze"


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
        key, cfg, model, slug = await helpers.resolve_analyze_provider_config(None)
    assert key == "qwen"
    assert cfg == {"model": "qwen-max"}
    assert model == "qwen-max"
    assert slug == "analyze"


async def test_resolve_translate_defaults_to_translate_slug():
    agents = {"translate": {"model": "qwen-max"}}
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(side_effect=lambda slug: agents.get(slug))
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            return_value=_settings_repo({}),
        ),
        patch(
            "app.services.ai.adapters.factory.provider_key_for_model",
            return_value="qwen",
        ),
    ):
        key, cfg, model, slug = await helpers.resolve_translate_provider_config("u-1")
    assert key == "qwen"
    assert model == "qwen-max"
    assert slug == "translate"


async def test_resolve_translate_honors_assignment():
    """task_assignment.translation routes to the user's chosen agent,
    same #622/#623 contract as visual_analysis."""
    agents = {
        "my-translator": {"model": "doubao-seed-2-0-pro-260215"},
        "translate": {"model": "qwen-max"},
    }
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(side_effect=lambda slug: agents.get(slug))
    settings_repo = _settings_repo(
        {
            "task_assignment": {"translation": "my-translator"},
            "ai_providers": {"doubao": {"api_key": "sk-d"}},
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
    ):
        key, cfg, model, slug = await helpers.resolve_translate_provider_config("u-1")
    assert key == "doubao"
    assert model == "doubao-seed-2-0-pro-260215"
    assert cfg["api_key"] == "sk-d"
    assert slug == "my-translator"


async def test_resolve_caption_defaults_to_caption_slug():
    agents = {"caption": {"model": "qwen-max"}}
    agent_repo = MagicMock()
    agent_repo.get_by_slug = AsyncMock(side_effect=lambda slug: agents.get(slug))
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=agent_repo,
        ),
        patch(
            "app.repositories.user_settings_repository.UserSettingsRepository",
            return_value=_settings_repo({}),
        ),
        patch(
            "app.services.ai.adapters.factory.provider_key_for_model",
            return_value="qwen",
        ),
    ):
        key, cfg, model, slug = await helpers.resolve_caption_provider_config("u-1")
    assert key == "qwen"
    assert model == "qwen-max"
    assert slug == "caption"


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
        key, cfg, model, slug = await helpers.resolve_analyze_provider_config("u-1")
    assert key == "qwen"
    # BYO api_key merged + model stamped on top.
    assert cfg == {"api_key": "sk-x", "model": "qwen-max"}
    assert model == "qwen-max"
    assert slug == "analyze"
