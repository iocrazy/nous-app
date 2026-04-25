"""Unit tests for MemoryHarvester PostToolUse hook (skeleton, M1.A).

Real memory writing lives in M1.B. M1.A only validates the side_effect
Celery dispatch wiring works.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.services.hooks import HookContext
from app.services.hooks.memory_harvester import MemoryHarvesterHook


def _ctx() -> HookContext:
    return HookContext(
        run_id=UUID("00000000-0000-0000-0000-000000000010"),
        agent_id=UUID("00000000-0000-0000-0000-000000000020"),
        agent_slug="script_ai",
        user_id=UUID("00000000-0000-0000-0000-000000000030"),
        session_id=UUID("00000000-0000-0000-0000-000000000040"),
        tool_name="Skill",
        tool_args={"skill": "script-outline"},
        accumulated_prompt_tokens=100,
        accumulated_completion_tokens=50,
        accumulated_cost_cents=2.5,
        iteration=1,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_factory_returns_continue_no_side_effect():
    """Default M1.A wiring — no Celery task bound yet."""
    hook = MemoryHarvesterHook()
    result = await hook(_ctx(), {"prompt": "ok"})
    assert result.decision == "continue"
    assert result.side_effect is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_factory_bound_returns_signature_as_side_effect():
    """Caller binds a Celery task signature factory; hook produces side_effect."""
    sentinel_signature = MagicMock(name="celery_task_signature")
    factory = MagicMock(return_value=sentinel_signature)

    hook = MemoryHarvesterHook(signature_factory=factory)
    result = await hook(_ctx(), {"prompt": "ok"})

    assert result.decision == "continue"
    assert result.side_effect is sentinel_signature
    factory.assert_called_once()
    # Verify factory got the right per-run context.
    kwargs = factory.call_args.kwargs
    assert kwargs["agent_id"] == "00000000-0000-0000-0000-000000000020"
    assert kwargs["session_id"] == "00000000-0000-0000-0000-000000000040"
    assert kwargs["iteration"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_factory_exception_swallowed_no_side_effect():
    """Factory bug must not break the run."""
    factory = MagicMock(side_effect=RuntimeError("broken"))

    hook = MemoryHarvesterHook(signature_factory=factory)
    result = await hook(_ctx(), {"prompt": "ok"})

    assert result.decision == "continue"
    assert result.side_effect is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_factory_receives_none_session_id_when_no_session():
    factory = MagicMock(return_value=MagicMock())
    hook = MemoryHarvesterHook(signature_factory=factory)

    no_session_ctx = HookContext(
        run_id=UUID("00000000-0000-0000-0000-000000000010"),
        agent_id=UUID("00000000-0000-0000-0000-000000000020"),
        agent_slug="script_ai",
        user_id=UUID("00000000-0000-0000-0000-000000000030"),
        session_id=None,
        tool_name="Skill",
        tool_args={},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )

    await hook(no_session_ctx, {})
    assert factory.call_args.kwargs["session_id"] is None
