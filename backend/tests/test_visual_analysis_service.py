"""Tests for VisualAnalysisService — runs through AgentRunner + RunRecorder.

V1 (runner-multimodal) migrated analyze off its native OpenAI closure;
it now composes via PromptComposer and executes through AgentRunner
just like every other AI service. These tests validate:

- Result JSON parsing (including markdown-fence tolerance)
- Cost estimation helper (pure function, now takes two ints)
- L1 / L2 instruction routing
- Image content blocks make it into user_messages that the runner sees
- Agent slug constant matches the seeded DB row
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.visual.visual_analysis_service import (
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
    # (1000 * 0.0025 + 200 * 0.01) / 1000 = (2.5 + 2.0) / 1000 = 0.0045
    assert VisualAnalysisService._estimate_cost(1000, 200) == pytest.approx(0.0045)


def test_estimate_cost_zero_tokens() -> None:
    assert VisualAnalysisService._estimate_cost(0, 0) == 0.0


def test_extract_json_handles_markdown_fence() -> None:
    wrapped = '```json\n{"category":"Food"}\n```'
    assert VisualAnalysisService._extract_json(wrapped) == {"category": "Food"}


def test_extract_json_plain() -> None:
    assert VisualAnalysisService._extract_json('{"a":1}') == {"a": 1}


def test_extract_json_invalid_returns_empty() -> None:
    assert VisualAnalysisService._extract_json("not json at all") == {}


def test_extract_json_empty_returns_empty() -> None:
    assert VisualAnalysisService._extract_json("") == {}
    assert VisualAnalysisService._extract_json(None) == {}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_analyze_l1_routes_through_runner_with_l1_instruction() -> None:
    """Happy path: service composes via PromptComposer (L1 instruction),
    builds runner, passes a single image_url content block as the user
    turn. Returns a parsed VisualAnalysisResult.
    """
    svc = VisualAnalysisService()

    composed = MagicMock()
    composed.agent_id = "00000000-0000-0000-0000-000000000001"
    composed.agent_slug = "analyze"
    composed.model = "gpt-4o"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"category":"Food","visual_description":"A pizza.",'
            '"detected_objects":["pizza"],"detected_scenes":["indoor"],'
            '"detected_people":[],"detected_text":"","mood":"appetizing"}',
            "raw": {},
        }
    )

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="BASE64DATA")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
    ):
        # user_id=None → bare path (no RunRecorder wrap), cleanest to
        # assert on runner call shape.
        result = await svc.analyze_l1("https://example.com/cover.jpg")

    assert isinstance(result, VisualAnalysisResult)
    assert result.category == "Food"
    assert result.detected_objects == ["pizza"]

    # Composer got the L1 instruction.
    composer.compose.assert_awaited_once()
    composer_input = composer.compose.await_args.args[0]
    assert "L1" in composer_input.request_instructions
    assert "L2" not in composer_input.request_instructions

    # Runner got the image block as the user turn's content array.
    runner.run_turn.assert_awaited_once()
    kw = runner.run_turn.await_args.kwargs
    user_messages = kw["user_messages"]
    assert user_messages[0]["role"] == "user"
    content = user_messages[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert "BASE64DATA" in content[0]["image_url"]["url"]


@pytest.mark.asyncio
async def test_analyze_l2_sends_cover_plus_keyframes() -> None:
    """L2 path: cover + 3 keyframes → 4 image content blocks, L2 instruction."""
    svc = VisualAnalysisService()

    composed = MagicMock()
    composed.agent_id = "00000000-0000-0000-0000-000000000001"
    composed.model = "gpt-4o"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"category":"Travel","visual_description":"Hiking.",'
            '"detected_objects":["trail"],"detected_scenes":["outdoor"],'
            '"detected_people":[],"detected_text":"","mood":"adventurous",'
            '"content_summary":"Hiking trip through a forest."}',
            "raw": {},
        }
    )

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="COVER")
        ),
        patch.object(
            svc, "_encode_image_from_file", new=AsyncMock(return_value="FRAME")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
    ):
        result = await svc.analyze_l2(
            "https://example.com/cover.jpg",
            ["/tmp/f1.jpg", "/tmp/f2.jpg", "/tmp/f3.jpg"],
        )

    assert result is not None
    assert result.category == "Travel"

    # L2 instruction in composer call.
    composer_input = composer.compose.await_args.args[0]
    assert "L2" in composer_input.request_instructions

    # 4 image blocks (cover + 3 keyframes) in the user turn.
    kw = runner.run_turn.await_args.kwargs
    content = kw["user_messages"][0]["content"]
    assert len(content) == 4
    assert all(b["type"] == "image_url" for b in content)


@pytest.mark.asyncio
async def test_analyze_l1_returns_none_when_image_encode_fails() -> None:
    """No image → no LLM call, None returned gracefully."""
    svc = VisualAnalysisService()

    with patch.object(svc, "_encode_image_from_url", new=AsyncMock(return_value=None)):
        result = await svc.analyze_l1("https://example.com/cover.jpg")
    assert result is None


def test_agent_slug_constant() -> None:
    """Regression guard: agent slug must match the seeded DB row."""
    assert AGENT_SLUG == "analyze"
    assert VisualAnalysisService.AGENT_SLUG == "analyze"


def test_agent_slug_defaults_and_overrides() -> None:
    """The constructor's agent_slug becomes the instance slug (defaulting to
    'analyze'). This is what _run_multimodal composes — so the user's assigned
    visual-analysis agent (e.g. 'test-analyze') drives BOTH the prompt and the
    composed model, instead of the hardcoded 'analyze'."""
    assert VisualAnalysisService().AGENT_SLUG == "analyze"
    assert VisualAnalysisService(agent_slug="test-analyze").AGENT_SLUG == "test-analyze"
    # Blank/whitespace falls back to the default.
    assert VisualAnalysisService(agent_slug="  ").AGENT_SLUG == "analyze"


@pytest.mark.asyncio
async def test_run_composes_the_constructor_agent_slug() -> None:
    """Regression for the visual-analysis bug: a service built for
    'test-analyze' must compose 'test-analyze' (not the hardcoded 'analyze'),
    so composed.model is the assigned agent's model that matches the BYO
    provider config the caller passed."""
    svc = VisualAnalysisService(agent_slug="test-analyze")

    composed = MagicMock()
    composed.agent_id = "00000000-0000-0000-0000-000000000001"
    composed.agent_slug = "test-analyze"
    composed.model = "doubao-seed-2-0-pro-260215"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"category":"Food","visual_description":"x",'
            '"detected_objects":[],"detected_scenes":[],"detected_people":[],'
            '"detected_text":"","mood":""}',
            "raw": {},
        }
    )

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="BASE64DATA")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
    ):
        await svc.analyze_l1("https://example.com/cover.jpg")

    composer_input = composer.compose.await_args.args[0]
    assert composer_input.agent_slug == "test-analyze"


# ---------------------------------------------------------------------------
# V4: BYO provider config flows into the adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_l1_passes_byo_config_into_adapter() -> None:
    """End-to-end: L1 call with BYO config → the fallback chain is built
    from the resolved (composed) model and the user's BYO config, not from
    global settings.

    Post-fallback-chain wiring (spec 2026-08-11-batch-llm-fallback §4): the
    adapter is now built by ``build_fallback_llm`` rather than
    ``svc._build_adapter`` directly — the capture point moves to
    ``build_fallback_llm``'s ``primary_model`` / ``user_provider_config``
    kwargs.
    """
    svc = VisualAnalysisService(
        provider_key="doubao",
        provider_config={
            "api_key": "user-doubao-key",
            "model": "doubao-seed-2-0-pro-260215",
        },
    )

    composed = MagicMock()
    composed.agent_id = "00000000-0000-0000-0000-000000000001"
    composed.agent_slug = "analyze"
    composed.model = "doubao-seed-2-0-pro-260215"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": '{"category":"Other","visual_description":"","detected_objects":[]}',
            "raw": {},
        }
    )

    build_mock = AsyncMock(return_value=MagicMock())

    with (
        patch.object(
            svc, "_encode_image_from_url", new=AsyncMock(return_value="IMGDATA")
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.llm.fallback_wiring.build_fallback_llm",
            new=build_mock,
        ),
        patch(
            "app.services.ai.visual.visual_analysis_service.SkillToolService",
            return_value=MagicMock(),
        ),
    ):
        await svc.analyze_l1("https://example.com/cover.jpg")

    # build_fallback_llm was built for the resolved model from composed,
    # with the service's BYO provider config.
    build_mock.assert_awaited_once_with(
        primary_model="doubao-seed-2-0-pro-260215",
        fallback_models=[],
        user_provider_config=svc._provider_config,
        provider_key=svc._provider_key,
        module="visual_analysis",
    )
