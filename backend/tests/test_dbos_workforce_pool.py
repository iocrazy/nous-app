"""Unit tests for DbosAgentWorkforcePool — interface parity with
AgentWorkerPool, plus enqueue path verification (mocked DBOS Queue)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_on_closed_pool_drops_silently():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    await pool.shutdown()

    # Should not raise, should not attempt to enqueue.
    await pool.dispatch({"id": str(uuid4()), "agent_id": str(uuid4())})
    assert pool.inflight_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_agent_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    await pool.dispatch({"id": str(uuid4())})  # no agent_id
    assert pool.inflight_count == 0
    assert pool.known_agents == set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_missing_task_id_logs_and_returns():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    await pool.dispatch({"agent_id": str(uuid4())})  # no id
    assert pool.inflight_count == 0


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

    queue_mock = MagicMock()
    queue_mock.enqueue = MagicMock()

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
    # Inflight estimate bumped
    assert pool.inflight_count == 1
    # Agent recorded
    assert agent_id in pool.known_agents


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_duplicate_workflow_id_swallowed():
    """A second enqueue for the same task should be treated as success
    (DBOS already has it). Counter does NOT bump on the duplicate."""
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

    # Counter NOT bumped — duplicate was swallowed BEFORE the bump line
    # in the source. (See dbos_pool.py: bump is inside the try block.)
    assert pool.inflight_count == 0


@pytest.mark.unit
def test_inflight_count_property_returns_estimate():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    assert pool.inflight_count == 0
    pool._inflight_estimate = 3
    assert pool.inflight_count == 3
    # Defensive non-negative clamp
    pool._inflight_estimate = -5
    assert pool.inflight_count == 0


@pytest.mark.unit
def test_known_agents_returns_copy():
    from app.services.workforce.dbos_pool import DbosAgentWorkforcePool

    pool = DbosAgentWorkforcePool()
    agent_id = uuid4()
    pool._known_agents.add(agent_id)
    snapshot = pool.known_agents
    snapshot.add(uuid4())  # mutating the snapshot must not affect pool
    assert len(pool._known_agents) == 1
