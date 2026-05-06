"""Tests for the Phase 2 PR 2.6 migration of StoryboardAIService to the
agent framework.

Covers the 3 text-LLM methods that moved from hardcoded system prompts to
DB-composed prompts: split_script, analyze_video, chat. Each test mocks
the composer + the _call_llm layer so no DB or network is touched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.storyboard.storyboard_ai_service import AGENT_SLUG, StoryboardAIService


def test_agent_slug_constant() -> None:
    assert AGENT_SLUG == "storyboard"
    assert StoryboardAIService.AGENT_SLUG == "storyboard"


@pytest.mark.asyncio
async def test_split_script_instruction_selects_mode_a_with_style_guide() -> None:
    """split_script composes a Mode A instruction and threads the style guide."""
    svc = StoryboardAIService()

    with (
        patch.object(
            svc,
            "_compose_system_prompt",
            new=AsyncMock(return_value="SYSTEM STORYBOARD"),
        ) as mock_compose,
        patch.object(
            svc,
            "_call_llm",
            new=AsyncMock(return_value='[{"scene_number":1,"description":"x"}]'),
        ) as mock_call,
    ):
        result = await svc.split_script(
            "FADE IN. INT. COFFEE SHOP - DAY.", style_guide="film noir"
        )

    assert isinstance(result, list)
    assert result[0]["scene_number"] == 1

    # Composer was called with a Mode A instruction that included the style guide
    mock_compose.assert_awaited_once()
    instruction = mock_compose.await_args.args[0]
    assert "Mode A" in instruction
    assert "split_script" in instruction
    assert "film noir" in instruction

    # LLM was called with the composed system prompt + user script content
    mock_call.assert_awaited_once()
    messages = mock_call.await_args.args[0]
    assert messages[0] == {"role": "system", "content": "SYSTEM STORYBOARD"}
    assert "COFFEE SHOP" in messages[1]["content"]


@pytest.mark.asyncio
async def test_split_script_without_style_guide_omits_style_line() -> None:
    svc = StoryboardAIService()
    with (
        patch.object(
            svc, "_compose_system_prompt", new=AsyncMock(return_value="SYS")
        ) as mock_compose,
        patch.object(svc, "_call_llm", new=AsyncMock(return_value="[]")),
    ):
        await svc.split_script("some script")

    instruction = mock_compose.await_args.args[0]
    assert "Mode A" in instruction
    assert "style guide" not in instruction.lower()


@pytest.mark.asyncio
async def test_analyze_video_composes_mode_b_once_for_all_keyframes() -> None:
    """analyze_video should compose the Mode B system prompt ONCE and reuse
    it across all keyframes — not N composer round-trips for N keyframes."""
    svc = StoryboardAIService()

    fake_keyframes = [
        {"time": 1.0, "image_path": "/frame1.jpg"},
        {"time": 2.5, "image_path": "/frame2.jpg"},
        {"time": 4.0, "image_path": "/frame3.jpg"},
    ]

    with patch(
        "app.services.storyboard.storyboard_image_service.StoryboardImageService"
    ) as mock_img_svc_cls:
        mock_img_svc_cls.return_value.detect_scenes = lambda _: fake_keyframes

        with (
            patch.object(
                svc, "_compose_system_prompt", new=AsyncMock(return_value="SYS_B")
            ) as mock_compose,
            patch.object(
                svc,
                "_call_llm",
                new=AsyncMock(
                    return_value='{"shot_type":"wide","camera_angle":"eye-level",'
                    '"movement":"static","suggested_prompt":"p"}'
                ),
            ) as mock_call,
        ):
            result = await svc.analyze_video("/fake.mp4")

    assert "keyframes" in result
    assert len(result["keyframes"]) == 3

    # Composer: exactly once, with Mode B instruction
    mock_compose.assert_awaited_once()
    instruction = mock_compose.await_args.args[0]
    assert "Mode B" in instruction
    assert "annotate_keyframe" in instruction

    # LLM: called once per keyframe
    assert mock_call.await_count == 3


# StoryboardAIService.chat() was removed in the AIChatDrawer migration —
# storyboard chat now flows through AILibraryChatService + AgentRunner
# (covered by tests/test_ai_library_chat.py). The two chat tests that
# used to live here verified the now-deleted Mode C instruction wiring.
