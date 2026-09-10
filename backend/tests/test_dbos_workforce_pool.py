"""Unit tests for DbosAgentWorkforcePool — the enqueue path (mocked DBOS
Queue) and the derived in-flight gauge.

``inflight_count`` used to be a process-local counter that only ever
incremented — nothing decremented it when a workflow reached a terminal
state, so it was a monotonically rising number wearing a gauge's name. It is
now an async read of ``task_tracking``; these tests pin that it asks the
repository rather than remembering anything itself."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _patched_queue():
    """Context managers + queue mock shared by the enqueue-path tests."""
    queue_mock = MagicMock()
    queue_mock.enqueue = MagicMock()
    return queue_mock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_on_closed_pool_drops_silently():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    await pool.shutdown()

    queue_mock = _patched_queue()
    with patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock):
        # Should not raise, should not attempt to enqueue.
        await pool.dispatch({"id": str(uuid4()), "agent_id": str(uuid4())})
    queue_mock.enqueue.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_agent_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    queue_mock = _patched_queue()
    with patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock):
        await pool.dispatch({"id": str(uuid4())})  # no agent_id
    queue_mock.enqueue.assert_not_called()
    assert pool.known_agents == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_task_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    queue_mock = _patched_queue()
    with patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock):
        await pool.dispatch({"agent_id": str(uuid4())})  # no id
    queue_mock.enqueue.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_enqueues_with_partition_key_and_workflow_id():
    """Successful enqueue path: queue.enqueue is called once with the
    workflow + task; SetWorkflowID + SetEnqueueOptions wrap the call
    with the agent_id partition + deterministic wf_id."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    agent_id = uuid4()
    task_id = uuid4()
    task = {"id": str(task_id), "agent_id": str(agent_id)}

    queue_mock = _patched_queue()

    with (
        patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock),
        patch("app.services.workforce.dbos_pool.SetWorkflowID") as set_wf,
        patch("app.services.workforce.dbos_pool.SetEnqueueOptions") as set_opts,
    ):
        # Patched context managers must support `with ... :` — make them
        # return MagicMocks that are also context-manager-compatible.
        set_wf.return_value.__enter__ = MagicMock()
        set_wf.return_value.__exit__ = MagicMock(return_value=False)
        set_opts.return_value.__enter__ = MagicMock()
        set_opts.return_value.__exit__ = MagicMock(return_value=False)

        pool = DbosAgentWorkforcePool()
        await pool.dispatch(task)

    # Workflow id deterministic on task_id
    set_wf.assert_called_once_with(f"workforce-{task_id}")
    # Partition key = agent_id str
    set_opts.assert_called_once_with(queue_partition_key=str(agent_id))
    # Queue.enqueue called once with (workflow_callable, task)
    queue_mock.enqueue.assert_called_once()
    args, _ = queue_mock.enqueue.call_args
    assert args[1] is task
    # Agent recorded
    assert agent_id in pool.known_agents


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_duplicate_workflow_id_swallowed():
    """A second enqueue for the same task should be treated as success
    (DBOS already has it) — the workflow id IS the dedup, so a replayed
    dispatch tick costs nothing."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    agent_id = uuid4()
    task_id = uuid4()
    task = {"id": str(task_id), "agent_id": str(agent_id)}

    queue_mock = MagicMock()
    queue_mock.enqueue = MagicMock(side_effect=Exception("workflow already exists"))

    with (
        patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock),
        patch("app.services.workforce.dbos_pool.SetWorkflowID") as set_wf,
        patch("app.services.workforce.dbos_pool.SetEnqueueOptions") as set_opts,
    ):
        set_wf.return_value.__enter__ = MagicMock()
        set_wf.return_value.__exit__ = MagicMock(return_value=False)
        set_opts.return_value.__enter__ = MagicMock()
        set_opts.return_value.__exit__ = MagicMock(return_value=False)

        pool = DbosAgentWorkforcePool()
        # Should NOT raise.
        await pool.dispatch(task)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inflight_count_is_derived_from_the_repository():
    """The gauge reads task_tracking. Nothing about it is process-local, so a
    second pod, a restart, or a workflow that finished elsewhere all show up."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    repo = MagicMock()
    repo.count_inflight_agent_tasks = AsyncMock(return_value=3)

    pool = DbosAgentWorkforcePool()
    with patch(
        "app.repositories.agent_workforce_repository.get_agent_workforce_repository",
        return_value=repo,
    ):
        assert await pool.inflight_count() == 3

    repo.count_inflight_agent_tasks.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inflight_count_does_not_remember_dispatches():
    """A dispatch must not bump any local counter — the old estimate's whole
    failure mode was that it counted enqueues and never counted completions."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    assert not hasattr(pool, "_inflight_estimate")

    repo = MagicMock()
    repo.count_inflight_agent_tasks = AsyncMock(return_value=0)

    queue_mock = _patched_queue()
    with (
        patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock),
        patch("app.services.workforce.dbos_pool.SetWorkflowID"),
        patch("app.services.workforce.dbos_pool.SetEnqueueOptions"),
        patch(
            "app.repositories.agent_workforce_repository."
            "get_agent_workforce_repository",
            return_value=repo,
        ),
    ):
        await pool.dispatch({"id": str(uuid4()), "agent_id": str(uuid4())})
        assert await pool.inflight_count() == 0


@pytest.mark.unit
def test_known_agents_returns_copy():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    agent_id = uuid4()
    pool._known_agents.add(agent_id)
    snapshot = pool.known_agents
    snapshot.add(uuid4())  # mutating the snapshot must not affect pool
    assert len(pool._known_agents) == 1
