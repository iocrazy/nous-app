"""fh3 T5 (ruling 10): an ``awaiting_approval`` exit must not close an issue.

Production's only path is ``stream_turn``'s buffered fallback. On an approval
exit it drops the trace (ruling B) and emits the non-empty text
``"\\n\\n[awaiting approval: …]"``. The executor used to read "content, no
declaration" and fire ``attempt_forced_finish_declaration``: one extra LLM
call, and the model could pick ``completed`` from the bracket text and close
an issue whose gated tool never ran. On the true-stream path the trace IS
carried, so an earlier FinishIssue(completed) routed the issue the same way.

Minimal guard (no park yet — no approval producer and no ``approval_requests``
table in production): ``awaiting_approval`` beats any declaration, the turn
routes to ``needs_followup`` with a typed ``awaiting_approval: `` reason, the
forced declaration is never called, and the bracket text stays as content.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.workflows.issue_lifecycle import route_finish_outcome
from tests.services.issues.test_no_trace_exits import (
    _REASON,
    _composed,
    _finish,
    _Rec,
    _ScriptedNoStreamAdapter,
    _skill,
    _trace,
    _turn,
)

pytestmark = pytest.mark.unit

_GATE_REASON = "publish live"
_ISSUE_ID = 347463736441767


class _HookRec(_Rec):
    """The hook chain builds its context off the recorder."""

    run_id = "347463736441769"
    user_id = uuid4()
    session_id = None
    team_id = None
    prompt_tokens = 0
    completion_tokens = 0


def _gated_runner(adapter):
    """A runner whose pre-hook asks for approval before any non-FinishIssue
    tool — so step 1's FinishIssue runs and step 2's Skill never does."""
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.tools.finish_issue_tool import finish_issue_handler
    from app.services.infra.hooks import ApprovalRequest, HookRegistry, HookResult

    async def gate(ctx) -> HookResult:
        if ctx.tool_name == "FinishIssue":
            return HookResult(decision="continue")
        return HookResult(
            decision="await_approval",
            approval_request=ApprovalRequest(reason=_GATE_REASON, payload={}),
        )

    class _Tool:
        recorder = None
        calls: list[dict] = []

        async def execute(self, args):
            self.calls.append(args)
            return {"skill": args.get("skill"), "prompt": "# Outline"}

    reg = HookRegistry()
    reg.register_pre(gate, name="publish_gate")
    tool = _Tool()
    runner = AgentRunner(adapter=adapter, skill_tool=tool, hooks=reg)
    runner.finish_issue_handler = finish_issue_handler
    return runner, tool


async def _chat_turn_through(runner) -> dict[str, Any]:
    """Real runner buffered fallback → real chat-service rebuild."""

    async def stream_turn(*a, **k):
        async for ch in runner.stream_turn(
            _composed(),
            [{"role": "user", "content": "q"}],
            recorder=_HookRec(),
            auto_recorder=False,
        ):
            yield ch

    return await _chat_turn(stream_turn)


async def _chat_turn(stream_turn) -> dict[str, Any]:
    """The approval repo raises, as it does in production (the table does not
    exist); the chat service logs it and carries on."""
    from app.services.ai.chat import ai_library_chat_service as svc_mod
    from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

    repo = MagicMock()
    repo.create = AsyncMock(
        side_effect=RuntimeError('relation "approval_requests" does not exist')
    )
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={"content": ""}, agent_id=agent_id, approval_repo=repo
    ):
        svc_mod.build_agent_runner_stack.return_value.runner.stream_turn = stream_turn
        return await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(),
            user_id=user_id,
            content="go",
            trigger="issue_dispatch",
            chunk_callback=AsyncMock(),
        )


def _patch_executor(monkeypatch, turn_result: dict[str, Any]) -> AsyncMock:
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "current_dbos_step_key", lambda: None)
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="347463736441768")
    )
    chat = AsyncMock()
    chat.run_session_turn = AsyncMock(return_value=turn_result)
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat)
    for name in ("publish_chunk", "publish_message", "publish_status"):
        monkeypatch.setattr(m, name, AsyncMock())
    forced = AsyncMock(return_value=("completed", "forced from the bracket text"))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)
    return forced


async def _run_executor():
    from app.services.issues import issue_agent_executor as m

    return await m.run_issue_agent(
        issue={"id": _ISSUE_ID, "title": "Approval Probe"},
        agent_id="a",
        user_id="u",
    )


def _approval_turn(content: str, trace=None, **extra) -> dict[str, Any]:
    return {
        **_turn(trace or [], awaiting_approval=True, approval_reason=_GATE_REASON),
        "assistant_message": {"content": content},
        **extra,
    }


_BRACKET = f"\n\n[awaiting approval: {_GATE_REASON}]"


# ── 1. the chat service hands the approval exit to the issue executor ───────


async def test_buffered_approval_exit_reaches_the_executor_typed():
    """Declared completed at step 1; step 2's gated Skill asks for approval
    and never runs. The rebuilt result carries the flag and reason — never as
    ``error`` (that key raises 502 on the issue path) — and the bracket text
    is still the assistant content."""
    adapter = _ScriptedNoStreamAdapter([_finish("completed"), _skill(1)])
    runner, tool = _gated_runner(adapter)
    out = await _chat_turn_through(runner)

    assert tool.calls == [], "the gated tool must not have run"
    assert out["tool_calls"] == []  # ruling B: the buffered exit drops the trace
    assert "[awaiting approval" in out["assistant_message"]["content"]
    assert out["awaiting_approval"] is True
    assert out["approval_reason"] == _GATE_REASON

    from app.services.issues.turn_outcome import (
        AWAITING_APPROVAL_REASON_PREFIX,
        resolve_turn_outcome,
    )

    got = resolve_turn_outcome(out)
    assert got.outcome == "needs_followup"
    assert got.reason == f"{AWAITING_APPROVAL_REASON_PREFIX}{_GATE_REASON}"
    assert got.awaiting_input is False and got.question is None


async def test_a_normal_turn_reports_no_approval():
    async def stream_turn(*a, **k):
        yield StreamChunk(
            delta_text="Done.", finish_reason="stop", usage=None, tool_call_trace=[]
        )

    out = await _chat_turn(stream_turn)
    assert out["awaiting_approval"] is False
    assert out["approval_reason"] is None


# ── 2. resolve_turn_outcome: approval beats any declaration ────────────────


@pytest.mark.parametrize("declared", ["completed", "continue", "needs_input"])
def test_true_stream_approval_with_a_declaration_is_not_routed_by_it(declared):
    """The true-stream path carries the trace, so a declaration is present;
    the gated tool may still never have run. Same tier as a park."""
    from app.services.issues.turn_outcome import (
        AWAITING_APPROVAL_REASON_PREFIX,
        resolve_turn_outcome,
    )

    got = resolve_turn_outcome(
        _approval_turn(_BRACKET, _trace(_finish(declared), _skill(1)))
    )
    assert got.outcome == "needs_followup"
    assert got.reason.startswith(AWAITING_APPROVAL_REASON_PREFIX)
    assert got.awaiting_input is False


def test_true_stream_approval_without_a_reason_still_types_it():
    """The true-stream terminal chunk files no ``approval_reason``."""
    from app.services.issues.turn_outcome import (
        AWAITING_APPROVAL_REASON_PREFIX,
        resolve_turn_outcome,
    )

    got = resolve_turn_outcome(
        {
            **_approval_turn(_BRACKET, _trace(_finish("completed"))),
            "approval_reason": "",
        }
    )
    assert got.outcome == "needs_followup"
    assert got.reason == f"{AWAITING_APPROVAL_REASON_PREFIX}approval required"


# ── 3. the executor never forces a declaration on an approval exit ─────────


async def test_executor_skips_the_forced_declaration_on_the_buffered_shape(
    monkeypatch,
):
    from app.services.issues.turn_outcome import AWAITING_APPROVAL_REASON_PREFIX

    forced = _patch_executor(monkeypatch, _approval_turn(_BRACKET))
    out = await _run_executor()
    forced.assert_not_awaited()
    assert out["outcome"] == "needs_followup"
    assert out["reason"] == f"{AWAITING_APPROVAL_REASON_PREFIX}{_GATE_REASON}"
    assert out["content"] == _BRACKET  # kept for the UI
    assert out["awaiting_input"] is False


async def test_executor_skips_the_forced_declaration_on_the_true_stream_shape(
    monkeypatch,
):
    forced = _patch_executor(
        monkeypatch, _approval_turn(_BRACKET, _trace(_finish("completed")))
    )
    out = await _run_executor()
    forced.assert_not_awaited()
    assert out["outcome"] == "needs_followup"


async def test_content_without_a_declaration_still_forces_one(monkeypatch):
    """No regression: the approval guard is the only new skip."""
    forced = _patch_executor(
        monkeypatch, _turn([], assistant_message={"content": "Here is the draft."})
    )
    out = await _run_executor()
    forced.assert_awaited_once()
    assert out["outcome"] == "completed"


# ── 4. route_finish_outcome: needs_followup, typed, never done ─────────────


@pytest.mark.parametrize("auto_close", [False, True])
async def test_approval_outcome_routes_to_needs_followup(auto_close):
    from app.services.issues.turn_outcome import resolve_turn_outcome

    got = resolve_turn_outcome(_approval_turn(_BRACKET, _trace(_finish("completed"))))
    set_status, disarm = AsyncMock(), AsyncMock()
    await route_finish_outcome(
        1,
        got.outcome,
        got.reason,
        auto_close=auto_close,
        set_status=set_status,
        content_len=len(_BRACKET),
        disarm_wakeups=disarm,
    )
    set_status.assert_awaited_once_with(
        1,
        "needs_followup",
        agent_outcome="awaiting_approval",
        outcome_reason=got.reason,
    )
    disarm.assert_not_awaited()
