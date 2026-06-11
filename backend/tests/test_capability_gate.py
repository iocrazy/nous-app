"""Tests for CapabilityGateHook (Phase 4.5 Week 1 — agent capability gating)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import pytest

from app.services.infra.hooks import HookContext
from app.services.infra.hooks.capability_gate import CapabilityGateHook


def _ctx(
    *,
    tool_name: str = "Skill",
    tool_args: Optional[dict[str, Any]] = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 200,
) -> HookContext:
    return HookContext(
        run_id="0",
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_slug="script_ai",
        user_id=UUID("00000000-0000-0000-0000-000000000003"),
        session_id=None,
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {"skill": "script-outline"},
        accumulated_prompt_tokens=prompt_tokens,
        accumulated_completion_tokens=completion_tokens,
        accumulated_cost_cents=0.0,
        iteration=1,
    )


# ============================================================
# tool_blacklist
# ============================================================


@pytest.mark.asyncio
async def test_blacklisted_tool_aborts() -> None:
    hook = CapabilityGateHook({"tool_blacklist": ["Delegate", "Skill"]})
    result = await hook(_ctx(tool_name="Skill"))
    assert result.decision == "abort"
    assert "Skill" in result.abort_reason
    assert "script_ai" in result.abort_reason


@pytest.mark.asyncio
async def test_non_blacklisted_tool_continues() -> None:
    hook = CapabilityGateHook({"tool_blacklist": ["Delegate"]})
    assert (await hook(_ctx(tool_name="Skill"))).decision == "continue"


# ============================================================
# allowed_skills
# ============================================================


@pytest.mark.asyncio
async def test_skill_outside_allowlist_aborts() -> None:
    hook = CapabilityGateHook({"allowed_skills": ["script-outline"]})
    result = await hook(_ctx(tool_name="Skill", tool_args={"skill": "script-branch"}))
    assert result.decision == "abort"
    assert "script-branch" in result.abort_reason


@pytest.mark.asyncio
async def test_skill_in_allowlist_continues() -> None:
    hook = CapabilityGateHook({"allowed_skills": ["script-outline"]})
    result = await hook(_ctx(tool_name="Skill", tool_args={"skill": "script-outline"}))
    assert result.decision == "continue"


@pytest.mark.asyncio
async def test_empty_allowlist_means_unrestricted() -> None:
    hook = CapabilityGateHook({"allowed_skills": []})
    result = await hook(_ctx(tool_name="Skill", tool_args={"skill": "anything"}))
    assert result.decision == "continue"


@pytest.mark.asyncio
async def test_allowlist_only_gates_the_skill_tool() -> None:
    hook = CapabilityGateHook({"allowed_skills": ["script-outline"]})
    assert (await hook(_ctx(tool_name="Delegate", tool_args={}))).decision == (
        "continue"
    )


# ============================================================
# max_parallel_delegates
# ============================================================


@pytest.mark.asyncio
async def test_zero_delegates_blocks_delegate_tool() -> None:
    hook = CapabilityGateHook({"max_parallel_delegates": 0})
    result = await hook(_ctx(tool_name="Delegate", tool_args={}))
    assert result.decision == "abort"
    assert "delegate" in result.abort_reason.lower()


@pytest.mark.asyncio
async def test_positive_delegates_allows_delegate_tool() -> None:
    # >0 concurrency enforcement is M2; until then any positive value passes.
    hook = CapabilityGateHook({"max_parallel_delegates": 2})
    assert (await hook(_ctx(tool_name="Delegate", tool_args={}))).decision == (
        "continue"
    )


# ============================================================
# context_budget_tokens
# ============================================================


@pytest.mark.asyncio
async def test_over_context_budget_aborts() -> None:
    hook = CapabilityGateHook({"context_budget_tokens": 250})
    result = await hook(_ctx(prompt_tokens=200, completion_tokens=100))
    assert result.decision == "abort"
    assert "300 > 250" in result.abort_reason


@pytest.mark.asyncio
async def test_under_context_budget_continues() -> None:
    hook = CapabilityGateHook({"context_budget_tokens": 1000})
    result = await hook(_ctx(prompt_tokens=200, completion_tokens=100))
    assert result.decision == "continue"


# ============================================================
# robustness
# ============================================================


@pytest.mark.asyncio
async def test_empty_or_malformed_profile_fails_open() -> None:
    assert (await CapabilityGateHook({})(_ctx())).decision == "continue"
    assert (await CapabilityGateHook(None)(_ctx())).decision == "continue"
    # Wrong types must not raise — config typos can't brick the agent.
    weird = CapabilityGateHook(
        {
            "tool_blacklist": "Skill",  # str, not list
            "allowed_skills": {"a": 1},  # dict, not list
            "max_parallel_delegates": "0",  # str, not int
            "context_budget_tokens": "lots",  # str, not int
        }
    )
    assert (await weird(_ctx())).decision == "continue"
