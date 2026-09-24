"""fh2 T4: a FinishIssue declaration must survive a turn that ends on a tool
call with no assistant text.

Production (30 days to 2026-09-23): all 4 ``agent_outcome=empty_output`` runs
ended ``… > tool_call > turn_end`` with no ``assistant`` event. Two of them
had declared ``FinishIssue(outcome=completed)`` — acknowledged by the handler
— and were still stamped EMPTY_OUTPUT:

* run 347463025060485 (``Acceptance 2a-1 Pause Resume Probe``): six steps,
  the last tool ``FinishIssue(completed)``; then PauseHook stopped the run at
  the step-7 boundary (no ``step_start`` for step 7).
* run 347463748025273 (``Acceptance 2a-4c2 Zero Budget Cancel Probe``):
  ``FinishIssue(completed)`` at step 1; the budget gate halted at the step-2
  boundary (``budget_check{halt}`` + ``question_asked{kind: budget}``).

The declaration was lost in ``AgentRunner._stopped_response``: the
``run_turn`` exit for a step-hook STOP built its result without the turn's
``tool_calls`` trace, and the buffered fallback of ``stream_turn`` (the only
path production takes — the chat wiring hands it an ``LLMFallbackChain`` with
no ``stream``) forwarded ``result.get("tool_calls") or []`` = ``[]``. So the
executor's ``extract_issue_outcome`` saw nothing and ``route_finish_outcome``
took the ``content_len == 0 and outcome is None`` branch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.workflows.issue_lifecycle import route_finish_outcome

pytestmark = pytest.mark.unit

# ── the production shapes ──────────────────────────────────────────────────

# Verbatim from agent_run_transcript_events (run 347463748025273, seq 4).
_PROD_FINISH_ARGS = {
    "reason": (
        "The requested one-paragraph zero-budget themed heist film synopsis "
        "is written and delivered."
    ),
    "outcome": "completed",
}
_PROD_FINISH_RESULT = {**_PROD_FINISH_ARGS, "acknowledged": True}

# run 347463748025273, seq 6 (question_asked{kind: budget}).
_BUDGET_QUESTION = {
    "question_id": "budget:347463748025273",
    "kind": "budget",
    "prompt": (
        "Budget exhausted: 0.0797 of 0 cents spent. Top up the budget, let the "
        "agent wrap up in one step, or cancel the issue."
    ),
    "options": [
        {"label": "Top up", "description": "Raise the issue's budget, then continue."},
        {"label": "Wrap up", "description": None},
        {"label": "Cancel", "description": None},
    ],
    "allow_free_text": False,
    "asked_at": "2026-09-08T19:53:59Z",
}


def _finish_trace(outcome: str = "completed") -> list[dict[str, Any]]:
    args = {**_PROD_FINISH_ARGS, "outcome": outcome}
    return [
        {
            "name": "FinishIssue",
            "args": args,
            "result": {**args, "acknowledged": True},
            "iteration": 1,
        }
    ]


_SKILL_TRACE = [
    {
        "name": "Skill",
        "args": {"skill": "script-outline"},
        "result": {"skill": "script-outline", "prompt": "# Script Outline"},
        "iteration": 1,
    }
]


# ── 1. the runner keeps the trace on a hook STOP ──────────────────────────


class _Rec:
    def __init__(self, question: dict | None = None):
        self.events: list[tuple[str, Any]] = []
        view_q = None
        if question is not None:
            view_q = {**question, "id": question["question_id"]}
            view_q.pop("question_id")
        self.views = {"view": {"question": view_q}}

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


class _StopAtStep2:
    """PauseHook / BudgetGateHook stand-in: the first boundary passes, the
    second one (after the FinishIssue step) stops the run."""

    name = "stop_at_step_2"

    def __init__(self, reason: str):
        self._reason = reason

    async def before_llm_call(self, ctx):
        from app.services.ai.runner.step_hooks import StepDecision

        if ctx.step >= 2:
            return ctx.stop(self._reason)
        return StepDecision.CONTINUE


class _FinishThenNothingAdapter:
    """No ``stream`` attribute on purpose — production's LLMFallbackChain.
    Step 1 answers with a FinishIssue tool call and no text (the prod shape);
    a second call would be a bug, the hook stops the run first."""

    def __init__(self):
        self.calls = 0

    async def call(self, composed, messages, **k):
        self.calls += 1
        assert self.calls == 1, "the hook must stop the run before step 2"
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "FinishIssue",
                                    "arguments": (
                                        '{"outcome": "completed", "reason": '
                                        '"The requested one-paragraph zero-budget '
                                        "themed heist film synopsis is written and "
                                        'delivered."}'
                                    ),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 4102, "completion_tokens": 888},
        }


def _composed():
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
    )


def _runner(reason: str):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain
    from app.services.ai.tools.finish_issue_tool import finish_issue_handler

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    runner = AgentRunner(
        adapter=_FinishThenNothingAdapter(),
        skill_tool=_Tool(),
        step_hooks=StepHookChain([_StopAtStep2(reason)]),
    )
    runner.finish_issue_handler = finish_issue_handler
    return runner


def _finish_calls(trace: list[dict] | None) -> list[dict]:
    return [c for c in (trace or []) if c.get("name") == "FinishIssue"]


@pytest.mark.parametrize("reason", ["paused", "awaiting_input", "cancelled"])
async def test_run_turn_hook_stop_keeps_the_tool_call_trace(reason):
    rec = _Rec(_BUDGET_QUESTION if reason == "awaiting_input" else None)
    out = await _runner(reason).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert out["stop_reason"] == reason
    calls = _finish_calls(out.get("tool_calls"))
    assert len(calls) == 1, out
    assert calls[0]["result"] == _PROD_FINISH_RESULT


@pytest.mark.parametrize("reason", ["paused", "awaiting_input"])
async def test_buffered_fallback_terminal_chunk_carries_the_trace_on_a_hook_stop(
    reason,
):
    """The production path: ``stream_turn`` with an adapter that has no
    ``stream`` delegates to ``run_turn`` and re-emits one terminal chunk.
    That chunk's ``tool_call_trace`` is what the chat service hands the
    executor — it was ``[]`` here, which is where the declaration died."""
    rec = _Rec(_BUDGET_QUESTION if reason == "awaiting_input" else None)
    chunks: list[StreamChunk] = []
    async for ch in _runner(reason).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        chunks.append(ch)
    terminal = chunks[-1]
    assert (terminal.usage or {}).get("stop_reason") == reason
    calls = _finish_calls(terminal.tool_call_trace)
    assert len(calls) == 1, terminal.tool_call_trace
    assert calls[0]["result"] == _PROD_FINISH_RESULT


# ── 2. the executor's outcome: a declared ``completed`` beats a budget park ──


def _turn_result(
    tool_calls: list[dict], *, question: dict | None = None
) -> dict[str, Any]:
    """``run_session_turn``'s result for a hook-stopped streaming turn."""
    out: dict[str, Any] = {
        "assistant_message": {"content": ""},
        "tool_calls": tool_calls,
        "run_id": "347463748025273",
    }
    if question is not None:
        out.update(stop_reason="awaiting_input", awaiting_input=True, question=question)
    return out


def test_budget_park_yields_to_a_declared_completed():
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn_result(_finish_trace(), question=_BUDGET_QUESTION))
    assert got.outcome == "completed"
    assert got.reason == _PROD_FINISH_ARGS["reason"]
    assert got.awaiting_input is False
    assert got.question is None


@pytest.mark.parametrize("declared", ["continue", "needs_input"])
def test_budget_park_stands_when_the_declaration_wants_more_work(declared):
    """``continue`` / ``needs_input`` both lead to more spend on the issue —
    the budget question is exactly the thing the human must answer first."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(
        _turn_result(_finish_trace(declared), question=_BUDGET_QUESTION)
    )
    assert got.outcome == "needs_input"
    assert got.awaiting_input is True
    assert got.question == _BUDGET_QUESTION


def test_an_agent_question_still_wins_over_an_earlier_completed():
    """AskUser is the agent's own later word; unchanged behaviour."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    user_q = {**_BUDGET_QUESTION, "kind": "user", "prompt": "Which ending?"}
    got = resolve_turn_outcome(_turn_result(_finish_trace(), question=user_q))
    assert got.outcome == "needs_input"
    assert got.awaiting_input is True
    assert got.question == user_q


def test_no_declaration_and_no_park_is_still_none():
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn_result(_SKILL_TRACE))
    assert got.outcome is None and got.awaiting_input is False


# ── 3. through route_finish_outcome ───────────────────────────────────────


@pytest.mark.parametrize("question", [None, _BUDGET_QUESTION])
async def test_prod_shape_routes_by_the_declaration_not_empty_output(question):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn_result(_finish_trace(), question=question))
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
        1,
        "in_review",
        agent_outcome="completed",
        outcome_reason=_PROD_FINISH_ARGS["reason"],
    )


async def test_tool_ended_without_a_declaration_is_still_empty_output():
    """Bucket ② (no declaration, last event a non-finish tool) is ticketed: it
    stays needs_followup / empty_output until a bounded continuation lands."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_turn_result(_SKILL_TRACE))
    set_status = AsyncMock()
    await route_finish_outcome(
        1, got.outcome, got.reason, auto_close=False, set_status=set_status
    )
    set_status.assert_awaited_once_with(
        1,
        "needs_followup",
        agent_outcome="empty_output",
        outcome_reason="Agent produced no output (EMPTY_OUTPUT)",
    )


# ── 4. both turn entry points use the resolver ────────────────────────────


async def test_run_issue_agent_returns_the_declared_outcome_on_a_budget_stop(
    monkeypatch,
):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "current_dbos_step_key", lambda: None)
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="347463736441767")
    )
    chat = AsyncMock()
    chat.run_session_turn = AsyncMock(
        return_value=_turn_result(_finish_trace(), question=_BUDGET_QUESTION)
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    for name in ("publish_chunk", "publish_message", "publish_status"):
        monkeypatch.setattr(m, name, AsyncMock())
    forced = AsyncMock(side_effect=AssertionError("content is empty; never forced"))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)

    out = await m.run_issue_agent(
        issue={"id": 347463736441767, "title": "Zero Budget Cancel Probe"},
        agent_id="a",
        user_id="u",
    )
    assert out["outcome"] == "completed"
    assert out["awaiting_input"] is False
    assert out["question"] is None


def test_reply_step_uses_the_same_resolver():
    """``run_issue_reply_step`` is the other turn entry point; a reply turn
    that declares completed and then hits the budget must route the same way.
    Source-level: the step is DBOS-decorated and its body is exercised by
    the lifecycle suites; this pins that it does not keep a private copy of
    the extract-then-park lines."""
    import inspect

    from app.workflows import issue_lifecycle

    src = inspect.getsource(issue_lifecycle.run_issue_reply_step)
    assert "resolve_turn_outcome(" in src
    assert "awaiting_input_outcome(" not in src


# ── 5. a human cancel after a declared completed never auto-closes ────────


@pytest.mark.parametrize("auto_close", [False, True])
async def test_cancelled_after_declared_completed_goes_to_review_never_done(
    auto_close,
):
    """Review MEDIUM-1: with the trace kept, a run cancelled via
    ``/ai-library/runs/{id}/cancel`` after FinishIssue(completed) would route
    as completed — and ``done`` under auto_close. A person said stop; a person
    reviews. The cancelled branch breaks out of the dispatch loop, so the
    downgraded ``continue`` takes the capped route: in_review, never done."""
    from app.services.issues.turn_outcome import resolve_turn_outcome

    result = {**_turn_result(_finish_trace()), "stop_reason": "cancelled"}
    got = resolve_turn_outcome(result)
    assert got.outcome == "continue"
    assert got.reason == _PROD_FINISH_ARGS["reason"]
    assert got.awaiting_input is False

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
    set_status.assert_awaited_once()
    assert set_status.await_args.args[1] == "in_review"
    for call in set_status.await_args_list:
        assert call.args[1] != "done"


@pytest.mark.parametrize("declared", ["continue", "needs_input"])
def test_cancelled_leaves_other_declarations_alone(declared):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    result = {**_turn_result(_finish_trace(declared)), "stop_reason": "cancelled"}
    assert resolve_turn_outcome(result).outcome == declared
