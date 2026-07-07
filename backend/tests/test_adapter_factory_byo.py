"""Tests for get_adapter_for_user — per-user / platform DB credentials.

Phase 2 PR 2.8b introduced this factory for user BYOK. 2026-07-07 (铁律):
credentials are DB-ONLY — the old ``fallback_settings`` env leg is dead.
A provider with no resolved key raises ProviderNotConfiguredError; the
``fallback_settings`` argument is ignored entirely.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.factory import (
    ProviderNotConfiguredError,
    get_adapter,
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter


def _env_settings() -> SimpleNamespace:
    """A settings object carrying the RETIRED env credential fields.

    Used to prove they are ignored: if any branch regresses into reading
    env credentials again, these sentinel values would leak into adapters
    and the fail-fast assertions below would break.
    """
    return SimpleNamespace(
        LLM_API_URL="https://env-leak.example/v1",
        LLM_API_KEY="sk-env-leak-qwen",
        LLM_MODEL="qwen-max",
        DEEPSEEK_API_URL="https://env-leak.example/deepseek",
        DEEPSEEK_API_KEY="sk-env-leak-deepseek",
        DOUBAO_API_URL="https://env-leak.example/doubao",
        DOUBAO_API_KEY="sk-env-leak-doubao",
        CLAUDE_API_KEY="sk-env-leak-claude",
        OPENAI_API_KEY="sk-env-leak-openai",
        OPENAI_MODEL="gpt-4o",
    )


# ---------------------------------------------------------------------------
# provider_key_for_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("qwen-max", "qwen"),
        ("tongyi-plus", "qwen"),
        ("", "qwen"),
        ("deepseek-chat", "deepseek"),
        ("doubao-seed-1", "doubao"),
        ("ep-20240101-abcdef", "doubao"),
        ("claude-opus-4-5", "claude"),
        # V1: native OpenAI family for multimodal (visual_analysis agent).
        ("gpt-4o", "openai"),
        ("gpt-4", "openai"),
        ("gpt-3.5-turbo", "openai"),
        ("o1", "openai"),
        ("o1-mini", "openai"),
        ("o3", "openai"),
        ("o3-mini", "openai"),
        # ModelScope org/name ids — slash wins over any prefix rule.
        ("deepseek-ai/DeepSeek-V3", "modelscope"),
        ("Qwen/Qwen2.5-72B-Instruct", "modelscope"),
    ],
)
def test_provider_key_for_model(model: str, expected: str) -> None:
    assert provider_key_for_model(model) == expected


def test_provider_key_for_model_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        provider_key_for_model("some-unknown-provider-x")


# ---------------------------------------------------------------------------
# get_adapter_for_user — resolved DB credentials build adapters
# ---------------------------------------------------------------------------


def test_user_byo_key_builds_qwen_adapter() -> None:
    user_cfg = {"qwen": {"api_key": "sk-user-qwen", "base_url": "https://custom/v1"}}
    a = get_adapter_for_user("qwen-max", user_cfg, None)
    assert isinstance(a, QwenAdapter)
    assert a.api_key == "sk-user-qwen"
    # OpenAICompatibleAdapter appends /chat/completions if missing
    assert "custom" in a.api_url


def test_user_byo_key_builds_doubao_adapter() -> None:
    user_cfg = {"doubao": {"api_key": "sk-user-doubao"}}
    a = get_adapter_for_user("doubao-pro-32k", user_cfg, None)
    assert isinstance(a, DoubaoAdapter)
    assert a.api_key == "sk-user-doubao"
    # No base_url configured → official Ark endpoint constant.
    assert "volces.com" in a.api_url


def test_user_byo_key_builds_deepseek_adapter() -> None:
    user_cfg = {"deepseek": {"api_key": "sk-user-deepseek"}}
    a = get_adapter_for_user("deepseek-chat", user_cfg, None)
    assert isinstance(a, DeepSeekAdapter)
    assert a.api_key == "sk-user-deepseek"
    assert "api.deepseek.com" in a.api_url


def test_user_byo_key_builds_claude_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key: str, timeout: Any = None) -> None:
            captured["api_key"] = api_key

    monkeypatch.setattr(
        "app.services.ai.adapters.claude.AsyncAnthropic", _FakeAnthropic
    )
    user_cfg = {"claude": {"api_key": "sk-user-claude"}}
    a = get_adapter_for_user("claude-opus-4-5", user_cfg, None)
    assert isinstance(a, ClaudeAdapter)
    assert captured["api_key"] == "sk-user-claude"


def test_user_byo_openai_key_builds_openai_adapter() -> None:
    user_cfg = {"openai": {"api_key": "sk-user-personal"}}
    a = get_adapter_for_user("gpt-4o", user_cfg, None)
    assert isinstance(a, OpenAIAdapter)
    assert a.api_key == "sk-user-personal"
    assert a.api_url.endswith("/chat/completions")


def test_ep_prefix_routes_to_doubao() -> None:
    a = get_adapter_for_user("ep-20240101-abcdef", {"doubao": {"api_key": "k"}}, None)
    assert isinstance(a, DoubaoAdapter)


# ---------------------------------------------------------------------------
# 铁律: no resolved key → ProviderNotConfiguredError; env settings IGNORED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,provider",
    [
        ("qwen-max", "qwen"),
        ("deepseek-chat", "deepseek"),
        ("doubao-pro", "doubao"),
        ("claude-opus-4-5", "claude"),
        ("gpt-4o", "openai"),
    ],
)
def test_no_config_raises_provider_not_configured(model: str, provider: str) -> None:
    with pytest.raises(ProviderNotConfiguredError) as exc_info:
        get_adapter_for_user(model, {}, None)
    assert exc_info.value.provider == provider
    # Error message points users to Settings, not env vars.
    assert "Settings" in str(exc_info.value)


def test_env_settings_are_ignored_not_read() -> None:
    # Even when a fully-populated settings object is passed, no branch may
    # read credentials from it — the env leg is dead.
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter_for_user("doubao-pro", {}, _env_settings())


def test_blank_user_key_raises_instead_of_env_fallback() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter_for_user("doubao-pro", {"doubao": {"api_key": ""}}, _env_settings())


def test_none_provider_config_raises() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter_for_user("claude-opus-4-5", None, _env_settings())  # type: ignore[arg-type]


def test_qwen_requires_base_url() -> None:
    # Qwen-compatible endpoints have no universal public URL — a key without
    # a base_url is not a usable credential set.
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter_for_user("qwen-max", {"qwen": {"api_key": "sk-only"}}, None)


def test_unknown_model_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        get_adapter_for_user("some-unknown-provider-x", {}, None)


# ---------------------------------------------------------------------------
# get_adapter — deprecated shim delegates with NO credentials
# ---------------------------------------------------------------------------


def test_get_adapter_shim_ignores_settings_and_fails_fast() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter("gpt-4o", _env_settings())


def test_get_adapter_shim_fails_fast_for_doubao() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter("doubao-seed-2-0-lite-260428", _env_settings())
