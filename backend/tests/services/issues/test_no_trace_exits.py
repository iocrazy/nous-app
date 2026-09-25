"""fh3 T4: the four ``run_turn`` exits that dropped the tool_calls trace.

User ruling (2026-09-25), per exit:

* ``run_timeout`` → A: carry the trace and route by the declaration. The
  deadline is checked at the loop TOP, a step boundary, exactly like the hook
  STOP fh2 T4 already routes by declaration.
* ``max_tool_iterations_exceeded`` → A′: route by the LAST declaration, unless
  a non-FinishIssue tool ran after it. Then the declaration went stale (the
  agent kept working) and the turn is ``continue``.
* abort mid-call → A: carry the trace; the existing cancel demotion
  (completed → continue → in_review) applies.
* ``_awaiting_approval_response`` → B: no trace, route by the exit. A pre-hook
  approval means the gated tool never ran.

Every runner-level test below uses an adapter WITHOUT ``stream``: production's
chat wiring hands ``stream_turn`` an ``LLMFallbackChain``, so the buffered
fallback branch is the only path production takes (CLAUDE.md, 2026-09-08).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.workflows.issue_lifecycle import route_finish_outcome

pytestmark = pytest.mark.unit

_REASON = "The requested synopsis is written and delivered."
_EMPTY_OUTPUT_CALL = (
    "needs_followup",
    {
        "agent_outcome": "empty_output",
        "outcome_reason": "Agent produced no output (EMPTY_OUTPUT)",
    },
)


# ── fixtures: a no-``stream`` adapter scripted call by call ────────────────


def _tool_call(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {
        "id": f"call_{n}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _tool_response(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [_tool_call(name, args, n)],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }


def _finish(outcome: str) -> tuple[str, dict[str, Any]]:
    return "FinishIssue", {"outcome": outcome, "reason": _REASON}


def _skill(n: int) -> tuple[str, dict[str, Any]]:
    # Distinct args per call so the loop guard never sees a repeat.
    return "Skill", {"skill": f"script-outline-{n}"}


class _ScriptedNoStreamAdapter:
    """No ``stream`` attribute on purpose. Call ``i`` answers with
    ``script[i]`` as one tool call; ``hooks[i]`` (optional) runs first."""

    def __init__(self, script, hooks=None):
        self._script = list(script)
        self._hooks = dict(hooks or {})
        self.calls = 0

    async def call(self, composed, messages, **k):
        i = self.calls
        self.calls += 1
        hook = self._hooks.get(i)
        if hook is not None:
            await hook()
        assert i < len(self._script), f"unexpected model call #{i + 1}"
        name, args = self._script[i]
        return _tool_response(name, args, i + 1)


class _Rec:
    def __init__(self):
        self.events: list[tuple[str, Any]] = []
        self.views = {"view": {"question": None}}

    async def record_event(self, event_type, payload, *, turn=None, step=None):
        self.events.append((event_type, payload))

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def turn_ends(self):
        return [p for t, p in self.events if t == "turn_end"]


def _composed(timeout_sec: int | None = None):
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
        timeout_sec=timeout_sec,
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain
    from app.services.ai.tools.finish_issue_tool import finish_issue_handler

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {"skill": args.get("skill"), "prompt": "# Outline"}

    runner = AgentRunner(
        adapter=adapter, skill_tool=_Tool(), step_hooks=StepHookChain([])
    )
    runner.finish_issue_handler = finish_issue_handler
    return runner


async def _terminal(runner, *, timeout_sec=None, abort=None, rec=None):
    rec = rec or _Rec()
    chunks: list[StreamChunk] = [
        ch
        async for ch in runner.stream_turn(
            _composed(timeout_sec),
            [{"role": "user", "content": "q"}],
            recorder=rec,
            abort=abort,
            auto_recorder=False,
        )
    ]
    assert chunks, "the buffered fallback always yields a terminal chunk"
    return chunks[-1], rec


def _names(trace) -> list[str]:
    return [c.get("name") for c in (trace or [])]


def _max_iterations_script(last: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    from app.services.ai.runner.agent_runner import MAX_TOOL_ITERATIONS

    head = [_skill(n) for n in range(MAX_TOOL_ITERATIONS - len(last))]
    return head + last


# ── 1. the runner: each exit carries (or, for approval, withholds) the trace ──


async def test_run_timeout_keeps_the_trace_on_the_buffered_path():
    """Step 1 declares completed; the call outlives the 1s deadline, so the
    loop top of step 2 returns run_timeout. The run stays typed as a timeout
    (turn_end error / error_code run_timeout) so ops still sees it."""
    adapter = _ScriptedNoStreamAdapter(
        [_finish("completed")], hooks={0: lambda: asyncio.sleep(1.05)}
    )
    terminal, rec = await _terminal(_runner(adapter), timeout_sec=1)
    assert adapter.calls == 1
    assert _names(terminal.tool_call_trace) == ["FinishIssue"]
    assert terminal.tool_call_trace[0]["result"]["acknowledged"] is True
    assert (terminal.usage or {}).get("error_code") == "run_timeout"
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "error", ends
    assert ends[0].get("error_code") == "run_timeout", ends


async def test_max_tool_iterations_keeps_the_trace_on_the_buffered_path():
    script = _max_iterations_script([_finish("continue"), _finish("completed")])
    terminal, rec = await _terminal(_runner(_ScriptedNoStreamAdapter(script)))
    assert _names(terminal.tool_call_trace)[-2:] == ["FinishIssue", "FinishIssue"]
    assert len(terminal.tool_call_trace) == len(script)
    assert (terminal.usage or {}).get("error_code") == "max_tool_iterations_exceeded"
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "max_iterations", ends


async def test_abort_mid_call_keeps_the_trace_on_the_buffered_path():
    """Step 1 declares completed; the cancel lands during step 2's model
    call. Everything in the trace really ran."""
    from app.agent_framework import AbortController

    abort = AbortController()

    async def _cancel_then_hang():
        abort.fire(reason="user cancel")
        await asyncio.sleep(30)

    adapter = _ScriptedNoStreamAdapter(
        [_finish("completed"), _skill(0)], hooks={1: _cancel_then_hang}
    )
    terminal, rec = await _terminal(_runner(adapter), abort=abort)
    assert (terminal.usage or {}).get("stop_reason") == "cancelled"
    assert _names(terminal.tool_call_trace) == ["FinishIssue"]
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "cancelled", ends


def test_awaiting_approval_still_carries_no_trace():
    """Ruling B: a pre-hook approval means the gated tool never ran, so an
    earlier declaration must not route the issue."""
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.infra.hooks import ApprovalRequest, HookResult

    out = AgentRunner._awaiting_approval_response(
        HookResult(
            decision="await_approval",
            approval_request=ApprovalRequest(reason="publish live", payload={}),
        )
    )
    assert out["awaiting_approval"] is True
    assert "tool_calls" not in out


# ── 2. the chat service hands the exit marker to the issue executor ─────────


async def _chat_turn(stream_turn) -> dict[str, Any]:
    from app.services.ai.chat import ai_library_chat_service as svc_mod
    from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(run_turn_result={"content": ""}, agent_id=agent_id):
        svc_mod.build_agent_runner_stack.return_value.runner.stream_turn = stream_turn
        return await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(),
            user_id=user_id,
            content="go",
            trigger="issue_dispatch",
            chunk_callback=AsyncMock(),
        )


@pytest.mark.parametrize(
    "usage, expected",
    [
        (
            {"error_code": "max_tool_iterations_exceeded"},
            "max_tool_iterations_exceeded",
        ),
        ({"error_code": "run_timeout"}, "run_timeout"),
        (
            {"warning": "max_stream_iterations_exceeded"},
            "max_stream_iterations_exceeded",
        ),
        (None, None),
    ],
)
async def test_chat_service_returns_the_terminal_chunk_exit_code(usage, expected):
    """The buffered branch forwards the exit as ``usage.error_code`` (the true
    stream as ``usage.warning``). The chat service used to drop both, so the
    executor could not tell a max-iterations exit from a normal end. It must
    NOT surface it as ``error``: that key raises 502 on the issue path."""

    async def stream_turn(*a, **k):
        yield StreamChunk(
            delta_text="",
            finish_reason="stop",
            usage=usage,
            tool_call_trace=[],
        )

    out = await _chat_turn(stream_turn)
    assert out["exit_code"] == expected


async def test_production_seam_end_to_end_max_iterations_stale_declaration():
    """Real runner buffered fallback → real chat-service rebuild →
    resolve_turn_outcome. Declared completed, then kept calling tools until the
    ceiling: the declaration went stale, so the turn is ``continue``."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    script = _max_iterations_script([_finish("completed"), _skill(99)])
    real = _runner(_ScriptedNoStreamAdapter(script))

    async def stream_turn(*a, **k):
        async for ch in real.stream_turn(
            _composed(),
            [{"role": "user", "content": "q"}],
            recorder=_Rec(),
            auto_recorder=False,
        ):
            yield ch

    out = await _chat_turn(stream_turn)
    assert out["exit_code"] == "max_tool_iterations_exceeded"
    got = resolve_turn_outcome(out)
    assert got.outcome == "continue"
    assert "declaration_stale_after_tools" in (got.reason or "")


# ── 3. resolve_turn_outcome: A for timeout / abort, A′ for max iterations ────


def _trace(*calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for i, (name, args) in enumerate(calls, start=1):
        result = (
            {**args, "acknowledged": True} if name == "FinishIssue" else {"ok": True}
        )
        out.append({"name": name, "args": args, "result": result, "iteration": i})
    return out


def _turn(trace, **extra) -> dict[str, Any]:
    return {
        "assistant_message": {"content": ""},
        "tool_calls": trace,
        "run_id": "347463748025273",
        **extra,
    }


@pytest.mark.parametrize(
    "exit_code", ["max_tool_iterations_exceeded", "max_stream_iterations_exceeded"]
)
def test_max_iterations_declaration_followed_by_a_tool_is_stale(exit_code):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(_trace(_finish("completed"), _skill(1), _skill(2)), exit_code=exit_code)
    )
    assert got.outcome == "continue"
    assert "declaration_stale_after_tools" in got.reason
    assert got.awaiting_input is False


def test_max_iterations_finish_loop_routes_by_the_last_declaration():
    """The 347463025060485 pattern: the model kept re-declaring. The last
    declaration is its latest word; FinishIssue after FinishIssue is not
    "kept working"."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(
            _trace(_skill(1), _finish("continue"), _finish("completed")),
            exit_code="max_tool_iterations_exceeded",
        )
    )
    assert got.outcome == "completed"
    assert got.reason == _REASON


def test_stale_rule_does_not_apply_to_a_timeout():
    """A′ is for max iterations only; a timeout routes by the declaration."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(_trace(_finish("completed"), _skill(1)), exit_code="run_timeout")
    )
    assert got.outcome == "completed"


def test_stale_rule_does_not_apply_to_a_normal_end():
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn(_trace(_finish("completed"), _skill(1))))
    assert got.outcome == "completed"


@pytest.mark.parametrize(
    "extra",
    [
        {"exit_code": "run_timeout"},
        {"exit_code": "max_tool_iterations_exceeded"},
        {"stop_reason": "cancelled", "cancelled": True},
    ],
)
async def test_the_three_exits_without_a_declaration_stay_empty_output(extra):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn(_trace(_skill(1), _skill(2)), **extra))
    assert got.outcome is None
    set_status = AsyncMock()
    await route_finish_outcome(
        1, got.outcome, got.reason, auto_close=False, set_status=set_status
    )
    status, kwargs = _EMPTY_OUTPUT_CALL
    set_status.assert_awaited_once_with(1, status, **kwargs)


# ── 4. through route_finish_outcome and the executor ───────────────────────


async def test_timeout_with_a_declared_completed_routes_to_review():
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(_trace(_finish("completed")), exit_code="run_timeout")
    )
    set_status = AsyncMock()
    await route_finish_outcome(
        1,
        got.outcome,
        got.reason,
        auto_close=False,
        set_status=set_status,
        content_len=0,
        disarm_wakeups=AsyncMock(),
    )
    set_status.assert_awaited_once_with(
        1, "in_review", agent_outcome="completed", outcome_reason=_REASON
    )


@pytest.mark.parametrize("auto_close", [False, True])
async def test_abort_after_a_declared_completed_goes_to_review_never_done(auto_close):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(_trace(_finish("completed")), stop_reason="cancelled", cancelled=True)
    )
    assert got.outcome == "continue"
    set_status = AsyncMock()
    await route_finish_outcome(
        1,
        got.outcome,
        got.reason,
        auto_close=auto_close,
        set_status=set_status,
        content_len=0,
        disarm_wakeups=AsyncMock(),
    )
    assert set_status.await_args.args[1] == "in_review"
    assert all(c.args[1] != "done" for c in set_status.await_args_list)


async def test_executor_routes_a_timed_out_declaration_without_forcing_one(
    monkeypatch,
):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "current_dbos_step_key", lambda: None)
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="347463736441767")
    )
    chat = AsyncMock()
    chat.run_session_turn = AsyncMock(
        return_value=_turn(_trace(_finish("completed")), exit_code="run_timeout")
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    for name in ("publish_chunk", "publish_message", "publish_status"):
        monkeypatch.setattr(m, name, AsyncMock())
    forced = AsyncMock(side_effect=AssertionError("content is empty; never forced"))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 347463736441767, "title": "Timeout Probe"},
        agent_id="a",
        user_id="u",
    )
    assert out["outcome"] == "completed"
    assert out["content"] == ""


def test_max_iterations_needs_input_declaration_followed_by_a_tool_is_stale():
    """fh3 T4 review L1: A′ applies to a declared ``needs_input`` too. The
    agent asked for input, then kept calling tools until the ceiling, so the
    question is stale and the turn is ``continue``, never a park."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn(
            _trace(_finish("needs_input"), _skill(1)),
            exit_code="max_tool_iterations_exceeded",
        )
    )
    assert got.outcome == "continue"
    assert "declaration_stale_after_tools" in got.reason
    assert "'needs_input'" in got.reason
    assert got.awaiting_input is False
