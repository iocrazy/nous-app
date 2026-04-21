"""Tests for ClaudeAdapter — converts Anthropic messages API output
into the OpenAI tool_calls shape AgentRunner expects."""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai_adapters.claude import ClaudeAdapter


def _make_composed(**overrides: Any) -> ComposedSystemPrompt:
    defaults = {
        "system_message": "You are helpful.",
        "tools": [],
        "model": "claude-opus-4-5",
        "temperature": 0.7,
        "max_tokens": 1024,
        "cache_fingerprint": "fp-xyz",
        "agent_id": uuid4(),
        "agent_slug": "test-agent",
        "skill_manifest": [],
    }
    defaults.update(overrides)
    return ComposedSystemPrompt(**defaults)


def test_convert_tools_from_openai_to_anthropic_shape() -> None:
    adapter = ClaudeAdapter(api_key="sk-ant-test")
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "Skill",
                "description": "Read a skill",
                "parameters": {
                    "type": "object",
                    "properties": {"skill": {"type": "string"}},
                    "required": ["skill"],
                },
            },
        }
    ]
    anthropic_tools = adapter._convert_tools(openai_tools)
    assert len(anthropic_tools) == 1
    assert anthropic_tools[0]["name"] == "Skill"
    assert anthropic_tools[0]["description"] == "Read a skill"
    assert anthropic_tools[0]["input_schema"]["required"] == ["skill"]


def test_normalize_response_text_only() -> None:
    """Anthropic content with only text blocks → OpenAI-shape with content string."""
    adapter = ClaudeAdapter(api_key="sk-ant-test")

    anthropic_resp = MagicMock()
    anthropic_resp.content = [
        MagicMock(type="text", text="Here is the answer."),
    ]
    anthropic_resp.stop_reason = "end_turn"

    openai_shape = adapter._normalize_response(anthropic_resp)

    msg = openai_shape["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Here is the answer."
    assert "tool_calls" not in msg
    assert openai_shape["choices"][0]["finish_reason"] == "stop"


def test_normalize_response_with_tool_use() -> None:
    """Anthropic tool_use block → OpenAI tool_calls array with stringified args."""
    adapter = ClaudeAdapter(api_key="sk-ant-test")

    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.id = "toolu_01XYZ"
    tool_use.name = "Skill"
    tool_use.input = {"skill": "script-outline", "file": "references/examples.md"}

    text_block = MagicMock(type="text", text="Let me look that up.")

    anthropic_resp = MagicMock()
    anthropic_resp.content = [text_block, tool_use]
    anthropic_resp.stop_reason = "tool_use"

    openai_shape = adapter._normalize_response(anthropic_resp)

    msg = openai_shape["choices"][0]["message"]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Let me look that up."
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "toolu_01XYZ"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "Skill"
    # Arguments must be a JSON STRING (OpenAI contract), not a dict
    assert isinstance(tc["function"]["arguments"], str)
    assert _json.loads(tc["function"]["arguments"]) == {
        "skill": "script-outline",
        "file": "references/examples.md",
    }
    assert openai_shape["choices"][0]["finish_reason"] == "tool_calls"


def test_convert_messages_round_trip() -> None:
    """Round-trip: OpenAI user+assistant-with-tool_calls+tool response →
    Anthropic user + assistant-with-tool_use-block + user-with-tool_result-block."""
    adapter = ClaudeAdapter(api_key="sk-ant-test")

    openai_messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_01",
                    "type": "function",
                    "function": {"name": "Skill", "arguments": '{"skill": "x"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_01",
            "name": "Skill",
            "content": '{"prompt": "body"}',
        },
    ]

    anthropic_messages = adapter._convert_messages(openai_messages)

    assert len(anthropic_messages) == 3
    assert anthropic_messages[0] == {"role": "user", "content": "hi"}

    assistant_msg = anthropic_messages[1]
    assert assistant_msg["role"] == "assistant"
    tool_use_block = next(
        b for b in assistant_msg["content"] if b["type"] == "tool_use"
    )
    assert tool_use_block["id"] == "toolu_01"
    assert tool_use_block["name"] == "Skill"
    assert tool_use_block["input"] == {"skill": "x"}

    tool_result_msg = anthropic_messages[2]
    assert tool_result_msg["role"] == "user"
    result_block = tool_result_msg["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "toolu_01"
    assert result_block["content"] == '{"prompt": "body"}'


@pytest.mark.asyncio
async def test_call_invokes_messages_create_with_normalized_output() -> None:
    """Integration-ish: adapter.call() invokes SDK and returns OpenAI shape."""
    adapter = ClaudeAdapter(api_key="sk-ant-test")
    composed = _make_composed()

    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="hello back")]
    fake_response.stop_reason = "end_turn"

    with patch.object(adapter, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=fake_response)
        result = await adapter.call(composed, [{"role": "user", "content": "hi"}])

    assert result["choices"][0]["message"]["content"] == "hello back"
    mock_client.messages.create.assert_awaited_once()
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-4-5"
    assert kwargs["max_tokens"] == 1024
    assert kwargs["system"] == "You are helpful."
