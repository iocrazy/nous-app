"""Seam A: the step-boundary hook chain.

The chain is the ONE place cross-cutting per-step behaviour lives. These
tests pin the contract (order, short-circuit, containment, injection) and
— load-bearing — that the runner's two paths each call it exactly once and
no longer carry the inline heartbeat/cancel blocks."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runner.step_hooks import (
    CancelHook,
    HeartbeatHook,
    StepContext,
    StepDecision,
    StepHookChain,
    default_step_hooks,
)

pytestmark = pytest.mark.unit
RUNNER_SRC = Path("app/services/ai/runner/agent_runner.py")


class _Hook:
    def __init__(self, name, decision=StepDecision.CONTINUE, raise_=False, inject=None):
        self.name, self._d, self._raise, self._inject = name, decision, raise_, inject
        self.calls = 0

    async def before_llm_call(self, ctx):
        self.calls += 1
        if self._raise:
            raise RuntimeError("bad hook")
        if self._inject:
            ctx.inject(self._inject)
        if self._d is StepDecision.STOP:
            return ctx.stop("paused")  # any literal in STOP_REASON_TO_TURN_END
        return self._d


def _ctx(recorder=None):
    return StepContext(turn=1, step=1, recorder=recorder)


async def test_hooks_run_in_registration_order():
    order = []

    class _H(_Hook):
        async def before_llm_call(self, ctx):
            order.append(self.name)
            return await super().before_llm_call(ctx)

    chain = StepHookChain([_H("a"), _H("b"), _H("c")])
    assert await chain.run(_ctx()) is StepDecision.CONTINUE
    assert order == ["a", "b", "c"]
    assert chain.names == ("a", "b", "c")


async def test_first_stop_wins_and_later_hooks_do_not_run():
    a, b, c = _Hook("a"), _Hook("b", StepDecision.STOP), _Hook("c")
    ctx = _ctx()
    assert await StepHookChain([a, b, c]).run(ctx) is StepDecision.STOP
    assert ctx.stop_reason == "paused"
    assert (a.calls, b.calls, c.calls) == (1, 1, 0)


async def test_a_raising_hook_is_skipped_not_fatal():
    bad, after = _Hook("bad", raise_=True), _Hook("after")
    assert await StepHookChain([bad, after]).run(_ctx()) is StepDecision.CONTINUE
    assert after.calls == 1


async def test_injected_messages_are_collected_on_the_context():
    ctx = _ctx()
    await StepHookChain([_Hook("i", inject={"role": "user", "content": "x"})]).run(ctx)
    assert ctx.injected == [{"role": "user", "content": "x"}]


async def test_cancel_hook_stops_with_reason_cancelled_only_when_flag_set():
    rec = AsyncMock()
    rec.check_cancelled = AsyncMock(return_value=True)
    ctx = _ctx(rec)
    assert await CancelHook().before_llm_call(ctx) is StepDecision.STOP
    assert ctx.stop_reason == "cancelled"
    rec.check_cancelled = AsyncMock(return_value=False)
    assert await CancelHook().before_llm_call(_ctx(rec)) is StepDecision.CONTINUE
    assert await CancelHook().before_llm_call(_ctx(None)) is StepDecision.CONTINUE


async def test_heartbeat_hook_refreshes_and_never_stops():
    rec = AsyncMock()
    assert await HeartbeatHook().before_llm_call(_ctx(rec)) is StepDecision.CONTINUE
    rec.heartbeat.assert_awaited_once()
    assert await HeartbeatHook().before_llm_call(_ctx(None)) is StepDecision.CONTINUE


def test_default_chain_is_heartbeat_then_cancel():
    assert default_step_hooks().names == ("heartbeat", "cancel")


# ── the load-bearing guard ────────────────────────────────────────────────


def _fn_src(name: str) -> str:
    whole = RUNNER_SRC.read_text(encoding="utf-8")
    m = re.search(
        rf"\n    async def {name}\(.*?(?=\n    (?:async )?def |\Z)", whole, re.S
    )
    assert m, f"{name} not found — guard would scan nothing"
    return m.group(0)


@pytest.mark.parametrize("fn", ["_run_turn_inner", "_stream_turn_inner"])
def test_each_inner_path_runs_the_chain_exactly_once_and_has_no_inline_copies(fn):
    src = _fn_src(fn)
    assert (
        src.count("self.step_hooks.run(") == 1
    ), f"{fn}: the chain must be invoked exactly once per step boundary"
    assert "check_cancelled(" not in src, f"{fn}: cancel is a hook now, not inline"
    assert (
        "recorder.heartbeat(" not in src
    ), f"{fn}: heartbeat is a hook now, not inline"
