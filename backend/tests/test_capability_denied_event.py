"""Task 5 (Agent 权限页梳理立项, 2026-08-10): 拦截可见化(后端).

When ``HighRiskCapabilityGateHook`` denies a tool call, the refusal used to
be visible only to the model (via the tool-result error text it re-reads).
This makes the denial user-visible: ``HookResult.abort_code`` lets a hook
classify its abort machine-readably, and ``AgentRunner._run_pre_hooks``
turns a ``capability_denied``-classified abort into a transcript event
(``RunRecorder.record_event``), deduped per turn per tool so a retrying
model can't flood the transcript.

Companion to test_hook_chain_execution.py (generic hook-chain dispatch) and
test_high_risk_capability_gate.py (the gate's own allow/deny logic) — this
file is specifically about the event that fires ON TOP of a capability_denied
abort, not the gate's grading rules themselves.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.infra.hooks import HookContext, HookResult
from app.services.infra.hooks.high_risk_capability_gate import (
    HighRiskCapabilityGateHook,
)


def _composed() -> ComposedSystemPrompt:
    return ComposedSystemPrompt(
        agent_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_slug="storyboard",
        model="qwen-max",
        temperature=0.7,
        max_tokens=1024,
        system_message="SYSTEM",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="x",
    )


class FakeRecorder:
    """Minimal RunRecorder stand-in — just enough for _run_pre_hooks."""

    def __init__(self) -> None:
        self.run_id = "1"
        self.user_id = uuid4()
        self.session_id = None
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.heartbeat = AsyncMock()
        self.check_cancelled = AsyncMock(return_value=False)
        self.record_usage = AsyncMock()

    async def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


def _runner() -> AgentRunner:
    """A bare AgentRunner — adapter/skill_tool are never exercised because
    these tests call ``_run_pre_hooks`` directly."""
    return AgentRunner(adapter=AsyncMock(), skill_tool=AsyncMock())


async def _pre_hooks(
    runner: AgentRunner,
    *,
    recorder: Optional[FakeRecorder],
    tool_name: str = "CreateShot",
    args: Optional[dict[str, Any]] = None,
) -> Optional[HookResult]:
    return await runner._run_pre_hooks(
        composed=_composed(),
        recorder=recorder,
        tool_name=tool_name,
        args=args if args is not None else {},
        iteration=1,
    )


# ============================================================
# 1. Gate denies (no write_level granted) → one transcript event
# ============================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_denied_records_one_event():
    runner = _runner()
    runner.hooks = _registry_with(HighRiskCapabilityGateHook(agent=None))
    recorder = FakeRecorder()

    result = await _pre_hooks(runner, recorder=recorder, tool_name="CreateShot")

    assert result is not None
    assert result.decision == "abort"
    assert result.abort_code == "capability_denied"
    assert len(recorder.events) == 1
    event_type, payload = recorder.events[0]
    assert event_type == "capability_denied"
    assert payload["tool"] == "CreateShot"
    assert "write" in payload["reason"]


# ============================================================
# 2. Same turn, same tool, denied again → no second event
# ============================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_capability_denied_dedup_same_turn_same_tool():
    runner = _runner()
    runner.hooks = _registry_with(HighRiskCapabilityGateHook(agent=None))
    recorder = FakeRecorder()

    await _pre_hooks(runner, recorder=recorder, tool_name="CreateShot")
    await _pre_hooks(runner, recorder=recorder, tool_name="CreateShot")

    assert len(recorder.events) == 1


# ============================================================
# 3. Non-capability abort (abort_code=None) → no event at all
# ============================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_capability_abort_does_not_record_event():
    async def other_abort_hook(ctx: HookContext) -> HookResult:
        return HookResult(decision="abort", abort_reason="unrelated failure")

    runner = _runner()
    runner.hooks = _registry_with(other_abort_hook)
    recorder = FakeRecorder()

    result = await _pre_hooks(runner, recorder=recorder, tool_name="AnyTool")

    assert result is not None
    assert result.decision == "abort"
    assert result.abort_code is None
    assert recorder.events == []


# ============================================================
# 4. recorder=None → does not raise
# ============================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recorder_none_does_not_raise():
    runner = _runner()
    runner.hooks = _registry_with(HighRiskCapabilityGateHook(agent=None))

    result = await _pre_hooks(runner, recorder=None, tool_name="CreateShot")

    assert result is not None
    assert result.decision == "abort"
    assert result.abort_code == "capability_denied"


def _registry_with(hook: Any) -> Any:
    from app.services.infra.hooks import HookRegistry

    reg = HookRegistry()
    reg.register_pre(hook, name="under_test")
    return reg
