"""Tests for the smart-canvas prompt-run service (Phase 2 Day 6-8)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from app.services.canvas.canvas_run_service import (
    DEFAULT_MODEL,
    CanvasRunService,
    _resolve_model,
)


class FakeAdapter:
    """Mimics the AIAdapter.call contract: returns an OpenAI-compatible
    response dict."""

    def __init__(self, *, reply: str = "ok", raise_with: Exception | None = None):
        self.reply = reply
        self.raise_with = raise_with
        self.calls: List[Dict[str, Any]] = []

    async def call(self, composed, messages):
        self.calls.append({"composed": composed, "messages": messages})
        if self.raise_with:
            raise self.raise_with
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": self.reply},
                    "finish_reason": "stop",
                }
            ]
        }


def make_service(adapter: FakeAdapter) -> CanvasRunService:
    svc = CanvasRunService(settings=SimpleNamespace())
    svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
    return svc


# ============================================================
# _resolve_model
# ============================================================


class TestResolveModel:
    def test_none_falls_back_to_default(self):
        assert _resolve_model(None) == DEFAULT_MODEL

    def test_empty_string_falls_back_to_default(self):
        assert _resolve_model("") == DEFAULT_MODEL

    def test_bare_model_id_passes_through(self):
        assert _resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_slash_form_strips_provider_prefix(self):
        assert _resolve_model("qwen/qwen-plus") == "qwen-plus"
        assert _resolve_model("anthropic/claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_slash_with_empty_suffix_falls_back_to_default(self):
        assert _resolve_model("qwen/") == DEFAULT_MODEL


# ============================================================
# run_prompt
# ============================================================


class TestRunPrompt:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text(self):
        adapter = FakeAdapter(reply="rendered output")
        svc = make_service(adapter)
        result = await svc.run_prompt(body="describe a robot")
        assert result.ok is True
        assert result.text == "rendered output"
        assert result.error is None
        # The adapter was handed one user message + composed prompt.
        assert adapter.calls[0]["messages"] == [
            {"role": "user", "content": "describe a robot"}
        ]

    @pytest.mark.asyncio
    async def test_empty_body_rejected_without_calling_adapter(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        result = await svc.run_prompt(body="   ")
        assert result.ok is False
        assert "empty" in (result.error or "").lower()
        assert adapter.calls == []

    @pytest.mark.asyncio
    async def test_adapter_exception_returns_in_band_failure(self):
        adapter = FakeAdapter(raise_with=RuntimeError("rate limited"))
        svc = make_service(adapter)
        result = await svc.run_prompt(body="anything")
        assert result.ok is False
        assert result.text == ""
        assert "rate limited" in (result.error or "")

    @pytest.mark.asyncio
    async def test_missing_choices_returns_empty_text_but_ok_true(self):
        """Malformed adapter response → text='' but ok stays true
        because the adapter call DID succeed (don't double-classify)."""
        adapter = FakeAdapter()

        async def call_returning_empty(composed, messages):
            return {}

        adapter.call = call_returning_empty  # type: ignore[method-assign]
        svc = make_service(adapter)
        result = await svc.run_prompt(body="hi")
        assert result.ok is True
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_provider_slug_picks_model(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
        await svc.run_prompt(body="hi", provider_slug="anthropic/claude-sonnet-4-6")
        svc._get_adapter.assert_called_once_with("claude-sonnet-4-6")

    @pytest.mark.asyncio
    async def test_no_provider_slug_uses_default_model(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        svc._get_adapter = AsyncMock(return_value=adapter)  # type: ignore[assignment]
        await svc.run_prompt(body="hi")
        svc._get_adapter.assert_called_once_with(DEFAULT_MODEL)

    @pytest.mark.asyncio
    async def test_agent_id_is_appended_to_system_message(self):
        adapter = FakeAdapter()
        svc = make_service(adapter)
        await svc.run_prompt(body="hi", agent_id="abc-123")
        composed = adapter.calls[0]["composed"]
        assert "Acting under agent abc-123" in composed.system_message
