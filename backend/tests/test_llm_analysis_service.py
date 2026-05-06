"""Tests for the LLMAnalysisService migration to the agent framework.

Phase 2 PR 2.4 moved summarize prompts from hardcoded strings into the
DB-driven agent framework (PromptComposer + AgentRunner). These tests
verify the service still produces SummaryResult output and that per-user
provider config is used to build the adapter instead of global settings.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.llm.llm_analysis_service import (
    AGENT_SLUG,
    LLMAnalysisService,
    SummaryResult,
    _build_adapter_from_provider_config,
    _build_language_directive,
)


def test_build_language_directive_known_codes() -> None:
    assert _build_language_directive("auto") == ""
    assert "English" in _build_language_directive("en")
    assert "中文" in _build_language_directive("zh")
    # Unknown code → empty (same behavior as Phase 1 hardcoded version).
    assert _build_language_directive("kv") == ""


def test_build_adapter_from_provider_config_plumbs_keys_and_url() -> None:
    adapter = _build_adapter_from_provider_config(
        "openai",
        {"api_key": "sk-test", "base_url": "http://host/v1", "model": "gpt-4o-mini"},
    )
    # OpenAICompatibleAdapter auto-appends /chat/completions to the base URL.
    assert adapter.api_url == "http://host/v1/chat/completions"
    assert adapter.api_key == "sk-test"
    assert adapter.default_model == "gpt-4o-mini"


def test_parse_summary_response_valid_json() -> None:
    svc = LLMAnalysisService()
    result = svc._parse_summary_response(
        '{"summary":"s","key_points":["a","b"],"topics":["t1"]}'
    )
    assert result.summary == "s"
    assert result.key_points == ["a", "b"]
    assert result.topics == ["t1"]


def test_parse_summary_response_strips_markdown_fence() -> None:
    svc = LLMAnalysisService()
    result = svc._parse_summary_response(
        '```json\n{"summary":"s","key_points":[],"topics":[]}\n```'
    )
    assert result.summary == "s"


def test_parse_summary_response_json_error_falls_back_to_raw() -> None:
    svc = LLMAnalysisService()
    result = svc._parse_summary_response("not a json at all")
    assert result.summary == "not a json at all"
    assert result.key_points == []
    assert result.topics == []


@pytest.mark.asyncio
async def test_generate_summary_composes_from_agent_and_parses_json() -> None:
    """Happy path: service calls composer → runner, parses JSON into SummaryResult.

    Mocks both layers so no DB / network is touched. Verifies that the
    composer is called with agent_slug='summarize' and that the runner's
    output flows through _parse_summary_response.
    """
    svc = LLMAnalysisService(
        provider_key="openai",
        provider_config={
            "api_key": "sk-test",
            "base_url": "http://host/v1",
            "model": "gpt-4o-mini",
        },
    )

    fake_composed = AsyncMock()
    fake_composed.model = "gpt-4o-mini"
    fake_composed.model_copy = lambda update=None: fake_composed

    composer_mock = AsyncMock()
    composer_mock.compose = AsyncMock(return_value=fake_composed)

    runner_mock = AsyncMock()
    runner_mock.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"cats","key_points":["k1"],"topics":["t1"]}',
            "raw": {},
        }
    )

    with (
        patch.object(svc, "_build_composer", return_value=composer_mock),
        patch.object(svc, "_build_runner", return_value=runner_mock),
    ):
        result = await svc.generate_summary(
            transcript_text="hello world",
            video_info={"title": "Cats", "author": "Alice"},
            model="gpt-4o-mini",
            language="en",
        )

    assert isinstance(result, SummaryResult)
    assert result.summary == "cats"
    assert result.key_points == ["k1"]
    assert result.topics == ["t1"]

    # Composer was called with the right agent slug
    composer_mock.compose.assert_awaited_once()
    composer_call = composer_mock.compose.await_args.args[0]
    assert composer_call.agent_slug == AGENT_SLUG
    # Language directive + video metadata are in the request_instructions
    assert "English" in composer_call.request_instructions
    assert "Cats" in composer_call.request_instructions
    assert "Alice" in composer_call.request_instructions

    # Runner was invoked with the transcript as user content
    runner_mock.run_turn.assert_awaited_once()
    run_kwargs = runner_mock.run_turn.await_args.kwargs
    user_msgs: List[Dict[str, Any]] = run_kwargs["user_messages"]
    assert user_msgs[0]["role"] == "user"
    assert "hello world" in user_msgs[0]["content"]


@pytest.mark.asyncio
async def test_generate_summary_no_language_directive_when_auto() -> None:
    """language='auto' → no language directive injected into request_instructions."""
    svc = LLMAnalysisService()

    fake_composed = AsyncMock()
    fake_composed.model = ""
    fake_composed.model_copy = lambda update=None: fake_composed

    composer_mock = AsyncMock()
    composer_mock.compose = AsyncMock(return_value=fake_composed)

    runner_mock = AsyncMock()
    runner_mock.run_turn = AsyncMock(
        return_value={
            "content": '{"summary":"s","key_points":[],"topics":[]}',
            "raw": {},
        }
    )

    with (
        patch.object(svc, "_build_composer", return_value=composer_mock),
        patch.object(svc, "_build_runner", return_value=runner_mock),
    ):
        await svc.generate_summary(transcript_text="t", language="auto")

    composer_call = composer_mock.compose.await_args.args[0]
    # Auto → no English/中文/etc directive string
    assert "English" not in composer_call.request_instructions
    assert "中文" not in composer_call.request_instructions
