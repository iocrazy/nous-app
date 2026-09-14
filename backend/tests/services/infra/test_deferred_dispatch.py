"""The "step reports, body dispatches" seam (harness 3a, Task 2).

DBOS refuses ``DBOS.start_workflow`` from inside a ``@DBOS.step``. An issue
turn runs inside one, so a tool or hook that dispatches during the turn used to
raise ``AssertionError: assert cur_ctx.is_workflow()`` and leave its
``task_tracking`` row queued forever. ``deferred_dispatch`` collects those
dispatches inside the step and performs them from the workflow body.

Two properties carry the weight here and each has its own test:

* the record CROSSES A STEP BOUNDARY, so it must be JSON-serialisable and must
  not carry a live callable;
* the callable named by that record is resolved ONLY to something
  ``app.workflows._dispatch_bundle`` exports. The string arrives from a step's
  return value; without the allowlist it names any importable callable in the
  process. ``test_drain_refuses_a_callable_outside_the_dispatch_bundle`` points
  at a real, importable, definitely-not-a-workflow callable on purpose — a
  fake-name test would still pass with the allowlist deleted.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.infra.deferred_dispatch import (
    DeferredDispatchError,
    collect_deferred_dispatches,
    deferral_active,
    drain_deferred_dispatches,
    record_deferred_dispatch,
    workflow_ref,
)

_WF_ID = "11111111-1111-1111-1111-111111111111"


def _bundle_workflow():
    from app.workflows.write_memory import write_memory_workflow

    return write_memory_workflow


# ====================================================================== #
# Collection
# ====================================================================== #


@pytest.mark.asyncio
async def test_no_collector_means_no_deferral():
    assert deferral_active() is False


@pytest.mark.asyncio
async def test_collector_is_active_only_inside_the_block():
    async with collect_deferred_dispatches() as pending:
        assert deferral_active() is True
        assert pending == []
    assert deferral_active() is False


@pytest.mark.asyncio
async def test_recorded_dispatch_is_json_serialisable_and_carries_no_callable():
    wf = _bundle_workflow()
    async with collect_deferred_dispatches() as pending:
        record = record_deferred_dispatch(
            task_type="memory_tasks",
            dbos_workflow_callable=wf,
            dbos_workflow_kwargs={"user_id": "u1", "iteration": 3},
            workflow_id=_WF_ID,
            task_id="task-9",
        )
    assert pending == [record]
    # The whole point: this survives a DBOS step's return value.
    assert json.loads(json.dumps(record)) == record
    assert record["workflow"] == "app.workflows.write_memory:write_memory_workflow"
    assert record["workflow_id"] == _WF_ID
    assert record["task_id"] == "task-9"
    assert record["kwargs"] == {"user_id": "u1", "iteration": 3}


@pytest.mark.asyncio
async def test_non_serialisable_kwargs_are_refused_at_record_time():
    """Refused while the originator can still roll back — not silently at drain,
    when the shot is already claimed and the task row already exists."""

    class _Opaque:
        pass

    with pytest.raises(DeferredDispatchError):
        async with collect_deferred_dispatches():
            record_deferred_dispatch(
                task_type="memory_tasks",
                dbos_workflow_callable=_bundle_workflow(),
                dbos_workflow_kwargs={"blob": _Opaque()},
                workflow_id=_WF_ID,
            )


@pytest.mark.asyncio
async def test_recording_without_a_collector_raises():
    with pytest.raises(DeferredDispatchError):
        record_deferred_dispatch(
            task_type="memory_tasks",
            dbos_workflow_callable=_bundle_workflow(),
            dbos_workflow_kwargs={},
            workflow_id=_WF_ID,
        )


def test_workflow_ref_round_trips_through_the_bundle():
    wf = _bundle_workflow()
    assert workflow_ref(wf) == "app.workflows.write_memory:write_memory_workflow"


# ====================================================================== #
# Drain
# ====================================================================== #


@pytest.mark.asyncio
async def test_drain_starts_each_record_with_the_resolved_callable():
    wf = _bundle_workflow()
    records = [
        {
            "task_type": "memory_tasks",
            "workflow": workflow_ref(wf),
            "kwargs": {"user_id": "u1"},
            "workflow_id": _WF_ID,
            "task_id": None,
        }
    ]
    dispatch = AsyncMock(return_value={"mode": "dbos", "dbos_workflow_id": _WF_ID})
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", new=dispatch
    ):
        started = await drain_deferred_dispatches(records)

    dispatch.assert_awaited_once()
    args, kwargs = dispatch.await_args
    assert args[0] == "memory_tasks"
    assert kwargs["dbos_workflow_callable"] is wf
    assert kwargs["dbos_workflow_kwargs"] == {"user_id": "u1"}
    assert kwargs["workflow_id"] == _WF_ID
    assert started == [{"mode": "dbos", "dbos_workflow_id": _WF_ID}]


@pytest.mark.asyncio
async def test_drain_of_nothing_is_a_no_op():
    dispatch = AsyncMock()
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", new=dispatch
    ):
        assert await drain_deferred_dispatches(None) == []
        assert await drain_deferred_dispatches([]) == []
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_drain_refuses_a_callable_outside_the_dispatch_bundle():
    """``os.system`` is importable and callable — everything the resolver needs
    EXCEPT membership in the reviewed dispatch bundle. Delete the allowlist and
    this dispatches it."""
    mgr = MagicMock()
    mgr.fail = AsyncMock()
    dispatch = AsyncMock()
    records = [
        {
            "task_type": "shot_generate",
            "workflow": "os:system",
            "kwargs": {"command": "echo pwned"},
            "workflow_id": _WF_ID,
            "task_id": "task-refused",
        }
    ]
    with (
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed", new=dispatch
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=mgr,
        ),
    ):
        started = await drain_deferred_dispatches(records)

    assert started == []
    dispatch.assert_not_awaited()
    # The orphan this module exists to close: a refused record must not leave
    # its task row queued forever.
    mgr.fail.assert_awaited_once()
    assert mgr.fail.await_args.args[0] == "task-refused"


@pytest.mark.asyncio
async def test_drain_refuses_a_malformed_reference():
    dispatch = AsyncMock()
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", new=dispatch
    ):
        assert (
            await drain_deferred_dispatches(
                [
                    {
                        "task_type": "shot_generate",
                        "workflow": "not-a-reference",
                        "kwargs": {},
                        "workflow_id": _WF_ID,
                    }
                ]
            )
            == []
        )
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_failing_record_neither_aborts_the_drain_nor_the_body():
    wf = _bundle_workflow()
    mgr = MagicMock()
    mgr.fail = AsyncMock()

    calls: list[str] = []

    async def _dispatch(task_type, **kwargs):
        calls.append(task_type)
        if task_type == "boom":
            raise RuntimeError("routing says off")
        return {"mode": "dbos", "task_type": task_type}

    records = [
        {
            "task_type": "boom",
            "workflow": workflow_ref(wf),
            "kwargs": {},
            "workflow_id": _WF_ID,
            "task_id": "task-boom",
        },
        {
            "task_type": "memory_tasks",
            "workflow": workflow_ref(wf),
            "kwargs": {},
            "workflow_id": "22222222-2222-2222-2222-222222222222",
            "task_id": None,
        },
    ]
    with (
        patch(
            "app.services.infra.dbos_orchestrator.start_workflow_routed", new=_dispatch
        ),
        patch(
            "app.services.infra.unified_task_manager.get_task_manager",
            return_value=mgr,
        ),
    ):
        started = await drain_deferred_dispatches(records)

    assert calls == ["boom", "memory_tasks"]
    assert started == [{"mode": "dbos", "task_type": "memory_tasks"}]
    mgr.fail.assert_awaited_once()
    assert mgr.fail.await_args.args[0] == "task-boom"


@pytest.mark.asyncio
async def test_drain_does_not_feed_an_enclosing_collector():
    """A body that drains while some outer collector is still installed must
    actually dispatch, not re-defer the records it is draining."""
    wf = _bundle_workflow()
    dispatch = AsyncMock(return_value={"mode": "dbos"})
    records = [
        {
            "task_type": "memory_tasks",
            "workflow": workflow_ref(wf),
            "kwargs": {},
            "workflow_id": _WF_ID,
        }
    ]
    with patch(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", new=dispatch
    ):
        async with collect_deferred_dispatches() as outer:
            await drain_deferred_dispatches(records)
            assert deferral_active() is True  # restored on the way out
    assert outer == []
    dispatch.assert_awaited_once()


# ====================================================================== #
# start_workflow_routed's own deferral branch
# ====================================================================== #


@pytest.mark.asyncio
async def test_start_workflow_routed_defers_instead_of_starting_dbos():
    import app.services.infra.dbos_orchestrator as orch

    wf = _bundle_workflow()
    fake_dbos = MagicMock()
    with (
        patch.object(
            orch,
            "get_routing",
            AsyncMock(
                return_value=orch.RoutingDecision(task_type="memory_tasks", mode="dbos")
            ),
        ),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
    ):
        async with collect_deferred_dispatches() as pending:
            out = await orch.start_workflow_routed(
                "memory_tasks",
                dbos_workflow_callable=wf,
                dbos_workflow_kwargs={"user_id": "u1"},
                workflow_id=_WF_ID,
                task_id="task-7",
            )

    fake_dbos.start_workflow.assert_not_called()
    assert out["deferred"] is True
    assert out["mode"] == "dbos"
    assert out["task_type"] == "memory_tasks"
    assert out["dbos_workflow_id"] == _WF_ID
    assert len(pending) == 1
    assert pending[0]["workflow"] == workflow_ref(wf)
    assert pending[0]["task_id"] == "task-7"


@pytest.mark.asyncio
async def test_deferred_dispatch_without_an_explicit_id_still_gets_one():
    """The caller's task_tracking row is keyed by dbos_workflow_id — a deferred
    dispatch that answered ``None`` would orphan it just as thoroughly as the
    AssertionError did."""
    import app.services.infra.dbos_orchestrator as orch

    wf = _bundle_workflow()
    with (
        patch.object(
            orch,
            "get_routing",
            AsyncMock(
                return_value=orch.RoutingDecision(task_type="memory_tasks", mode="dbos")
            ),
        ),
        patch.object(orch, "is_enabled", lambda: True),
    ):
        async with collect_deferred_dispatches() as pending:
            out = await orch.start_workflow_routed(
                "memory_tasks",
                dbos_workflow_callable=wf,
                dbos_workflow_kwargs={},
            )

    assert out["dbos_workflow_id"]
    assert pending[0]["workflow_id"] == out["dbos_workflow_id"]


@pytest.mark.asyncio
async def test_routing_gate_still_refuses_before_any_deferral():
    """Deferral is inserted AFTER the three gates, so a killed routing row
    refuses at the same place it always did instead of queueing work the body
    would then dispatch."""
    import app.services.infra.dbos_orchestrator as orch

    with patch.object(
        orch,
        "get_routing",
        AsyncMock(
            return_value=orch.RoutingDecision(task_type="memory_tasks", mode="off")
        ),
    ):
        async with collect_deferred_dispatches() as pending:
            with pytest.raises(RuntimeError):
                await orch.start_workflow_routed(
                    "memory_tasks",
                    dbos_workflow_callable=_bundle_workflow(),
                    dbos_workflow_kwargs={},
                )
    assert pending == []


@pytest.mark.asyncio
async def test_no_collector_leaves_the_immediate_path_untouched():
    """Chat-path turns have no collector: dispatch still goes straight to
    DBOS.start_workflow, as it always did."""
    import app.services.infra.dbos_orchestrator as orch

    handle = MagicMock(workflow_id=_WF_ID)
    fake_dbos = MagicMock()
    fake_dbos.start_workflow = MagicMock(return_value=handle)
    with (
        patch.object(
            orch,
            "get_routing",
            AsyncMock(
                return_value=orch.RoutingDecision(task_type="memory_tasks", mode="dbos")
            ),
        ),
        patch.object(orch, "is_enabled", lambda: True),
        patch("dbos.DBOS", fake_dbos),
    ):
        out = await orch.start_workflow_routed(
            "memory_tasks",
            dbos_workflow_callable=_bundle_workflow(),
            dbos_workflow_kwargs={},
        )

    fake_dbos.start_workflow.assert_called_once()
    assert out == {
        "mode": "dbos",
        "task_type": "memory_tasks",
        "dbos_workflow_id": _WF_ID,
    }
    assert "deferred" not in out
