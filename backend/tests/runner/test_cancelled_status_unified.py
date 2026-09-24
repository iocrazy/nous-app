"""Framework hardening C: a cancelled turn is ``cancelled`` everywhere.

Before this a hook STOP (IssueStatusGate / CancelHook) returned normally with
``stop_reason="cancelled"`` and every writer downstream read "returned
normally" as success: the run row said ``completed`` (while its own fold said
``turn_end_reason='cancelled'``), the Delegate envelope said ``success``, and
the workforce task said ``done``. ``StepContext.run_id`` was never populated,
so the gate logged ``run None`` on every step.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder
from app.services.ai.runner.subagent_task_service import SubAgentTaskService
from app.services.ai.runner.turn_end import result_was_cancelled
from tests.runner.test_turn_end_reasons import _composed, _NoStreamAdapter, _Rec

pytestmark = pytest.mark.unit


# ── the one mapping ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"stop_reason": "cancelled", "cancelled": True}, True),
        ({"stop_reason": "cancelled"}, True),
        ({"cancelled": True}, True),  # run_turn's abort-mid-call shape
        ({"stop_reason": "paused", "cancelled": False}, False),
        ({"stop_reason": "awaiting_input"}, False),
        ({"content": "done"}, False),
        (None, False),
    ],
)
def test_result_was_cancelled(result, expected):
    assert result_was_cancelled(result) is expected


# ── writer 1: agent_runs.status ─────────────────────────────────────────


def _recorder(monkeypatch, *, folded_reason):
    rec = RunRecorder(
        agent_id=uuid4(),
        user_id=uuid4(),
        trigger="test",
        session_id=None,
        model="m",
    )
    rec.run_id = 123
    rec._event_writer = SimpleNamespace(
        views={"efficiency": {"turn_end_reason": folded_reason}}
    )
    finish = AsyncMock()
    monkeypatch.setattr(rec, "_finish", finish)
    exported: list[str] = []
    monkeypatch.setattr(
        rec, "_maybe_export_langfuse", lambda **k: exported.append(k["status"])
    )
    return rec, finish, exported


async def test_recorder_files_a_folded_cancel_as_cancelled(monkeypatch):
    rec, finish, exported = _recorder(monkeypatch, folded_reason="cancelled")
    await rec.__aexit__(None, None, None)
    assert finish.await_args.kwargs["status"] == "cancelled"
    assert exported == ["cancelled"]


async def test_recorder_still_files_cancel_requested_as_cancelled(monkeypatch):
    rec, finish, _ = _recorder(monkeypatch, folded_reason=None)
    rec._cancelled = True
    await rec.__aexit__(None, None, None)
    assert finish.await_args.kwargs["status"] == "cancelled"


@pytest.mark.parametrize("reason", [None, "completed", "paused", "awaiting_input"])
async def test_recorder_other_ends_stay_completed(monkeypatch, reason):
    rec, finish, _ = _recorder(monkeypatch, folded_reason=reason)
    await rec.__aexit__(None, None, None)
    assert finish.await_args.kwargs["status"] == "completed"


async def test_recorder_an_exception_is_still_failed_even_after_a_cancel_fold(
    monkeypatch,
):
    rec, finish, _ = _recorder(monkeypatch, folded_reason="cancelled")
    await rec.__aexit__(RuntimeError, RuntimeError("boom"), None)
    assert finish.await_args.kwargs["status"] == "failed"


# ── writer 2: the Delegate envelope ─────────────────────────────────────


@pytest.mark.parametrize(
    "result",
    [
        {"content": "", "stop_reason": "cancelled", "cancelled": True},
        {"content": "", "cancelled": True, "abort_reason": "user cancel"},
    ],
)
def test_envelope_names_a_cancelled_child_cancelled(result):
    env = SubAgentTaskService._build_envelope(result=result, sub_run_id=7)
    assert env["status"] == "cancelled"
    assert "error" not in env


def test_envelope_success_and_failure_unchanged():
    ok = SubAgentTaskService._build_envelope(result={"content": "x"}, sub_run_id=7)
    bad = SubAgentTaskService._build_envelope(
        result={"content": "", "error": "boom"}, sub_run_id=7
    )
    paused = SubAgentTaskService._build_envelope(
        result={"content": "", "stop_reason": "paused", "cancelled": False},
        sub_run_id=7,
    )
    assert (ok["status"], bad["status"], paused["status"]) == (
        "success",
        "failed",
        "success",
    )


async def test_fan_out_counts_a_cancelled_child_as_not_ok(monkeypatch):
    """A cancelled child is the truth, not a success: the fan-out reads
    ``partial`` (one ok, one cancelled) and ``failed`` when all cancelled."""
    svc = SubAgentTaskService.__new__(SubAgentTaskService)
    svc.max_parallel = 3
    outs = iter([{"status": "success"}, {"status": "cancelled"}])

    async def _spawn(entry):
        return next(outs)

    monkeypatch.setattr(svc, "spawn", _spawn)
    tasks = [{"subagent_type": "a", "prompt": "p"}] * 2
    out = await svc._spawn_parallel({"tasks": tasks})
    assert out["status"] == "partial"

    outs = iter([{"status": "cancelled"}, {"status": "cancelled"}])
    out = await svc._spawn_parallel({"tasks": tasks})
    assert out["status"] == "failed"


# ── StepContext.run_id ──────────────────────────────────────────────────


class _RunIdSpy:
    name = "run_id_spy"

    def __init__(self):
        self.seen: list = []

    async def before_llm_call(self, ctx):
        self.seen.append(ctx.run_id)
        return ctx.stop("cancelled")


def _spy_runner(adapter, spy):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(
        adapter=adapter, skill_tool=_Tool(), step_hooks=StepHookChain([spy])
    )


async def test_step_context_carries_the_run_id_on_the_buffered_path():
    spy = _RunIdSpy()
    rec = _Rec()
    rec.run_id = 4242
    async for _ in _spy_runner(_NoStreamAdapter(), spy).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    assert spy.seen == [4242]


async def test_step_context_carries_the_run_id_on_the_true_stream_path():
    class _StreamAdapter:
        async def call(self, *a, **k):
            raise AssertionError("must not call the model")

        async def stream(self, *a, **k):
            raise AssertionError("must not call the model")
            yield  # pragma: no cover

    spy = _RunIdSpy()
    rec = _Rec()
    rec.run_id = 4343
    async for _ in _spy_runner(_StreamAdapter(), spy).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    assert spy.seen == [4343]


async def test_step_context_run_id_is_none_without_a_recorder_run_id():
    spy = _RunIdSpy()
    await _spy_runner(_NoStreamAdapter(), spy).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=_Rec()
    )
    assert spy.seen == [None]


# ── the buffered chunk reaches the chat service's result ────────────────


async def test_buffered_cancel_reaches_the_chat_result_as_cancelled():
    """End to end on the production shape: a real ``stream_turn`` over an
    adapter with no ``stream`` (what LLMFallbackChain looks like) feeds the
    chat service's chunk loop. The issue workflow routes on the result's
    ``stop_reason``; before the fix it was ``None`` here."""
    from app.services.ai.chat import ai_library_chat_service as svc_mod
    from tests.runner.test_turn_end_reasons import _runner
    from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    real = _runner(_NoStreamAdapter(), "cancelled")
    with _chat_env(run_turn_result={"content": ""}, agent_id=agent_id):
        runner = svc_mod.build_agent_runner_stack.return_value.runner

        def stream_turn(*a, **k):
            return real.stream_turn(
                _composed(),
                [{"role": "user", "content": "q"}],
                recorder=_Rec(),
                auto_recorder=False,
            )

        runner.stream_turn = stream_turn
        out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(),
            user_id=user_id,
            content="go",
            trigger="issue_dispatch",
            chunk_callback=AsyncMock(),
        )
    assert out["stop_reason"] == "cancelled"
    assert out["cancelled"] is True
