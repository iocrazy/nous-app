"""Un-configured LLM provider must FAIL FAST with an actionable error.

History: the old implicit localhost:8000 default was removed first (#1112),
then env credentials were retired entirely (铁律 2026-07-07) — resolution is
DB-only (mediahub_models catalog → user BYOK → ProviderNotConfiguredError).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.adapters.factory import (
    ProviderNotConfiguredError,
    get_adapter,
    get_adapter_for_user,
)


def test_env_credential_fields_are_retired():
    """LLM/DeepSeek/Doubao/Claude credential fields no longer exist on
    Settings — DB-only credentials cannot regress into env silently."""
    from app.core.config import Settings

    for retired in (
        "LLM_API_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "DEEPSEEK_API_URL",
        "DEEPSEEK_API_KEY",
        "DOUBAO_API_URL",
        "DOUBAO_API_KEY",
        "CLAUDE_API_KEY",
    ):
        assert retired not in Settings.model_fields, retired


def test_get_adapter_fails_fast_without_credentials():
    class S:
        LLM_API_URL = "https://env-leak.example/v1"  # must be ignored
        LLM_API_KEY = "sk-env"
        LLM_MODEL = "qwen-max"

    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        get_adapter("qwen-max", S())


def test_get_adapter_for_user_fails_fast_when_nothing_resolves():
    with pytest.raises(ProviderNotConfiguredError, match="not configured"):
        get_adapter_for_user("qwen-max", {}, None)


def test_get_adapter_for_user_byo_base_still_works():
    adapter = get_adapter_for_user(
        "qwen-max",
        {"qwen": {"base_url": "https://byo.example/v1", "api_key": "k"}},
        None,
    )
    assert adapter is not None


@pytest.mark.asyncio
async def test_storyboard_service_fails_fast_when_model_not_in_catalog():
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    svc = StoryboardAIService()
    repo = MagicMock()
    repo.get_by_slug = AsyncMock(return_value={"model": "doubao-unlisted"})
    with (
        patch(
            "app.repositories.agent_repository.get_agent_repository",
            return_value=repo,
        ),
        patch(
            "app.services.ai.providers.ai_provider_helpers.resolve_mediahub_model",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(RuntimeError, match="not configured"):
            await svc._call_llm([{"role": "user", "content": "hi"}])
