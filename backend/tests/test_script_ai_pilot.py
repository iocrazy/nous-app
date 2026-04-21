"""Unit tests for ScriptAIService — DB-driven prompt pipeline.

These are thin integration checks that the service wires
PromptComposer + AgentRunner correctly. Full end-to-end (with real DB
+ LLM) is deferred to Task 18's smoke test.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.script_ai_service import ScriptAIService


def _fake_composed() -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="script_ai",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="MOCK SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_outline_uses_composed_system_message(monkeypatch):
    """generate_outline must compose via PromptComposer with agent_slug=script_ai
    and run the result through AgentRunner."""
    from app.services import agent_runner as ar_module
    from app.services import prompt_composer as pc_module

    mock_compose = AsyncMock(return_value=_fake_composed())
    monkeypatch.setattr(pc_module.PromptComposer, "compose", mock_compose)

    # AgentRunner returns JSON (outline is parsed via _extract_json)
    outline_payload = json.dumps(
        [
            {"title": "Ch 1", "summary": "The detective arrives."},
            {"title": "Ch 2", "summary": "A body is found in the drifts."},
        ]
    )
    mock_run = AsyncMock(return_value={"content": outline_payload})
    monkeypatch.setattr(ar_module.AgentRunner, "run_turn", mock_run)

    svc = ScriptAIService()
    chapters = await svc.generate_outline(
        premise="a detective in a snowstorm", chapter_count=2
    )

    assert isinstance(chapters, list)
    assert len(chapters) == 2
    assert chapters[0]["title"] == "Ch 1"

    # Verify composer was called with script_ai slug
    assert mock_compose.await_count == 1
    args, kwargs = mock_compose.call_args
    # Support both positional and keyword invocation styles
    inp = None
    if args:
        # PromptComposer.compose(self, inp) — positional call from service
        for a in args:
            if hasattr(a, "agent_slug"):
                inp = a
                break
    if inp is None:
        inp = kwargs.get("inp")
    assert inp is not None, "composer.compose was not called with a ComposerInput"
    assert inp.agent_slug == "script_ai"
    # Task-specific instructions live in request_instructions, not a hardcoded system prompt
    assert inp.request_instructions is not None
    assert "outline" in inp.request_instructions.lower()


@pytest.mark.unit
def test_no_hardcoded_system_prompt_left():
    """Grep the service source for common hardcoded-prompt sentinels."""
    from app.services import script_ai_service

    src = inspect.getsource(script_ai_service)

    # No leftover `system_prompt = "..."` assignments
    assert (
        "system_prompt" not in src
    ), "script_ai_service still references `system_prompt` directly"
    # No direct LLM call helper
    assert "_call_llm" not in src, "script_ai_service still defines/uses `_call_llm`"
    # No direct httpx usage — all HTTP goes through QwenAdapter now
    assert (
        "httpx" not in src
    ), "script_ai_service still imports or uses `httpx` directly"
