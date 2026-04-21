"""Tests for VisualAnalysisService — hybrid DB-prompt + native OpenAI path.

Phase 2 PR 2.5 moved the L1/L2 prompt text out of module-level constants
into the ``analyze`` ai_agents row (fetched via PromptComposer). The
multimodal OpenAI call stays direct because AgentRunner lacks image-
content support. These tests cover the composer wiring, result parsing,
and the disabled-client path — no network, no DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.visual_analysis_service import (
    AGENT_SLUG,
    VisualAnalysisResult,
    VisualAnalysisService,
)


def test_result_from_json_populates_all_fields() -> None:
    data = {
        "category": "Tech",
        "visual_description": "A keyboard on a desk.",
        "detected_objects": ["keyboard", "mouse"],
        "detected_scenes": ["indoor"],
        "detected_people": [{"gender": "n/a", "clothing": "", "action": ""}],
        "detected_text": "SHIFT",
        "mood": "focused",
    }
    result = VisualAnalysisService._result_from_json(data, cost=0.0042)
    assert isinstance(result, VisualAnalysisResult)
    assert result.category == "Tech"
    assert result.visual_description == "A keyboard on a desk."
    assert result.detected_objects == ["keyboard", "mouse"]
    assert result.cost == 0.0042


def test_result_from_json_defaults_missing_fields() -> None:
    result = VisualAnalysisService._result_from_json({}, cost=0.0)
    assert result.category == "Other"
    assert result.visual_description == ""
    assert result.detected_objects == []
    assert result.detected_people == []


def test_estimate_cost_gpt4o_pricing() -> None:
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
    # (1000 * 0.0025 + 200 * 0.01) / 1000 = (2.5 + 2.0) / 1000 = 0.0045
    assert VisualAnalysisService._estimate_cost(usage) == pytest.approx(0.0045)


def test_estimate_cost_missing_usage_fields() -> None:
    usage = SimpleNamespace()
    assert VisualAnalysisService._estimate_cost(usage) == 0.0


@pytest.mark.asyncio
async def test_analyze_l1_returns_none_without_api_key() -> None:
    """Without OPENAI_API_KEY the client is None and L1 returns None gracefully."""
    with patch.dict("os.environ", {"OPENAI_API_KEY": ""}, clear=False):
        svc = VisualAnalysisService()
        svc.client = None  # explicit belt-and-braces for the test environment

    result = await svc.analyze_l1("https://example.com/cover.jpg")
    assert result is None


@pytest.mark.asyncio
async def test_analyze_l1_composes_from_agent_and_parses_json() -> None:
    """Happy path: service composes system prompt from 'analyze' agent,
    sends image + system message to OpenAI, parses JSON response."""
    svc = VisualAnalysisService()
    svc.client = AsyncMock()

    # Mock the OpenAI chat completion response
    fake_message = SimpleNamespace(
        content='{"category":"Food","visual_description":"A pizza.",'
        '"detected_objects":["pizza"],"detected_scenes":["indoor"],'
        '"detected_people":[],"detected_text":"","mood":"appetizing"}'
    )
    fake_choice = SimpleNamespace(message=fake_message)
    fake_usage = SimpleNamespace(prompt_tokens=500, completion_tokens=100)
    fake_response = SimpleNamespace(choices=[fake_choice], usage=fake_usage)
    svc.client.chat = SimpleNamespace(
        completions=SimpleNamespace(create=AsyncMock(return_value=fake_response))
    )

    # Patch image encoding + prompt composition
    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="BASE64DATA")
        ),
        patch.object(
            svc,
            "_compose_system_prompt",
            new=AsyncMock(return_value="SYSTEM PROMPT FROM AGENT"),
        ) as mock_compose,
    ):
        result = await svc.analyze_l1("https://example.com/cover.jpg")

    assert isinstance(result, VisualAnalysisResult)
    assert result.category == "Food"
    assert result.visual_description == "A pizza."
    assert result.detected_objects == ["pizza"]
    assert result.mood == "appetizing"
    assert result.cost > 0  # Cost was calculated from usage

    # Composer was invoked with the L1 instruction (not L2)
    mock_compose.assert_awaited_once()
    instruction = mock_compose.await_args.args[0]
    assert "L1" in instruction
    assert "L2" not in instruction

    # OpenAI was called with system+user messages (system from agent, user with image)
    svc.client.chat.completions.create.assert_awaited_once()
    call_kwargs = svc.client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "SYSTEM PROMPT FROM AGENT"
    assert messages[1]["role"] == "user"
    # User content is a list of content blocks; first should be the image
    assert isinstance(messages[1]["content"], list)
    assert messages[1]["content"][0]["type"] == "image_url"
    assert call_kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_analyze_l2_sends_multiple_images_and_uses_l2_instruction() -> None:
    """L2 path: cover + 3 keyframes → 4 image content blocks, L2 instruction."""
    svc = VisualAnalysisService()
    svc.client = AsyncMock()

    fake_message = SimpleNamespace(
        content='{"category":"Travel","visual_description":"Hiking.",'
        '"detected_objects":["trail"],"detected_scenes":["outdoor"],'
        '"detected_people":[],"detected_text":"","mood":"adventurous",'
        '"content_summary":"Hiking trip through a forest."}'
    )
    fake_choice = SimpleNamespace(message=fake_message)
    fake_usage = SimpleNamespace(prompt_tokens=2000, completion_tokens=300)
    fake_response = SimpleNamespace(choices=[fake_choice], usage=fake_usage)
    svc.client.chat = SimpleNamespace(
        completions=SimpleNamespace(create=AsyncMock(return_value=fake_response))
    )

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="COVER")
        ),
        patch.object(
            svc, "_encode_image_from_file", new=AsyncMock(return_value="FRAME")
        ),
        patch.object(
            svc,
            "_compose_system_prompt",
            new=AsyncMock(return_value="SYSTEM L2"),
        ) as mock_compose,
    ):
        result = await svc.analyze_l2(
            "https://example.com/cover.jpg",
            ["/tmp/f1.jpg", "/tmp/f2.jpg", "/tmp/f3.jpg"],
        )

    assert result is not None
    assert result.category == "Travel"

    # L2 instruction in composer call
    instruction = mock_compose.await_args.args[0]
    assert "L2" in instruction

    # 4 image blocks (cover + 3 keyframes)
    call_kwargs = svc.client.chat.completions.create.await_args.kwargs
    user_content = call_kwargs["messages"][1]["content"]
    assert len(user_content) == 4
    assert all(b["type"] == "image_url" for b in user_content)


def test_agent_slug_constant() -> None:
    """Regression guard: agent slug must match the seeded DB row."""
    assert AGENT_SLUG == "analyze"
    assert VisualAnalysisService.AGENT_SLUG == "analyze"
