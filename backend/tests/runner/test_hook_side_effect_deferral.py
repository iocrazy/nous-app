"""A hook's ``side_effect`` dispatch survives being fired from inside a step.

``AgentRunner._dispatch_side_effect`` invokes a zero-arg closure the hook owner
built. Production's memory_harvester closure (``ai_library_chat_wiring``) calls
``start_workflow_routed`` through ``run_async``, and on an issue turn that whole
chain runs inside ``run_issue_reply_step`` / ``run_issue_agent_step`` — where
DBOS refuses ``start_workflow``. The symptom was one
``[hook:memory_harvester] side_effect dispatch failed`` per turn, forever,
because ``_dispatch_side_effect`` swallows the exception by design.

So "it did not raise" is NOT the assertion here (it never raised). The
assertion is that the dispatch was RECORDED — the only observable difference
between a harvest that will happen and one that was silently dropped.

``run_async``'s thread hop is deliberately exercised rather than stubbed: it
runs the coroutine on a worker thread under ``contextvars.copy_context()``, and
a copied context propagates the collector's *list object* but would not
propagate a re-``set()`` of the ContextVar. That is precisely why the collector
holds a mutable list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.infra.dbos_orchestrator as orch
from app.services.ai.runner.agent_runner import AgentRunner
from app.services.infra.deferred_dispatch import collect_deferred_dispatches
from app.services.infra.hooks import HookResult


def _memory_harvester_shaped_side_effect():
    """The production closure's shape, verbatim in the parts that matter:
    ``run_async`` + ``start_workflow_routed`` + ``write_memory_workflow``."""
    from app.tasks.utils import run_async
    from app.workflows.write_memory import write_memory_workflow

    def _fire() -> None:
        run_async(
            orch.start_workflow_routed(
                "memory_tasks",
                dbos_workflow_callable=write_memory_workflow,
                dbos_workflow_kwargs={
                    "agent_id": "agent-1",
                    "user_id": "22222222-2222-2222-2222-222222222222",
                    "session_id": None,
                    "run_id": "800100000000000009",
                    "iteration": 1,
                    "tool_name": "Skill",
                },
            )
        )

    return _fire


@pytest.mark.asyncio
async def test_memory_harvester_side_effect_is_recorded_not_dropped():
    fake_dbos = MagicMock()
    routing = orch.RoutingDecision(task_type="memory_tasks", mode="dbos")
    with (
        patch.object(orch, "get_routing", AsyncMock(return_value=routing)),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
    ):
        async with collect_deferred_dispatches() as pending:
            AgentRunner._dispatch_side_effect(
                "memory_harvester",
                HookResult(
                    decision="continue",
                    side_effect=_memory_harvester_shaped_side_effect(),
                ),
            )

    fake_dbos.start_workflow.assert_not_called()
    assert len(pending) == 1
    assert pending[0]["workflow"] == (
        "app.workflows.write_memory:write_memory_workflow"
    )
    assert pending[0]["task_type"] == "memory_tasks"
    assert pending[0]["kwargs"]["run_id"] == "800100000000000009"
    assert pending[0]["workflow_id"]


@pytest.mark.asyncio
async def test_side_effect_outside_a_collector_still_dispatches_immediately():
    """The chat path has no collector — the harvest must still fire the moment
    the hook returns, exactly as it always has."""
    handle = MagicMock(workflow_id="44444444-4444-4444-4444-444444444444")
    fake_dbos = MagicMock()
    fake_dbos.start_workflow = MagicMock(return_value=handle)
    routing = orch.RoutingDecision(task_type="memory_tasks", mode="dbos")
    with (
        patch.object(orch, "get_routing", AsyncMock(return_value=routing)),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
    ):
        AgentRunner._dispatch_side_effect(
            "memory_harvester",
            HookResult(
                decision="continue",
                side_effect=_memory_harvester_shaped_side_effect(),
            ),
        )

    fake_dbos.start_workflow.assert_called_once()
