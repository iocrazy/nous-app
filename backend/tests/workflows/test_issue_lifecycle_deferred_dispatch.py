"""The issue turn's two steps report dispatches; the body performs them.

``run_issue_reply_step`` and ``run_issue_agent_step`` are ``@DBOS.step``, and a
turn's tools/hooks reach ``start_workflow_routed`` from inside them — which DBOS
refuses (``assert cur_ctx.is_workflow()``). The steps therefore run the turn
under ``collect_deferred_dispatches()`` and return the records in
``pending_dispatches``; the two workflow-body consumers (``_run_reply_turns``
and ``_run_dispatch_with_continuation``) drain them.

The ORDER is load-bearing and has its own test: the drain runs BEFORE
``route_finish_outcome``. Status routing and dispatch are independent results of
one turn (CLAUDE.md "正交的结果各自独立上报"), so a routing failure must not
swallow the dispatch — nor the reverse, which is why the drain never raises.

Module-identity note: this directory's autouse conftest restores ``sys.modules``
after the reload tests, so patching ``app.workflows.issue_lifecycle`` attributes
here resolves to the module these tests actually call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import app.workflows.issue_lifecycle as il

_ISSUE_ID = 700100000000000001
_USER_ID = "22222222-2222-2222-2222-222222222222"


def _record(task_type: str = "shot_generate") -> dict:
    from app.services.infra.deferred_dispatch import workflow_ref
    from app.workflows.script_shot_generate import script_shot_generate_workflow

    return {
        "task_type": task_type,
        "workflow": workflow_ref(script_shot_generate_workflow),
        "kwargs": {"shot_id": "1", "user_id": _USER_ID},
        "workflow_id": "33333333-3333-3333-3333-333333333333",
        "task_id": "task-1",
    }


# ====================================================================== #
# The steps collect and report
# ====================================================================== #


@pytest.mark.asyncio
async def test_reply_step_reports_dispatches_originated_during_the_turn():
    """The tool/hook inside the turn sees an ACTIVE collector — proving the
    step wraps the turn, not merely that it returns an empty list."""
    from app.services.infra.deferred_dispatch import (
        deferral_active,
        record_deferred_dispatch,
    )
    from app.workflows.script_shot_generate import script_shot_generate_workflow

    seen = {}

    async def _fake_turn(*args, **kwargs):
        seen["active"] = deferral_active()
        record_deferred_dispatch(
            task_type="shot_generate",
            dbos_workflow_callable=script_shot_generate_workflow,
            dbos_workflow_kwargs={"shot_id": "1"},
            workflow_id="33333333-3333-3333-3333-333333333333",
            task_id="task-1",
        )
        return {"assistant_message": {"content": "hi"}, "tool_calls": None}

    service = AsyncMock()
    service.run_session_turn = _fake_turn
    with (
        patch.object(il, "AILibraryChatService", return_value=service),
        patch.object(il, "publish_chunk", AsyncMock()),
        patch.object(il, "publish_message", AsyncMock()),
    ):
        out = await il.run_issue_reply_step.__wrapped__(
            issue_id=_ISSUE_ID,
            session_id="900",
            user_id=_USER_ID,
            reply_text="go",
        )

    assert seen["active"] is True
    assert len(out["pending_dispatches"]) == 1
    assert out["pending_dispatches"][0]["task_id"] == "task-1"
    assert out["content"] == "hi"


@pytest.mark.asyncio
async def test_agent_step_reports_dispatches_and_keeps_the_turn_result():
    from app.services.infra.deferred_dispatch import (
        deferral_active,
        record_deferred_dispatch,
    )
    from app.workflows.script_shot_generate import script_shot_generate_workflow

    seen = {}

    async def _fake_run_issue_agent(**kwargs):
        seen["active"] = deferral_active()
        record_deferred_dispatch(
            task_type="shot_generate",
            dbos_workflow_callable=script_shot_generate_workflow,
            dbos_workflow_kwargs={"shot_id": "1"},
            workflow_id="33333333-3333-3333-3333-333333333333",
        )
        return {"content": "done", "outcome": "completed", "reason": None}

    with patch(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        new=_fake_run_issue_agent,
    ):
        out = await il.run_issue_agent_step.__wrapped__(
            {"id": _ISSUE_ID}, "agent-1", _USER_ID
        )

    assert seen["active"] is True
    assert out["outcome"] == "completed"
    assert out["content"] == "done"
    assert len(out["pending_dispatches"]) == 1


@pytest.mark.asyncio
async def test_a_turn_that_dispatches_nothing_reports_an_empty_list():
    async def _fake_run_issue_agent(**kwargs):
        return {"content": "x", "outcome": None, "reason": None}

    with patch(
        "app.services.issues.issue_agent_executor.run_issue_agent",
        new=_fake_run_issue_agent,
    ):
        out = await il.run_issue_agent_step.__wrapped__(
            {"id": _ISSUE_ID}, "agent-1", _USER_ID
        )
    assert out["pending_dispatches"] == []


# ====================================================================== #
# The bodies drain — before routing
# ====================================================================== #


@pytest.mark.asyncio
async def test_reply_body_drains_before_route_finish_outcome():
    order: list[str] = []

    async def _drain(items):
        order.append(f"drain:{len(list(items or []))}")
        return []

    async def _route(*args, **kwargs):
        order.append("route")

    async def _run_turn(**kwargs):
        return {
            "content": "ok",
            "outcome": "completed",
            "reason": None,
            "run_id": None,
            "pending_dispatches": [_record()],
        }

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", _route),
        patch.object(il, "_maybe_fire_subissue_barrier", AsyncMock()),
    ):
        await il._run_reply_turns(
            _ISSUE_ID,
            _USER_ID,
            "go",
            session_id="900",
            acquire=AsyncMock(return_value=True),
            run_turn=_run_turn,
            release=AsyncMock(),
            sleep=AsyncMock(),
            load_issue=AsyncMock(
                return_value={
                    "status": "needs_followup",
                    "execution_state": {"agent_outcome": "needs_input"},
                }
            ),
            set_status=AsyncMock(),
        )

    assert order == ["drain:1", "route"]


@pytest.mark.asyncio
async def test_reply_body_still_dispatches_when_routing_blows_up():
    """Two independent results of one turn. A routing failure that also ate the
    dispatch would leave the shot claimed and its task row queued forever —
    the exact orphan this seam closes."""
    drained: list[list] = []

    async def _drain(items):
        drained.append(list(items or []))
        return []

    async def _route(*args, **kwargs):
        raise RuntimeError("set_status exploded")

    async def _run_turn(**kwargs):
        return {
            "content": "ok",
            "outcome": "completed",
            "reason": None,
            "run_id": None,
            "pending_dispatches": [_record()],
        }

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", _route),
        patch.object(il, "_maybe_fire_subissue_barrier", AsyncMock()),
        pytest.raises(RuntimeError),
    ):
        await il._run_reply_turns(
            _ISSUE_ID,
            _USER_ID,
            "go",
            session_id="900",
            acquire=AsyncMock(return_value=True),
            run_turn=_run_turn,
            release=AsyncMock(),
            sleep=AsyncMock(),
            load_issue=AsyncMock(
                return_value={
                    "status": "needs_followup",
                    "execution_state": {"agent_outcome": "needs_input"},
                }
            ),
            set_status=AsyncMock(),
        )

    assert drained == [[_record()]]


@pytest.mark.asyncio
async def test_reply_body_drains_a_plain_non_resuming_reply_too():
    """A plain reply never routes status — but its turn can still have
    dispatched. Draining only on the resume branch would drop those."""
    drained: list[list] = []

    async def _drain(items):
        drained.append(list(items or []))
        return []

    async def _run_turn(**kwargs):
        return {"content": "ok", "pending_dispatches": [_record()]}

    with patch.object(il, "drain_deferred_dispatches", _drain):
        await il._run_reply_turns(
            _ISSUE_ID,
            _USER_ID,
            "go",
            session_id="900",
            acquire=AsyncMock(return_value=True),
            run_turn=_run_turn,
            release=AsyncMock(),
            sleep=AsyncMock(),
        )

    assert drained == [[_record()]]


@pytest.mark.asyncio
async def test_dispatch_loop_drains_before_route_finish_outcome():
    order: list[str] = []

    async def _drain(items):
        order.append(f"drain:{len(list(items or []))}")
        return []

    async def _route(*args, **kwargs):
        order.append("route")

    async def _run_turn(issue_row, agent_id, user_id, is_continuation=False):
        order.append("turn")
        return {
            "content": "ok",
            "outcome": "completed",
            "reason": None,
            "pending_dispatches": [_record()],
        }

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", _route),
    ):
        await il._run_dispatch_with_continuation(
            _ISSUE_ID,
            {"id": _ISSUE_ID},
            "agent-1",
            _USER_ID,
            run_turn=_run_turn,
            set_status=AsyncMock(),
            load_issue=AsyncMock(return_value={"status": "in_progress"}),
            pending_inbox=AsyncMock(return_value=0),
        )

    assert order == ["turn", "drain:1", "route"]


@pytest.mark.asyncio
async def test_dispatch_loop_drains_every_continuation_turn():
    """Each turn's dispatches go out at the end of THAT turn, not banked until
    the loop ends — a 3-turn dispatch would otherwise sit on the first turn's
    image for minutes."""
    drained: list[int] = []

    async def _drain(items):
        drained.append(len(list(items or [])))
        return []

    turns = iter(
        [
            {"content": "a", "outcome": "continue", "pending_dispatches": [_record()]},
            {"content": "b", "outcome": "completed", "pending_dispatches": []},
        ]
    )

    async def _run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return next(turns)

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", AsyncMock()),
    ):
        await il._run_dispatch_with_continuation(
            _ISSUE_ID,
            {"id": _ISSUE_ID},
            "agent-1",
            _USER_ID,
            run_turn=_run_turn,
            set_status=AsyncMock(),
            load_issue=AsyncMock(return_value={"status": "in_progress"}),
            pending_inbox=AsyncMock(return_value=0),
        )

    assert drained == [1, 0]


@pytest.mark.asyncio
async def test_dispatch_loop_drains_a_post_wake_reply_turn():
    """The needs_input resume runs its turn through ``run_reply``, not
    ``run_turn`` — the same seam, a different injected callable."""
    drained: list[int] = []

    async def _drain(items):
        drained.append(len(list(items or [])))
        return []

    async def _run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "a", "outcome": "needs_input", "reason": "which one?"}

    async def _run_reply(issue_id_, payload):
        return {
            "content": "b",
            "outcome": "completed",
            "pending_dispatches": [_record()],
        }

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", AsyncMock()),
        patch.object(il, "_question_for_park", AsyncMock(return_value=None)),
    ):
        await il._run_dispatch_with_continuation(
            _ISSUE_ID,
            {"id": _ISSUE_ID},
            "agent-1",
            _USER_ID,
            run_turn=_run_turn,
            set_status=AsyncMock(),
            load_issue=AsyncMock(return_value={"status": "in_progress"}),
            pending_inbox=AsyncMock(return_value=0),
            wait_for_input=AsyncMock(return_value={"reply_text": "that one"}),
            mark_waiting=AsyncMock(),
            clear_waiting=AsyncMock(),
            run_reply=_run_reply,
        )

    assert drained == [0, 1]


@pytest.mark.asyncio
async def test_turn_results_without_the_key_drain_nothing():
    """Every injected fake in the pre-existing suites returns a turn dict with
    no ``pending_dispatches``. Those must stay a no-op, not a crash."""
    calls: list = []

    async def _drain(items):
        calls.append(list(items or []))
        return []

    async def _run_turn(issue_row, agent_id, user_id, is_continuation=False):
        return {"content": "ok", "outcome": "completed"}

    with (
        patch.object(il, "drain_deferred_dispatches", _drain),
        patch.object(il, "route_finish_outcome", AsyncMock()),
    ):
        await il._run_dispatch_with_continuation(
            _ISSUE_ID,
            {"id": _ISSUE_ID},
            "agent-1",
            _USER_ID,
            run_turn=_run_turn,
            set_status=AsyncMock(),
            load_issue=AsyncMock(return_value={"status": "in_progress"}),
            pending_inbox=AsyncMock(return_value=0),
        )

    assert calls == [[]]
