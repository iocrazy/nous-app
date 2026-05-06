"""Tests for get_adapter() — dispatches by model prefix to the right adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ai.adapters.claude import ClaudeAdapter
from app.services.ai.adapters.deepseek import DeepSeekAdapter
from app.services.ai.adapters.doubao import DoubaoAdapter
from app.services.ai.adapters.factory import get_adapter
from app.services.ai.adapters.openai import OpenAIAdapter
from app.services.ai.adapters.qwen import QwenAdapter


def _settings(**overrides) -> SimpleNamespace:
    """Minimal settings stub matching the keys get_adapter reads."""
    defaults = {
        "LLM_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "LLM_API_KEY": "sk-qwen",
        "LLM_MODEL": "qwen-max",
        "DEEPSEEK_API_URL": "https://api.deepseek.com/v1/chat/completions",
        "DEEPSEEK_API_KEY": "sk-deepseek",
        "DOUBAO_API_URL": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "DOUBAO_API_KEY": "sk-doubao",
        "CLAUDE_API_KEY": "sk-ant",
        "OPENAI_API_KEY": "sk-openai",
        "OPENAI_MODEL": "gpt-4o",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_qwen_prefix_returns_qwen_adapter() -> None:
    a = get_adapter("qwen-max", _settings())
    assert isinstance(a, QwenAdapter)
    assert a.api_key == "sk-qwen"


def test_tongyi_prefix_also_routes_to_qwen() -> None:
    a = get_adapter("tongyi-plus", _settings())
    assert isinstance(a, QwenAdapter)


def test_deepseek_prefix_returns_deepseek_adapter() -> None:
    a = get_adapter("deepseek-chat", _settings())
    assert isinstance(a, DeepSeekAdapter)
    assert a.api_key == "sk-deepseek"


def test_doubao_prefix_returns_doubao_adapter() -> None:
    a = get_adapter("doubao-pro-32k", _settings())
    assert isinstance(a, DoubaoAdapter)


def test_ep_prefix_also_routes_to_doubao() -> None:
    """Volcengine endpoint IDs look like 'ep-20240101-abcdef'."""
    a = get_adapter("ep-20240101-abcdef", _settings())
    assert isinstance(a, DoubaoAdapter)


def test_claude_prefix_returns_claude_adapter() -> None:
    a = get_adapter("claude-opus-4-5", _settings())
    assert isinstance(a, ClaudeAdapter)


def test_empty_model_defaults_to_qwen() -> None:
    a = get_adapter("", _settings())
    assert isinstance(a, QwenAdapter)


def test_gpt_prefix_returns_openai_adapter() -> None:
    """V1 (runner-multimodal): native OpenAI family routes to OpenAIAdapter."""
    a = get_adapter("gpt-4o", _settings())
    assert isinstance(a, OpenAIAdapter)
    assert a.api_key == "sk-openai"


def test_o1_prefix_routes_to_openai() -> None:
    a = get_adapter("o1", _settings())
    assert isinstance(a, OpenAIAdapter)


def test_o3_prefix_routes_to_openai() -> None:
    a = get_adapter("o3-mini", _settings())
    assert isinstance(a, OpenAIAdapter)


def test_unknown_prefix_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        get_adapter("totally-fictional-provider-x", _settings())
