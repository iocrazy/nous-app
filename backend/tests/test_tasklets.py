"""Tests for the Tasklet framework + builtins.

Tests focus on the framework contract (return type, error handling, schema
validation) — they don't hit a real LLM. The adapter is mocked with
``unittest.mock.AsyncMock``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.tasklets.base import (
    Tasklet,
    TaskletError,
    TaskletResult,
    parse_json_response,
)
from app.services.ai.tasklets.builtins import (
    BUILTIN_TASKLETS,
    intent_classifier,
    title_generator,
)


# ============================================================================
# parse_json_response — robust JSON extraction
# ============================================================================


class TestParseJsonResponse:
    def test_plain_json_dict(self):
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_plain_json_list(self):
        assert parse_json_response("[1, 2, 3]") == [1, 2, 3]

    def test_with_surrounding_whitespace(self):
        assert parse_json_response('  \n{"x": "y"}\n  ') == {"x": "y"}

    def test_markdown_fenced_json(self):
        text = '```json\n{"k": "v"}\n```'
        assert parse_json_response(text) == {"k": "v"}

    def test_markdown_fenced_no_lang_tag(self):
        text = '```\n{"k": "v"}\n```'
        assert parse_json_response(text) == {"k": "v"}

    def test_markdown_uppercase_json_tag(self):
        text = '```JSON\n{"k": "v"}\n```'
        assert parse_json_response(text) == {"k": "v"}

    def test_malformed_returns_none(self):
        assert parse_json_response("not json at all") is None

    def test_empty_returns_none(self):
        assert parse_json_response("") is None
        assert parse_json_response("   ") is None


# ============================================================================
# Tasklet.run — happy paths + each error branch
# ============================================================================


def _mock_adapter_returning(content: str) -> MagicMock:
    """Build an adapter mock whose .call returns the given string content."""
    adapter = MagicMock()
    adapter.call = AsyncMock(
        return_value={"choices": [{"message": {"content": content}}]}
    )
    return adapter


@pytest.mark.asyncio
async def test_tasklet_raw_text_mode():
    """No schema → returns stripped raw text."""
    t = Tasklet(
        slug="echo",
        system_prompt="You echo.",
    )
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning("  hello  "),
    ):
        result = await t.run("hi", settings=MagicMock())

    assert result.ok is True
    assert result.value == "hello"
    assert result.error is None


@pytest.mark.asyncio
async def test_tasklet_json_mode_happy():
    """Schema set + LLM returns matching JSON → ok=True, value=parsed."""
    t = Tasklet(
        slug="echo_json",
        system_prompt="Return JSON.",
        output_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
    )
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning('{"name": "alice"}'),
    ):
        result = await t.run("who?", settings=MagicMock())

    assert result.ok is True
    assert result.value == {"name": "alice"}


@pytest.mark.asyncio
async def test_tasklet_json_mode_malformed():
    """Schema set + LLM returns non-JSON → ok=False, error='malformed_json'."""
    t = Tasklet(
        slug="bad",
        system_prompt="Return JSON.",
        output_schema={"type": "object", "required": ["x"]},
    )
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning("not json"),
    ):
        result = await t.run("?", settings=MagicMock())

    assert result.ok is False
    assert result.error == "malformed_json"
    assert result.raw is not None


@pytest.mark.asyncio
async def test_tasklet_json_mode_schema_mismatch():
    """Schema set + LLM returns JSON missing required field → schema_mismatch."""
    t = Tasklet(
        slug="missing_required",
        system_prompt="Return JSON.",
        output_schema={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        },
    )
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning('{"age": 30}'),
    ):
        result = await t.run("?", settings=MagicMock())

    assert result.ok is False
    assert result.error == "schema_mismatch"
    assert result.value == {"age": 30}  # caller can still inspect


@pytest.mark.asyncio
async def test_tasklet_json_mode_enum_violation():
    """Enum constraint violated → schema_mismatch."""
    t = Tasklet(
        slug="enum_check",
        system_prompt="Return JSON.",
        output_schema={
            "type": "object",
            "required": ["color"],
            "properties": {"color": {"type": "string", "enum": ["red", "green"]}},
        },
    )
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning('{"color": "purple"}'),
    ):
        result = await t.run("?", settings=MagicMock())

    assert result.ok is False
    assert result.error == "schema_mismatch"


@pytest.mark.asyncio
async def test_tasklet_llm_exception_is_swallowed():
    """LLM throws → ok=False, error='llm_call_failed: ...'."""
    t = Tasklet(slug="boom", system_prompt="anything")
    boom_adapter = MagicMock()
    boom_adapter.call = AsyncMock(side_effect=RuntimeError("connection refused"))
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=boom_adapter,
    ):
        result = await t.run("?", settings=MagicMock())

    assert result.ok is False
    assert result.error is not None
    assert "llm_call_failed" in result.error


@pytest.mark.asyncio
async def test_tasklet_bad_response_shape():
    """Adapter returns wrong shape → ok=False, error='bad_response_shape'."""
    t = Tasklet(slug="shape", system_prompt="x")
    weird_adapter = MagicMock()
    weird_adapter.call = AsyncMock(return_value={"unexpected": "shape"})
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=weird_adapter,
    ):
        result = await t.run("?", settings=MagicMock())

    assert result.ok is False
    assert result.error == "bad_response_shape"


@pytest.mark.asyncio
async def test_run_or_raise_happy():
    t = Tasklet(slug="ok", system_prompt="x")
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=_mock_adapter_returning("yes"),
    ):
        value = await t.run_or_raise("?", settings=MagicMock())
    assert value == "yes"


@pytest.mark.asyncio
async def test_run_or_raise_propagates():
    t = Tasklet(slug="bad", system_prompt="x")
    boom_adapter = MagicMock()
    boom_adapter.call = AsyncMock(side_effect=RuntimeError("nope"))
    with patch(
        "app.services.ai.adapters.factory.get_adapter",
        return_value=boom_adapter,
    ):
        with pytest.raises(TaskletError) as excinfo:
            await t.run_or_raise("?", settings=MagicMock())
    assert excinfo.value.tasklet_slug == "bad"


# ============================================================================
# Cache fingerprint contract
# ============================================================================


def test_cache_fingerprint_includes_slug_and_version():
    t = Tasklet(slug="title", system_prompt="x", cache_fingerprint_suffix="v3")
    assert t.cache_fingerprint == "tasklet_title_v3"


def test_cache_fingerprint_default_suffix():
    t = Tasklet(slug="x", system_prompt="y")
    assert t.cache_fingerprint == "tasklet_x_v1"


# ============================================================================
# Builtins exist and look right
# ============================================================================


class TestBuiltins:
    def test_count(self):
        assert len(BUILTIN_TASKLETS) == 10

    def test_all_unique_slugs(self):
        slugs = [t.slug for t in BUILTIN_TASKLETS]
        assert len(slugs) == len(set(slugs))

    def test_all_have_prompts(self):
        for t in BUILTIN_TASKLETS:
            assert len(t.system_prompt) > 20, f"{t.slug} prompt too short"

    def test_all_use_cheap_model(self):
        for t in BUILTIN_TASKLETS:
            assert t.model == "qwen-turbo", f"{t.slug} not on cheap model"

    def test_intent_classifier_schema(self):
        assert intent_classifier.output_schema is not None
        assert "enum" in intent_classifier.output_schema["properties"]["intent"]

    def test_title_generator_no_schema(self):
        # Title is short raw text, no JSON
        assert title_generator.output_schema is None
        assert title_generator.max_tokens == 32

    @pytest.mark.asyncio
    async def test_builtin_smoke_through_mock(self):
        """Each builtin runs end-to-end via mock without crashing the framework."""
        adapter = _mock_adapter_returning('{"intent": "chat", "confidence": 0.9}')
        with patch(
            "app.services.ai.adapters.factory.get_adapter",
            return_value=adapter,
        ):
            result = await intent_classifier.run("hi there", settings=MagicMock())
        assert result.ok is True
        assert result.value["intent"] == "chat"
