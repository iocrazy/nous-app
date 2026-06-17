"""Tests for OpenAICompatibleAdapter — the base class shared by Qwen,
DeepSeek, and Doubao adapters (all OpenAI chat-completions compatible)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.openai_compat import OpenAICompatibleAdapter


def _make_composed(**overrides: Any) -> ComposedSystemPrompt:
    """Build a minimal ComposedSystemPrompt for testing."""
    defaults: dict[str, Any] = {
        "agent_id": uuid4(),
        "agent_slug": "test-agent",
        "system_message": "You are a helpful assistant.",
        "tools": [],
        "skill_manifest": [],
        "model": "test-model",
        "temperature": 0.7,
        "max_tokens": 1024,
        "cache_fingerprint": "abc123",
    }
    defaults.update(overrides)
    return ComposedSystemPrompt(**defaults)


def test_build_body_injects_system_then_user_messages() -> None:
    # default_model aligned with composed.model — the realistic factory case
    # (audit #8: a mismatch now self-heals to default_model, covered separately).
    adapter = OpenAICompatibleAdapter(
        api_url="https://example.com/v1/chat/completions",
        api_key="sk-test",
        default_model="test-model",
    )
    composed = _make_composed()
    messages = [{"role": "user", "content": "hi"}]

    body = adapter._build_body(composed, messages)

    assert body["model"] == "test-model"
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 1024
    assert body["messages"][0] == {
        "role": "system",
        "content": "You are a helpful assistant.",
    }
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert "tool_choice" not in body
    assert "tools" not in body


def test_build_body_injects_tools_and_tool_choice_auto() -> None:
    adapter = OpenAICompatibleAdapter(
        api_url="https://example.com/v1/chat/completions",
        api_key="sk-test",
    )
    tool_schema = {
        "type": "function",
        "function": {"name": "Skill", "parameters": {"type": "object"}},
    }
    composed = _make_composed(tools=[tool_schema])

    body = adapter._build_body(composed, [])

    assert body["tools"] == [tool_schema]
    assert body["tool_choice"] == "auto"


def test_build_body_falls_back_to_default_model_when_composed_empty() -> None:
    adapter = OpenAICompatibleAdapter(
        api_url="https://example.com",
        api_key="sk",
        default_model="fallback-model",
    )
    composed = _make_composed(model="")
    body = adapter._build_body(composed, [])
    assert body["model"] == "fallback-model"


def test_build_body_self_heals_on_model_mismatch() -> None:
    """Audit #8 fix C: adapter resolved for default_model must not send a
    different composed.model on the wire — self-heal to the resolved model."""
    adapter = OpenAICompatibleAdapter(
        api_url="https://doubao.example/v1/chat/completions",
        api_key="sk",
        default_model="doubao-seed-2",
    )
    composed = _make_composed(model="qwen-max")  # misrouted name

    with patch("app.services.ai.adapters._model_routing.inc_metric") as mock_metric:
        body = adapter._build_body(composed, [])

    assert body["model"] == "doubao-seed-2"  # resolved model wins, not composed
    mock_metric.assert_called_once_with("adapter_wire_model_mismatch")


def test_build_body_no_mismatch_signal_when_aligned() -> None:
    """No false-positive metric when composed.model == default_model."""
    adapter = OpenAICompatibleAdapter(
        api_url="https://example.com",
        api_key="sk",
        default_model="qwen-max",
    )
    composed = _make_composed(model="qwen-max")

    with patch("app.services.ai.adapters._model_routing.inc_metric") as mock_metric:
        body = adapter._build_body(composed, [])

    assert body["model"] == "qwen-max"
    mock_metric.assert_not_called()


def test_build_body_generic_adapter_honors_composed_model() -> None:
    """When default_model is empty (generic adapter), composed.model carries
    the routing decision and is honored without a mismatch signal."""
    adapter = OpenAICompatibleAdapter(
        api_url="https://example.com",
        api_key="sk",
        default_model="",
    )
    composed = _make_composed(model="qwen-plus")

    with patch("app.services.ai.adapters._model_routing.inc_metric") as mock_metric:
        body = adapter._build_body(composed, [])

    assert body["model"] == "qwen-plus"
    mock_metric.assert_not_called()


@pytest.mark.asyncio
async def test_call_posts_to_api_url_with_bearer_auth() -> None:
    adapter = OpenAICompatibleAdapter(
        api_url="https://provider.example/v1/chat/completions",
        api_key="sk-live-test",
    )
    composed = _make_composed()

    fake_response_data = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}]
    }

    with patch(
        "app.services.ai.adapters.openai_compat.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_instance = AsyncMock()
        fake_resp = type(
            "_Resp",
            (),
            {
                "json": lambda self: fake_response_data,
                "raise_for_status": lambda self: None,
            },
        )()
        mock_instance.post = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_instance

        result = await adapter.call(composed, [{"role": "user", "content": "hi"}])

    assert result == fake_response_data
    mock_instance.post.assert_awaited_once()
    kwargs = mock_instance.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer sk-live-test"
    assert kwargs["headers"]["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_call_omits_authorization_when_api_key_empty() -> None:
    adapter = OpenAICompatibleAdapter(
        api_url="https://provider.example/v1/chat/completions",
        api_key="",
    )
    composed = _make_composed()

    with patch(
        "app.services.ai.adapters.openai_compat.httpx.AsyncClient"
    ) as mock_client_cls:
        mock_instance = AsyncMock()
        fake_resp = type(
            "_Resp",
            (),
            {"json": lambda self: {}, "raise_for_status": lambda self: None},
        )()
        mock_instance.post = AsyncMock(return_value=fake_resp)
        mock_client_cls.return_value.__aenter__.return_value = mock_instance

        await adapter.call(composed, [])

    kwargs = mock_instance.post.call_args.kwargs
    assert "Authorization" not in kwargs["headers"]


# ─── subclass defaults ─────────────────────────────────────────────────


def test_qwen_adapter_has_dashscope_default_url() -> None:
    from app.services.ai.adapters import QwenAdapter

    a = QwenAdapter(api_key="test")
    assert "dashscope" in a.api_url
    assert a.default_model == "qwen-max"


def test_deepseek_adapter_has_deepseek_default_url() -> None:
    from app.services.ai.adapters import DeepSeekAdapter

    a = DeepSeekAdapter(api_key="test")
    assert "api.deepseek.com" in a.api_url
    assert a.default_model == "deepseek-chat"


def test_doubao_adapter_has_volces_default_url() -> None:
    from app.services.ai.adapters import DoubaoAdapter

    a = DoubaoAdapter(api_key="test")
    assert "volces.com" in a.api_url
