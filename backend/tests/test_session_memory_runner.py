"""Audit #17: session-memory summarizer routes through the agent's own
provider instead of a hardcoded Qwen adapter (silent no-op off-Qwen)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.runner.session_memory_runner import (
    _FALLBACK_SUMMARY_MODEL,
    _default_summarizer,
)


def _fake_adapter(content: str = "NOTES") -> MagicMock:
    a = MagicMock()
    a.call = AsyncMock(return_value={"content": content})
    return a


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarizer_routes_through_agent_model():
    """A Doubao agent's session-memory must use Doubao, not Qwen."""
    adapter = _fake_adapter("doubao notes")
    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(return_value=adapter),
    ) as get_adapter:
        out = await _default_summarizer("p", model="doubao-seed-2")

    assert out == "doubao notes"
    # The factory was asked for the agent's OWN model, not qwen.
    assert get_adapter.call_args.args[0] == "doubao-seed-2"
    composed = adapter.call.await_args.args[0]
    assert composed.model == "doubao-seed-2"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarizer_falls_back_to_qwen_when_model_empty():
    adapter = _fake_adapter()
    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(return_value=adapter),
    ) as get_adapter:
        await _default_summarizer("p", model="")

    assert get_adapter.call_args.args[0] == _FALLBACK_SUMMARY_MODEL


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarizer_falls_back_on_unknown_prefix():
    """An unknown model prefix raises ValueError in the factory → retry with
    the cheap fallback model rather than no-op."""
    adapter = _fake_adapter()

    async def _factory(model, module):
        if model == "weird-model":
            raise ValueError("unsupported model")
        return adapter

    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=_factory,
    ):
        out = await _default_summarizer("p", model="weird-model")

    assert out == "NOTES"
    assert adapter.call.await_args.args[0].model == _FALLBACK_SUMMARY_MODEL


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarizer_swallows_errors_returns_empty():
    """Best-effort: any failure returns '' rather than raising into chat."""
    with patch(
        "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = await _default_summarizer("p", model="qwen-max")

    assert out == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_maybe_update_threads_model_into_summarizer():
    """The dispatched updater must pass the agent model down to the
    summarizer (so routing #17 actually takes effect end to end)."""
    from app.services.ai.runner import session_memory_runner as mod

    captured: dict = {}

    async def _fake_default(prompt, model=""):
        captured["model"] = model
        return "x"

    repo = MagicMock()

    class _FakeService:
        def __init__(self, *, repo, summarizer, trigger):
            self._summarizer = summarizer

        async def maybe_update(self, session_id, messages, model=""):
            # Drive the injected summarizer like the real service would.
            await self._summarizer("prompt")

    with (
        patch.object(mod, "_default_summarizer", _fake_default),
        patch.object(mod, "SessionMemoryService", _FakeService),
    ):
        await mod.maybe_update_session_memory(
            session_id="s1", messages=[], model="doubao-seed-2", repo=repo
        )

    assert captured["model"] == "doubao-seed-2"
