"""Tests for HighRiskCapabilityGateHook (A1 — screenwriting agent layer).

Companion to test_capability_gate.py (the OLD fail-open gate this new gate
coexists with, never replaces).
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import pytest

from app.services.infra.hooks import HookContext
from app.services.infra.hooks.capability_gate import CapabilityGateHook
from app.services.infra.hooks.high_risk_capability_gate import (
    TOOL_REQUIREMENTS,
    HighRiskCapabilityGateHook,
    ToolRequirement,
)


def _ctx(
    *,
    tool_name: str = "GenerateImage",
    tool_args: Optional[dict[str, Any]] = None,
) -> HookContext:
    return HookContext(
        run_id="0",
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        agent_slug="storyboard",
        user_id=UUID("00000000-0000-0000-0000-000000000003"),
        session_id=None,
        tool_name=tool_name,
        tool_args=tool_args if tool_args is not None else {},
        accumulated_prompt_tokens=0,
        accumulated_completion_tokens=0,
        accumulated_cost_cents=0.0,
        iteration=1,
    )


# ============================================================
# Tools with no declared requirement pass through untouched
# ============================================================


@pytest.mark.asyncio
async def test_tool_without_requirement_continues():
    hook = HighRiskCapabilityGateHook(agent=None)
    result = await hook(_ctx(tool_name="Skill"))
    assert result.decision == "continue"


# ============================================================
# Media: granted vs absent, per-kind, malformed profile
# ============================================================


@pytest.mark.asyncio
async def test_media_absent_denies():
    hook = HighRiskCapabilityGateHook(agent={})
    result = await hook(_ctx(tool_name="GenerateImage"))
    assert result.decision == "abort"
    assert "not granted" in result.abort_reason


@pytest.mark.asyncio
async def test_media_granted_allows():
    agent = {"capability_profile": {"capabilities": {"media": {"image": True}}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="GenerateImage"))
    assert result.decision == "continue"


@pytest.mark.asyncio
async def test_image_granted_video_still_denied():
    agent = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "video": False}}
        }
    }
    hook = HighRiskCapabilityGateHook(agent=agent)
    assert (await hook(_ctx(tool_name="GenerateImage"))).decision == "continue"
    video_result = await hook(_ctx(tool_name="GenerateVideo"))
    assert video_result.decision == "abort"
    assert "video" in video_result.abort_reason


@pytest.mark.asyncio
async def test_malformed_profile_denies_media():
    # Wrong-typed capability_profile must fail closed, not raise, not allow.
    agent = {"capability_profile": "not-a-dict"}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="GenerateImage"))
    assert result.decision == "abort"


# ============================================================
# Media per-turn call cap
# ============================================================


@pytest.mark.asyncio
async def test_media_cap_enforced_within_a_turn():
    agent = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "max_calls_per_turn": 2}}
        }
    }
    hook = HighRiskCapabilityGateHook(agent=agent)
    # Same hook instance = same "turn" (per build_agent_runner_stack's
    # per-turn HookRegistry design).
    first = await hook(_ctx(tool_name="GenerateImage"))
    second = await hook(_ctx(tool_name="GenerateImage"))
    third = await hook(_ctx(tool_name="GenerateImage"))
    assert first.decision == "continue"
    assert second.decision == "continue"
    assert third.decision == "abort"
    assert "cap" in third.abort_reason


@pytest.mark.asyncio
async def test_kill_switch_blocks_even_when_capability_granted(monkeypatch):
    # The kill switch must override an explicit grant — it's a subtract-only
    # override, and the hook must enforce it itself (not rely solely on
    # ai_library_chat_service.py's tool-registration check).
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", "off")
    agent = {"capability_profile": {"capabilities": {"media": {"image": True}}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="GenerateImage"))
    assert result.decision == "abort"
    assert "kill switch" in result.abort_reason.lower()


@pytest.mark.asyncio
async def test_kill_switch_stops_a_wired_handler_before_it_runs(monkeypatch):
    """Full-path regression pin (review finding #1): even if a handler is
    wired regardless of registration-time gating (cached system prompt from a
    prior turn still advertising the tool, a hallucinated call, or a future
    caller that wires the handler unconditionally), the hook must abort
    BEFORE the handler executes — mirroring AgentRunner's real dispatch order
    (PreToolUse hooks run before the GenerateImage/GenerateVideo branches in
    agent_runner.py's tool-dispatch switch).
    """
    monkeypatch.setenv("FEATURE_AGENT_MEDIA_TOOLS", "off")
    agent = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "video": True}}
        }
    }
    hook = HighRiskCapabilityGateHook(agent=agent)

    handler_calls: list[dict] = []

    async def _fake_generate_image_handler(args: dict, run_context: dict) -> dict:
        handler_calls.append(args)
        return {"ok": True, "url": "should-never-happen"}

    result = await hook(_ctx(tool_name="GenerateImage"))
    if result.decision != "abort":
        await _fake_generate_image_handler({"prompt": "x"}, {})

    assert result.decision == "abort"
    assert handler_calls == []  # handler must never have been reached


@pytest.mark.asyncio
async def test_media_cap_defaults_when_unspecified():
    agent = {"capability_profile": {"capabilities": {"media": {"video": True}}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    allowed = 0
    for _ in range(10):
        result = await hook(_ctx(tool_name="GenerateVideo"))
        if result.decision == "continue":
            allowed += 1
    assert allowed == 4  # _DEFAULT_MEDIA_CAP_PER_TURN


@pytest.mark.asyncio
async def test_a_fresh_hook_instance_resets_the_cap():
    # Two separate turns == two separate hook instances (per-turn wiring),
    # so exhausting the cap on one must not bleed into the next.
    agent = {
        "capability_profile": {
            "capabilities": {"media": {"image": True, "max_calls_per_turn": 1}}
        }
    }
    turn_one = HighRiskCapabilityGateHook(agent=agent)
    assert (await turn_one(_ctx(tool_name="GenerateImage"))).decision == "continue"
    assert (await turn_one(_ctx(tool_name="GenerateImage"))).decision == "abort"

    turn_two = HighRiskCapabilityGateHook(agent=agent)
    assert (await turn_two(_ctx(tool_name="GenerateImage"))).decision == "continue"


# ============================================================
# Write-grading (via monkeypatched TOOL_REQUIREMENTS — no real screenwriting
# tools exist yet in A1; this exercises the enforcement path the same way
# A4 will exercise it once ListScenes/CreateShot/etc. land).
# ============================================================


@pytest.mark.asyncio
async def test_propose_only_agent_cannot_write(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS, "UpdateShot", ToolRequirement(write_level="write")
    )
    agent = {"capability_profile": {"capabilities": {"write_level": "propose"}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="UpdateShot"))
    assert result.decision == "abort"
    assert "write" in result.abort_reason


@pytest.mark.asyncio
async def test_read_only_agent_cannot_propose(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS, "ProposeEdit", ToolRequirement(write_level="propose")
    )
    agent = {"capability_profile": {"capabilities": {"write_level": "read"}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="ProposeEdit"))
    assert result.decision == "abort"


@pytest.mark.asyncio
async def test_write_level_agent_may_call_lower_tier_tool(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS, "ReadScene", ToolRequirement(write_level="read")
    )
    agent = {"capability_profile": {"capabilities": {"write_level": "write"}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="ReadScene"))
    assert result.decision == "continue"


@pytest.mark.asyncio
async def test_no_write_level_denies_even_read_tier_tool(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS, "ReadScene", ToolRequirement(write_level="read")
    )
    hook = HighRiskCapabilityGateHook(agent={})  # no capability_profile at all
    result = await hook(_ctx(tool_name="ReadScene"))
    assert result.decision == "abort"


# ============================================================
# delete / cross_episode_read / external_publish
# ============================================================


@pytest.mark.asyncio
async def test_delete_not_granted_denies(monkeypatch):
    monkeypatch.setitem(TOOL_REQUIREMENTS, "DeleteScene", ToolRequirement(delete=True))
    hook = HighRiskCapabilityGateHook(agent={})
    result = await hook(_ctx(tool_name="DeleteScene"))
    assert result.decision == "abort"


@pytest.mark.asyncio
async def test_delete_granted_allows(monkeypatch):
    monkeypatch.setitem(TOOL_REQUIREMENTS, "DeleteScene", ToolRequirement(delete=True))
    agent = {"capability_profile": {"capabilities": {"delete": True}}}
    hook = HighRiskCapabilityGateHook(agent=agent)
    result = await hook(_ctx(tool_name="DeleteScene"))
    assert result.decision == "continue"


@pytest.mark.asyncio
async def test_cross_episode_read_gate(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS,
        "ReadOtherEpisode",
        ToolRequirement(cross_episode_read=True),
    )
    denied = HighRiskCapabilityGateHook(agent={})
    assert (await denied(_ctx(tool_name="ReadOtherEpisode"))).decision == "abort"

    agent = {"capability_profile": {"capabilities": {"cross_episode_read": True}}}
    allowed = HighRiskCapabilityGateHook(agent=agent)
    assert (await allowed(_ctx(tool_name="ReadOtherEpisode"))).decision == "continue"


@pytest.mark.asyncio
async def test_external_publish_gate(monkeypatch):
    monkeypatch.setitem(
        TOOL_REQUIREMENTS, "PublishExternally", ToolRequirement(external_publish=True)
    )
    denied = HighRiskCapabilityGateHook(agent={})
    assert (await denied(_ctx(tool_name="PublishExternally"))).decision == "abort"

    agent = {"capability_profile": {"capabilities": {"external_publish": True}}}
    allowed = HighRiskCapabilityGateHook(agent=agent)
    assert (await allowed(_ctx(tool_name="PublishExternally"))).decision == "continue"


# ============================================================
# The whole point: contrast with the OLD fail-open gate
# ============================================================


@pytest.mark.asyncio
async def test_contrast_old_gate_fails_open_new_gate_fails_closed_same_input():
    """Pin the exact regression this stage exists to prevent.

    Same tool call (GenerateImage), same agent with NO capability_profile
    at all — CapabilityGateHook (blacklist, fail-open) has nothing telling
    it to block, so it continues. HighRiskCapabilityGateHook (whitelist,
    fail-closed) has nothing telling it to allow, so it aborts. If someone
    "unifies" the two gates, this test starts failing.
    """
    agent_with_no_profile = {"id": "agent-x"}  # no capability_profile key at all
    ctx = _ctx(tool_name="GenerateImage")

    old_gate = CapabilityGateHook(agent_with_no_profile.get("capability_profile"))
    old_result = await old_gate(ctx)
    assert old_result.decision == "continue"  # fail-open: nothing blacklists it

    new_gate = HighRiskCapabilityGateHook(agent_with_no_profile)
    new_result = await new_gate(ctx)
    assert new_result.decision == "abort"  # fail-closed: nothing granted it


@pytest.mark.asyncio
async def test_contrast_malformed_profile_old_continues_new_aborts():
    """Same idea, but with an actively malformed (not merely absent) profile."""
    agent = {"capability_profile": {"chat": "this-should-be-a-dict"}}
    ctx = _ctx(tool_name="GenerateImage")

    old_gate = CapabilityGateHook(agent["capability_profile"])
    assert (await old_gate(ctx)).decision == "continue"

    new_gate = HighRiskCapabilityGateHook(agent)
    assert (await new_gate(ctx)).decision == "abort"
