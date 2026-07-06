"""Un-configured LLM provider must FAIL FAST with an actionable error —
never dial the old implicit localhost:8000 default (prod's .env never set
LLM_API_URL, so the qwen fallback path was a live landmine surfacing as
opaque connect timeouts)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.adapters.factory import get_adapter, get_adapter_for_user


def test_default_llm_api_url_is_empty():
    """The silent localhost default is gone — Settings ships empty."""
    from app.core.config import Settings

    assert Settings.model_fields["LLM_API_URL"].default == ""


def test_get_adapter_fails_fast_without_llm_url():
    class S:
        LLM_API_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = "qwen-max"

    with pytest.raises(ValueError, match="not configured"):
        get_adapter("qwen-max", S())


def test_get_adapter_for_user_fails_fast_when_nothing_resolves():
    class FallbackSettings:
        LLM_API_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = "qwen-max"

    with pytest.raises(ValueError, match="not configured"):
        get_adapter_for_user("qwen-max", {}, FallbackSettings())


def test_get_adapter_for_user_byo_base_still_works():
    class FallbackSettings:
        LLM_API_URL = ""
        LLM_API_KEY = ""
        LLM_MODEL = "qwen-max"

    adapter = get_adapter_for_user(
        "qwen-max",
        {"qwen": {"base_url": "https://byo.example/v1", "api_key": "k"}},
        FallbackSettings(),
    )
    assert adapter is not None


@pytest.mark.asyncio
async def test_storyboard_service_fails_fast_without_url():
    from app.services.storyboard.storyboard_ai_service import StoryboardAIService

    svc = StoryboardAIService()
    with patch("app.services.storyboard.storyboard_ai_service.settings") as s:
        s.LLM_API_URL = ""
        s.LLM_API_KEY = ""
        with pytest.raises(RuntimeError, match="not configured"):
            await svc._call_llm([{"role": "user", "content": "hi"}])
