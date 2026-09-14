"""GenerateShotImage under a deferred-dispatch collector (harness 3a, Task 2).

Real-stack evidence this closes (3a re-verification, 2026-09-14, run
349427295369155): the issue turn runs inside ``run_issue_reply_step`` /
``run_issue_agent_step`` (``@DBOS.step``), so the handler's
``start_workflow_routed`` hit ``AssertionError: assert cur_ctx.is_workflow()``.
The tool answered ``dispatch_failed``, the shot rolled back to ``empty``, and
the ``task_tracking`` row it had already created (task_type ``shot_generate``,
dbos_workflow_id 857490f7-…) sat ``queued``/``pending`` forever.

The handler's own contract is unchanged — resolve, claim, create the task row,
dispatch. Only the last step becomes "record it for the workflow body", and the
result says so (``deferred``) rather than pretending the image is already on
its way from this call.

The ``@DBOS.step``-vs-``@DBOS.workflow`` distinction is NOT stubbed away with a
fake orchestrator: these drive the real ``start_workflow_routed`` with its
three gates patched at the module level, so the deferral branch is reached
through the same code production reaches it through.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.ai.scope.scope_resolver as resolver_mod
import app.services.ai.scope.scoped_script_gateway as gateway_mod
import app.services.ai.tools.screenwriting_tools as tools_mod
import app.services.infra.dbos_orchestrator as orch
from app.services.ai.scope.agent_run_scope import AgentRunScope
from app.services.ai.tools.screenwriting_tools import SCREENWRITING_HANDLERS
from app.services.infra.deferred_dispatch import collect_deferred_dispatches

_RUN_ID = "800100000000000009"
_USER_ID = "22222222-2222-2222-2222-222222222222"
_PROJECT_A = 900100000000000001
_TEAM_A = 900100000000000003
_SCENE_ID = 700100000000000001
_SHOT_ID = 700100000000000002

_RUN_CONTEXT = {
    "run_id": _RUN_ID,
    "user_id": _USER_ID,
    "team_id": _TEAM_A,
    "agent_id": "00000000-0000-0000-0000-000000000002",
    "turn": 2,
    "step": 5,
}


def _scope() -> AgentRunScope:
    return AgentRunScope(
        run_id=_RUN_ID,
        user_id=_USER_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
    )


def _resolved_shot(status: str = "empty"):
    return resolver_mod.ResolvedShot(
        id=_SHOT_ID,
        scene_id=_SCENE_ID,
        project_id=_PROJECT_A,
        team_id=_TEAM_A,
        episode_id=None,
        shot_number=1,
        shot_type="MS",
        status=status,
    )


def _task_manager_mock(task_id: str = "task-shot-1") -> MagicMock:
    mgr = MagicMock()
    mgr.create = AsyncMock(return_value=task_id)
    return mgr


def _handler_env(fake_dbos: MagicMock, mgr: MagicMock, set_status: AsyncMock):
    """Everything the handler touches EXCEPT start_workflow_routed, which is
    the code under test and runs for real. Returns an entered ``ExitStack``."""
    from app.core.config import settings

    routing = orch.RoutingDecision(task_type="script_shot_generate", mode="dbos")
    patches = (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod, "resolve_shot", AsyncMock(return_value=_resolved_shot())
        ),
        patch.object(gateway_mod, "set_shot_status", set_status),
        patch.object(orch, "get_routing", AsyncMock(return_value=routing)),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=mgr,
        ),
    )
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


@pytest.mark.asyncio
async def test_under_a_collector_the_dispatch_is_recorded_not_started():
    fake_dbos = MagicMock()
    mgr = _task_manager_mock()
    set_status = AsyncMock()
    with _handler_env(fake_dbos, mgr, set_status):
        async with collect_deferred_dispatches() as pending:
            result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
                {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
            )

    # The defect: DBOS.start_workflow inside a step. It must not be reached.
    fake_dbos.start_workflow.assert_not_called()

    assert result["ok"] is True
    assert result["dispatched"] is True
    assert result["deferred"] is True
    assert result["task_id"] == "task-shot-1"
    assert result["shot_id"] == str(_SHOT_ID)
    assert "queued at the end of this step" in result["note"]

    # The shot stays claimed — nothing rolled it back to 'empty'.
    assert [c.args[2] for c in set_status.call_args_list] == ["generating"]

    assert len(pending) == 1
    record = pending[0]
    assert record["task_type"] == "script_shot_generate"
    assert (
        record["workflow"]
        == "app.workflows.script_shot_generate:script_shot_generate_workflow"
    )
    assert record["kwargs"]["shot_id"] == str(_SHOT_ID)
    assert record["kwargs"]["user_id"] == _USER_ID
    assert record["kwargs"]["turn"] == 2
    assert record["kwargs"]["step"] == 5
    # The task row and the deferred record must name the SAME workflow id, or
    # the row is orphaned exactly as it was before this fix.
    assert record["workflow_id"] == mgr.create.await_args.kwargs["dbos_workflow_id"]
    assert record["task_id"] == "task-shot-1"


@pytest.mark.asyncio
async def test_the_deferred_record_carries_the_task_row_so_a_bad_drain_can_fail_it():
    """``task_id`` is the handler's only way to hand the body what it needs to
    close the orphan. It rides on the record, never into the workflow kwargs."""
    fake_dbos = MagicMock()
    mgr = _task_manager_mock("task-shot-2")
    with _handler_env(fake_dbos, mgr, AsyncMock()):
        async with collect_deferred_dispatches() as pending:
            await SCREENWRITING_HANDLERS["GenerateShotImage"](
                {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
            )

    assert pending[0]["task_id"] == "task-shot-2"
    assert "task_id" not in pending[0]["kwargs"]


@pytest.mark.asyncio
async def test_without_a_collector_the_behavior_is_unchanged():
    """The chat path and the human REST path have no collector: the workflow
    starts immediately and the result carries no ``deferred`` flag."""
    handle = MagicMock(workflow_id="55555555-5555-5555-5555-555555555555")
    fake_dbos = MagicMock()
    fake_dbos.start_workflow = MagicMock(return_value=handle)
    mgr = _task_manager_mock("task-shot-3")
    with _handler_env(fake_dbos, mgr, AsyncMock()):
        result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
            {"shot_id": str(_SHOT_ID)}, _RUN_CONTEXT
        )

    fake_dbos.start_workflow.assert_called_once()
    assert result["ok"] is True
    assert result["dispatched"] is True
    assert "deferred" not in result
    assert "dispatched asynchronously" in result["note"]


@pytest.mark.asyncio
async def test_a_refused_deferral_still_rolls_the_shot_back():
    """A record that cannot cross the step boundary is a dispatch failure like
    any other — typed ``dispatch_failed``, shot back to 'empty'. Reporting
    success here would strand the shot at 'generating' with nothing coming."""
    from app.core.config import settings

    fake_dbos = MagicMock()
    mgr = _task_manager_mock("task-shot-4")
    set_status = AsyncMock()
    routing = orch.RoutingDecision(task_type="script_shot_generate", mode="dbos")

    class _Unserialisable:
        pass

    ctx = {**_RUN_CONTEXT, "turn": _Unserialisable()}
    with (
        patch.object(settings, "FEATURE_SHOT_GENERATE", True),
        patch.object(tools_mod, "scope_for_run", AsyncMock(return_value=_scope())),
        patch.object(
            tools_mod, "resolve_shot", AsyncMock(return_value=_resolved_shot())
        ),
        patch.object(gateway_mod, "set_shot_status", set_status),
        patch.object(orch, "get_routing", AsyncMock(return_value=routing)),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=mgr,
        ),
    ):
        async with collect_deferred_dispatches() as pending:
            result = await SCREENWRITING_HANDLERS["GenerateShotImage"](
                {"shot_id": str(_SHOT_ID)}, ctx
            )

    assert pending == []
    assert result["ok"] is False
    assert result["error_code"] == "dispatch_failed"
    assert [c.args[2] for c in set_status.call_args_list] == ["generating", "empty"]
