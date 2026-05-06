"""Tests for get_adapter_for_user — per-user BYO credentials with fallback.

Phase 2 PR 2.8b: Celery tasks resolve a user-picked agent slug → agent row's
model → provider prefix → this factory. User's ``ai_providers[provider]``
config must take precedence; unset keys fall back to global settings.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.factory import (
    get_adapter,
    get_adapter_for_user,
    provider_key_for_model,
)
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter


def _settings(**overrides: Any) -> SimpleNamespace:
    defaults: Dict[str, Any] = {
        "LLM_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "LLM_API_KEY": "sk-global-qwen",
        "LLM_MODEL": "qwen-max",
        "DEEPSEEK_API_URL": "https://api.deepseek.com/v1/chat/completions",
        "DEEPSEEK_API_KEY": "sk-global-deepseek",
        "DOUBAO_API_URL": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "DOUBAO_API_KEY": "sk-global-doubao",
        "CLAUDE_API_KEY": "sk-global-claude",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
        ("o1-preview", "openai"),
        ("o3-mini", "openai"),
    ],
)
def test_provider_key_for_model(model: str, expected: str) -> None:
    assert provider_key_for_model(model) == expected


def test_provider_key_for_model_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        provider_key_for_model("some-unknown-provider-x")


# ---------------------------------------------------------------------------
# get_adapter_for_user — user BYO takes precedence
# ---------------------------------------------------------------------------


def test_user_byo_key_takes_precedence_qwen() -> None:
    user_cfg = {"qwen": {"api_key": "sk-user-qwen", "base_url": "https://custom/v1"}}
    a = get_adapter_for_user("qwen-max", user_cfg, _settings())
    assert isinstance(a, QwenAdapter)
    assert a.api_key == "sk-user-qwen"
    # OpenAICompatibleAdapter appends /chat/completions if missing
    assert "custom" in a.api_url


def test_user_byo_key_takes_precedence_doubao() -> None:
    user_cfg = {"doubao": {"api_key": "sk-user-doubao"}}
    a = get_adapter_for_user("doubao-pro-32k", user_cfg, _settings())
    assert isinstance(a, DoubaoAdapter)
    assert a.api_key == "sk-user-doubao"


def test_user_byo_key_takes_precedence_deepseek() -> None:
    user_cfg = {"deepseek": {"api_key": "sk-user-deepseek"}}
    a = get_adapter_for_user("deepseek-chat", user_cfg, _settings())
    assert isinstance(a, DeepSeekAdapter)
    assert a.api_key == "sk-user-deepseek"


def test_user_byo_key_takes_precedence_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key: str, timeout: Any = None) -> None:
            captured["api_key"] = api_key

    monkeypatch.setattr(
        "app.services.ai.adapters.claude.AsyncAnthropic", _FakeAnthropic
    )
    user_cfg = {"claude": {"api_key": "sk-user-claude"}}
    a = get_adapter_for_user("claude-opus-4-5", user_cfg, _settings())
    assert isinstance(a, ClaudeAdapter)
    assert captured["api_key"] == "sk-user-claude"


# ---------------------------------------------------------------------------
# Fallback to global settings when user has no config
# ---------------------------------------------------------------------------


def test_user_empty_falls_back_to_settings_qwen() -> None:
    a = get_adapter_for_user("qwen-max", {}, _settings())
    assert isinstance(a, QwenAdapter)
    assert a.api_key == "sk-global-qwen"


def test_user_empty_dict_falls_back_to_settings_doubao() -> None:
    # user has the provider key but blank values
    a = get_adapter_for_user("doubao-pro", {"doubao": {"api_key": ""}}, _settings())
    assert isinstance(a, DoubaoAdapter)
    assert a.api_key == "sk-global-doubao"


def test_user_none_provider_config_falls_back_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    class _FakeAnthropic:
        def __init__(self, *, api_key: str, timeout: Any = None) -> None:
            captured["api_key"] = api_key

    monkeypatch.setattr(
        "app.services.ai.adapters.claude.AsyncAnthropic", _FakeAnthropic
    )
    # user_provider_config passed as None is treated as empty
    a = get_adapter_for_user("claude-opus-4-5", None, _settings())  # type: ignore[arg-type]
    assert isinstance(a, ClaudeAdapter)
    assert captured["api_key"] == "sk-global-claude"


# ---------------------------------------------------------------------------
# Model prefix coverage + error paths
# ---------------------------------------------------------------------------


def test_claude_prefix_returns_claude_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.ai.adapters.claude.AsyncAnthropic",
        lambda *, api_key, timeout=None: object(),
    )
    a = get_adapter_for_user("claude-haiku-4-5", {}, _settings())
    assert isinstance(a, ClaudeAdapter)


def test_deepseek_prefix_returns_deepseek_adapter() -> None:
    a = get_adapter_for_user("deepseek-coder", {}, _settings())
    assert isinstance(a, DeepSeekAdapter)


def test_ep_prefix_routes_to_doubao() -> None:
    a = get_adapter_for_user("ep-20240101-abcdef", {}, _settings())
    assert isinstance(a, DoubaoAdapter)


def test_unknown_model_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        get_adapter_for_user("some-unknown-provider-x", {}, _settings())


# ---------------------------------------------------------------------------
# V1: OpenAI family routes to OpenAIAdapter (multimodal)
# ---------------------------------------------------------------------------


def _settings_with_openai() -> Any:
    """Settings fixture with an OpenAI key set (other fields untouched)."""
    base = _settings()
    base.OPENAI_API_KEY = "sk-global-openai"
    base.OPENAI_MODEL = "gpt-4o"
    return base


def test_gpt_prefix_returns_openai_adapter_via_get_adapter() -> None:
    a = get_adapter("gpt-4o", _settings_with_openai())
    assert isinstance(a, OpenAIAdapter)
    # Points at the real OpenAI endpoint (base-class suffix normaliser
    # appends /chat/completions).
    assert a.api_url.endswith("/chat/completions")
    assert a.api_key == "sk-global-openai"


def test_o1_prefix_returns_openai_adapter_via_get_adapter() -> None:
    a = get_adapter("o1", _settings_with_openai())
    assert isinstance(a, OpenAIAdapter)


def test_user_byo_openai_key_takes_precedence() -> None:
    user_cfg = {"openai": {"api_key": "sk-user-personal"}}
    a = get_adapter_for_user("gpt-4o", user_cfg, _settings_with_openai())
    assert isinstance(a, OpenAIAdapter)
    assert a.api_key == "sk-user-personal"


def test_user_empty_falls_back_to_settings_openai() -> None:
    a = get_adapter_for_user("gpt-4o", {}, _settings_with_openai())
    assert isinstance(a, OpenAIAdapter)
    assert a.api_key == "sk-global-openai"
