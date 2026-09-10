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
        dropped = await pool.dispatch({"id": str(uuid4()), "agent_id": str(uuid4())})
    queue_mock.enqueue.assert_not_called()
    # None, so the caller does not stamp a dispatch that never happened.
    assert dropped is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_agent_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    queue_mock = _patched_queue()
    with patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock):
        assert await pool.dispatch({"id": str(uuid4())}) is None  # no agent_id
    queue_mock.enqueue.assert_not_called()
    assert pool.known_agents == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_task_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    queue_mock = _patched_queue()
    with patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock):
        assert await pool.dispatch({"agent_id": str(uuid4())}) is None  # no id
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
    task = {"id": str(task_id), "agent_id": str(agent_id), "dispatch_attempt": 0}

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
        record = await pool.dispatch(task)

    # Workflow id is scoped to the ATTEMPT, not just the task.
    set_wf.assert_called_once_with(f"workforce-{task_id}-1")
    # Partition key = agent_id str
    set_opts.assert_called_once_with(queue_partition_key=str(agent_id))
    # Queue.enqueue called once with (workflow_callable, task)
    queue_mock.enqueue.assert_called_once()
    args, _ = queue_mock.enqueue.call_args
    assert args[1] is task
    # Agent recorded
    assert agent_id in pool.known_agents
    # The caller gets back exactly what was enqueued, so the same values can be
    # persisted — the stored id must equal the enqueued id or the replay
    # re-entry check in claim_task compares against the wrong thing.
    assert record is not None
    assert record.workflow_id == f"workforce-{task_id}-1"
    assert record.attempt == 1


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_requeued_task_gets_a_workflow_id_dbos_has_never_seen():
    """The whole point of the attempt suffix.

    Re-enqueuing a workflow id DBOS already holds does NOT re-run it: the
    status row is upserted with only recovery_attempts/updated_at, keeps its
    terminal status, and DBOS answers should_execute=False. Its dedup means
    NEVER AGAIN, not "exactly once". So a task that came back through requeue
    must arrive under a fresh id, or it is enqueued forever and run never —
    while the dispatch loop counts it as sent."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    task_id = uuid4()
    agent_id = uuid4()
    # attempt 1 already went out and died; requeue kept the counter.
    task = {"id": str(task_id), "agent_id": str(agent_id), "dispatch_attempt": 1}

    queue_mock = _patched_queue()
    with (
        patch("app.workflows.agent_workforce.agent_workforce_queue", queue_mock),
        patch("app.services.workforce.dbos_pool.SetWorkflowID") as set_wf,
        patch("app.services.workforce.dbos_pool.SetEnqueueOptions") as set_opts,
    ):
        set_wf.return_value.__enter__ = MagicMock()
        set_wf.return_value.__exit__ = MagicMock(return_value=False)
        set_opts.return_value.__enter__ = MagicMock()
        set_opts.return_value.__exit__ = MagicMock(return_value=False)

        record = await DbosAgentWorkforcePool().dispatch(task)

    set_wf.assert_called_once_with(f"workforce-{task_id}-2")
    assert record.attempt == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_same_attempt_redispatch_reuses_the_id_so_dbos_drops_it():
    """The other direction: a replayed dispatch tick must NOT invent a new id.

    Same attempt → same id → DBOS recognises the duplicate and does not start a
    second run. Deduplication is wanted HERE; it is only fatal when the task
    genuinely needs to run again, which is what the attempt counter separates."""
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    task_id = uuid4()
    task = {"id": str(task_id), "agent_id": str(uuid4()), "dispatch_attempt": 4}

    seen = []
    queue_mock = _patched_queue()
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
        seen.append(await pool.dispatch(task))
        seen.append(await pool.dispatch(task))

    assert seen[0].workflow_id == seen[1].workflow_id == f"workforce-{task_id}-5"
