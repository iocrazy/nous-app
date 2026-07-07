"""Tests for get_adapter() — the deprecated shim over get_adapter_for_user.

2026-07-07 (铁律): credentials are DB-ONLY. ``get_adapter(model, settings)``
IGNORES ``settings`` and delegates with no user config, so every prefix now
fails fast with ProviderNotConfiguredError instead of reading env fields.
Prefix→adapter routing itself is covered via get_adapter_for_user with an
explicit credential dict (and in tests/test_adapter_factory_byo.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.factory import (
    ProviderNotConfiguredError,
    get_adapter,
    get_adapter_for_user,
)
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter


def _env_settings(**overrides) -> SimpleNamespace:
    """Settings stub carrying the RETIRED env credential fields — get_adapter
    must ignore every one of them."""
    defaults = {
        "LLM_API_URL": "https://env-leak.example/v1",
        "LLM_API_KEY": "sk-qwen",
        "LLM_MODEL": "qwen-max",
        "DEEPSEEK_API_URL": "https://env-leak.example/deepseek",
        "DEEPSEEK_API_KEY": "sk-deepseek",
        "DOUBAO_API_URL": "https://env-leak.example/doubao",
        "DOUBAO_API_KEY": "sk-doubao",
        "CLAUDE_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-openai",
        "OPENAI_MODEL": "gpt-4o",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.parametrize(
    "model",
    [
        "qwen-max",
        "tongyi-plus",
        "",
        "deepseek-chat",
        "doubao-pro-32k",
        "ep-20240101-abcdef",
        "claude-opus-4-5",
        "gpt-4o",
        "o1",
        "o3-mini",
    ],
)
def test_shim_never_reads_env_settings(model: str) -> None:
    with pytest.raises(ProviderNotConfiguredError):
        get_adapter(model, _env_settings())


def test_unknown_prefix_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        get_adapter("totally-fictional-provider-x", _env_settings())


# Routing sanity — the shim delegates to get_adapter_for_user, so prefix
# routing with real credentials still lands on the right adapter class.


def test_routing_qwen() -> None:
    a = get_adapter_for_user(
        "qwen-max", {"qwen": {"api_key": "k", "base_url": "https://q/v1"}}, None
    )
    assert isinstance(a, QwenAdapter)


def test_routing_deepseek() -> None:
    a = get_adapter_for_user("deepseek-chat", {"deepseek": {"api_key": "k"}}, None)
    assert isinstance(a, DeepSeekAdapter)


def test_routing_doubao_ep() -> None:
    a = get_adapter_for_user("ep-20240101-abcdef", {"doubao": {"api_key": "k"}}, None)
    assert isinstance(a, DoubaoAdapter)


def test_routing_openai() -> None:
    a = get_adapter_for_user("gpt-4o", {"openai": {"api_key": "k"}}, None)
    assert isinstance(a, OpenAIAdapter)
