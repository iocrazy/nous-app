"""Un-configured LLM provider must FAIL FAST with an actionable error.

History: the old implicit localhost:8000 default was removed first (#1112),
then env credentials were retired entirely (铁律 2026-07-07) — resolution is
DB-only (mediahub_models catalog → user BYOK → ProviderNotConfiguredError).
"""

from __future__ import annotations

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
