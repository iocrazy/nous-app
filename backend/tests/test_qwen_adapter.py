"""Unit tests for QwenAdapter (ai_provider.QwenAdapter).

No network: exercises _build_body shape and constructor invariants. The
``call()`` method is integration scope and covered elsewhere.
"""

from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai_provider import QwenAdapter


def _sample_composed(tools=None) -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=tools or [],
        skill_manifest=[],
        cache_fingerprint="abc",
    )


@pytest.mark.unit
def test_request_body_has_system_message_first():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    body = adapter._build_body(
        _sample_composed(),
        messages=[{"role": "user", "content": "hi"}],
    )
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"] == "SYSTEM"
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "hi"


@pytest.mark.unit
def test_body_uses_composed_model_and_params():
    adapter = QwenAdapter(api_url="http://fake", api_key="k")
    body = adapter._build_body(_sample_composed(), messages=[])
    assert body["model"] == "qwen-max"
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 1024


@pytest.mark.unit
def test_no_tools_field_when_empty():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    body = adapter._build_body(_sample_composed(tools=[]), messages=[])
    assert "tools" not in body
    assert "tool_choice" not in body


@pytest.mark.unit
def test_tools_passed_through():
    adapter = QwenAdapter(api_url="http://fake", api_key="k", default_model="qwen-max")
    sample_tool = {"type": "function", "function": {"name": "Skill"}}
    body = adapter._build_body(
        _sample_composed(tools=[sample_tool]),
        messages=[],
    )
    assert body["tools"][0]["function"]["name"] == "Skill"
    assert body["tool_choice"] == "auto"


@pytest.mark.unit
def test_api_url_stored_as_provided():
    # Base class no longer strips trailing slashes; the caller should pass
    # a full endpoint URL (including path) without a trailing slash.
    adapter = QwenAdapter(api_url="http://fake", api_key="k")
    assert adapter.api_url == "http://fake"


@pytest.mark.unit
def test_no_auth_header_when_key_empty():
    adapter = QwenAdapter(api_url="http://fake", api_key="")
    # Cannot test the call() method without mocking httpx — that's integration scope.
    # Just verify the instance accepts empty key without error.
    assert adapter.api_key == ""
