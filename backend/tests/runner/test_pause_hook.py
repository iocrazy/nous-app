"""Phase 2a Task 5: PauseHook — a target-level pause stops the ROOT run at its
next step boundary with ``stop_reason="paused"``; sub-runs are left alone."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.ai.runner.pause_hook import PauseHook
from app.services.ai.runner.step_hooks import StepContext, StepDecision

pytestmark = pytest.mark.unit
WIRING_SRC = Path("app/services/ai/chat/ai_library_chat_wiring.py")


class _Rec:
    def __init__(self, paused: bool):
        self.run_id = 1
        self._p = paused
        self.calls = 0

    async def check_paused(self) -> bool:
        self.calls += 1
        return self._p


async def test_pause_hook_stops_with_paused_reason():
    ctx = StepContext(turn=1, step=2, recorder=_Rec(True), parent_run_id=None)
    assert await PauseHook().before_llm_call(ctx) is StepDecision.STOP
    assert ctx.stop_reason == "paused"


async def test_pause_hook_ignores_sub_runs_and_unpaused():
    sub = _Rec(True)
    ctx = StepContext(turn=1, step=2, recorder=sub, parent_run_id="p")
    assert await PauseHook().before_llm_call(ctx) is StepDecision.CONTINUE
    assert sub.calls == 0  # a sub-run never even polls the flag
    ctx = StepContext(turn=1, step=2, recorder=_Rec(False), parent_run_id=None)
    assert await PauseHook().before_llm_call(ctx) is StepDecision.CONTINUE
    assert ctx.stop_reason is None


async def test_pause_hook_tolerates_a_recorder_without_the_method():
    ctx = StepContext(turn=1, step=2, recorder=object(), parent_run_id=None)
    assert await PauseHook().before_llm_call(ctx) is StepDecision.CONTINUE
    ctx = StepContext(turn=1, step=2, recorder=None, parent_run_id=None)
    assert await PauseHook().before_llm_call(ctx) is StepDecision.CONTINUE


def test_hook_name_is_pause():
    assert PauseHook.name == "pause"


def test_chain_order_is_heartbeat_cancel_pause_budget_inbox():
    """Budget BEFORE inbox (Task 6 review F2): the claim is durable, the
    injection is not — a step that halts on the budget question must not have
    claimed a steer it will never read."""
    src = WIRING_SRC.read_text()
    assert re.search(
        r"HeartbeatHook\(\),\s*CancelHook\(\),\s*PauseHook\(\),\s*"
        r"BudgetGateHook\(\),\s*InboxClaimHook\(\)",
        src,
    ), "chain must be heartbeat → cancel → pause → budget → inbox"
