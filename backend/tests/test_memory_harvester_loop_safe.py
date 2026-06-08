"""Regression: the memory-harvester dispatch closure must be loop-safe.

AgentRunner._run_post_hooks (async) invokes the HookResult.side_effect closure
ON the event loop. The closure dispatches the memory-write workflow. It used to
call **bare `asyncio.run(...)`**, which raises `RuntimeError: asyncio.run()
cannot be called from a running event loop` under the runner's loop — silently
swallowed by `_dispatch_side_effect`, so every memory-harvest was dropped. The
fix routes it through the loop-safe `run_async` helper.

This test drives the REAL closure (via the wired MemoryHarvesterHook's
signature_factory) from inside a running loop and asserts it dispatches without
raising — i.e. it would FAIL against the old bare-asyncio.run code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.chat.ai_library_chat_wiring import build_agent_runner_stack

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _agent():
    return {
        "id": str(uuid4()),
        "slug": "script_ai",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }


async def test_memory_harvester_dispatch_survives_running_loop():
    dispatched: dict = {}

    async def _fake_start_workflow_routed(task_type, **kw):
        dispatched["task_type"] = task_type
        dispatched["wf_kwargs"] = kw.get("dbos_workflow_kwargs")
        return {"mode": "dbos"}

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_wiring._safe_recall_memories",
            AsyncMock(return_value=[]),
        ),
        patch(
            # The closure does a LOCAL import of start_workflow_routed at wiring
            # time, so patch the source module BEFORE building the stack.
            "app.services.infra.dbos_orchestrator.start_workflow_routed",
            _fake_start_workflow_routed,
        ),
    ):
        stack = await build_agent_runner_stack(
            agent=_agent(),
            skill_repo=MagicMock(),
            user_id=uuid4(),
            session_id=uuid4(),
            user_query="hi",
            settings=MagicMock(),
        )

        mh = next(
            e.hook
            for e in stack.runner.hooks.get_post_hooks()
            if e.name == "memory_harvester"
        )
        assert mh.signature_factory is not None

        # The real _fire closure (sync, zero-arg) — what AgentRunner invokes.
        fire = mh.signature_factory(
            run_id="r1",
            agent_id="a1",
            user_id="u1",
            session_id="s1",
            iteration=1,
            tool_name="t1",
        )

        # Invoke it from THIS test's running event loop. Bare asyncio.run would
        # raise here; the loop-safe run_async hops to a worker thread instead.
        fire()  # must NOT raise

    assert dispatched["task_type"] == "memory_tasks"
    assert dispatched["wf_kwargs"]["agent_id"] == "a1"
    assert dispatched["wf_kwargs"]["run_id"] == "r1"
