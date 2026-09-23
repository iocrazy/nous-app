"""input_gate.release_parked_workflow — the reaper's recipe, callable by the
fork endpoint, the cancel hook and the preempted-wait reaper.

Order is cancel → marker → lock (hotfix-2 ruling 2, defect H): the marker and
the lock are only let go once the workflow is really cancelled. The old order
(marker first) stranded production issue 352701793481310: the API process has
no DBOS singleton, the cancel raised after the marker was already gone, the
lock stayed held, and the stale-wait reaper could no longer see the workflow.
"""

from unittest.mock import AsyncMock, patch

import pytest
from dbos._error import DBOSException

from app.agent_framework import input_gate as g
from app.services.infra import dbos_orchestrator as orch

pytestmark = pytest.mark.unit


def _record(order):
    async def _clear(*, workflow_id):
        order.append(("clear", workflow_id))

    async def _unlock(wf):
        order.append(("unlock", wf))

    return _clear, _unlock


async def test_release_cancels_then_clears_marker_then_unlocks():
    order = []
    _clear, _unlock = _record(order)

    async def _cancel(wf):
        order.append(("cancel", wf))

    with (
        patch.object(g, "clear_awaiting_input", _clear),
        patch.object(g, "_cancel_workflow", _cancel),
        patch.object(g, "_clear_issue_lock", _unlock),
    ):
        await g.release_parked_workflow("wf-1")
    assert order == [("cancel", "wf-1"), ("clear", "wf-1"), ("unlock", "wf-1")]


async def test_a_failed_cancel_leaves_marker_and_lock_and_raises_typed():
    order = []
    _clear, _unlock = _record(order)
    with (
        patch.object(g, "clear_awaiting_input", _clear),
        patch.object(
            g, "_cancel_workflow", AsyncMock(side_effect=RuntimeError("engine down"))
        ),
        patch.object(g, "_clear_issue_lock", _unlock),
    ):
        with pytest.raises(g.ParkedReleaseError):
            await g.release_parked_workflow("wf-1")
    assert order == []


async def test_a_failed_marker_clear_after_cancel_is_logged_not_raised():
    """The workflow is already CANCELLED: the release succeeded. A residual
    marker is logged at ERROR, the lock is still released, nothing raises."""
    unlock = AsyncMock()
    with (
        patch.object(g, "_cancel_workflow", AsyncMock()),
        patch.object(
            g, "clear_awaiting_input", AsyncMock(side_effect=RuntimeError("db"))
        ),
        patch.object(g, "_clear_issue_lock", unlock),
        patch.object(g.logger, "error") as err,
    ):
        await g.release_parked_workflow("wf-1")
    unlock.assert_awaited_once_with("wf-1")
    msgs = [str(c.args[0]) for c in err.call_args_list]
    assert any("CANCELLED" in m and "awaiting_input" in m for m in msgs), msgs


# ── the API-process shape (nous-backend, NOUS_ROLE=gateway) ─────────────────


def _no_singleton():
    """``DBOS.cancel_workflow_async`` in a process that never launched DBOS."""
    return AsyncMock(side_effect=DBOSException("No DBOS was created yet"))


class _FakeClient:
    def __init__(self):
        self.cancelled = []

    async def cancel_workflow_async(self, workflow_id):
        self.cancelled.append(workflow_id)


async def test_api_shape_cancels_through_the_client_then_releases(monkeypatch):
    """The gateway holds a DBOSClient, not the singleton. The cancel must go
    through the client; then marker and lock are released."""
    client = _FakeClient()
    monkeypatch.setattr(orch, "_dbos", None)
    monkeypatch.setattr(orch, "_launched", False)
    monkeypatch.setattr(orch, "_client", client)
    order = []
    _clear, _unlock = _record(order)
    with (
        patch("dbos.DBOS.cancel_workflow_async", _no_singleton()),
        patch.object(g, "clear_awaiting_input", _clear),
        patch.object(g, "_clear_issue_lock", _unlock),
    ):
        await g.release_parked_workflow("wf-parked")
    assert client.cancelled == ["wf-parked"]
    assert order == [("clear", "wf-parked"), ("unlock", "wf-parked")]


async def test_api_shape_without_any_handle_keeps_marker_and_lock(monkeypatch):
    """No client could be built either: the singleton is NOT pretended into
    (it raises 'No DBOS was created yet'), marker and lock stay for the worker
    reaper, and the failure is typed."""
    monkeypatch.setattr(orch, "_dbos", None)
    monkeypatch.setattr(orch, "_launched", False)
    monkeypatch.setattr(orch, "_client", None)
    monkeypatch.setattr(orch, "init_dbos_client", lambda: None)
    order = []
    _clear, _unlock = _record(order)
    with (
        patch("dbos.DBOS.cancel_workflow_async", _no_singleton()),
        patch.object(g, "clear_awaiting_input", _clear),
        patch.object(g, "_clear_issue_lock", _unlock),
    ):
        with pytest.raises(g.ParkedReleaseError):
            await g.release_parked_workflow("wf-parked")
    assert order == []


async def test_api_shape_builds_the_client_lazily_when_startup_did_not(monkeypatch):
    """Startup construction can fail (DB blip); the first release retries it."""
    client = _FakeClient()
    monkeypatch.setenv("NOUS_ROLE", "gateway")
    monkeypatch.setattr(orch, "_dbos", None)
    monkeypatch.setattr(orch, "_launched", False)
    monkeypatch.setattr(orch, "_client", None)

    def _init():
        orch._client = client

    monkeypatch.setattr(orch, "init_dbos_client", _init)
    with (
        patch("dbos.DBOS.cancel_workflow_async", _no_singleton()),
        patch.object(g, "clear_awaiting_input", AsyncMock()),
        patch.object(g, "_clear_issue_lock", AsyncMock()),
    ):
        await g.release_parked_workflow("wf-parked")
    assert client.cancelled == ["wf-parked"]


async def test_worker_shape_uses_the_launched_singleton(monkeypatch):
    monkeypatch.setattr(orch, "_dbos", object())
    monkeypatch.setattr(orch, "_launched", True)
    monkeypatch.setattr(orch, "_client", None)
    singleton = AsyncMock()
    with patch("dbos.DBOS.cancel_workflow_async", singleton):
        await g._cancel_workflow("wf-w")
    singleton.assert_awaited_once_with("wf-w")


async def test_worker_with_a_failed_launch_does_not_grow_a_client(monkeypatch):
    """A singleton-role process must not construct a client: ``_client``
    flips dispatch onto the enqueue path and makes readyz report healthy."""
    monkeypatch.setattr(orch, "_dbos", object())
    monkeypatch.setattr(orch, "_launched", False)
    monkeypatch.setattr(orch, "_client", None)
    built = []
    monkeypatch.setattr(orch, "init_dbos_client", lambda: built.append(1))
    with pytest.raises(g.ParkedReleaseError):
        await g._cancel_workflow("wf-w")
    assert built == []


# ── reapers ────────────────────────────────────────────────────────────────


async def test_reaper_goes_through_the_same_helper():
    rel = AsyncMock()
    rows = [
        {"dbos_workflow_id": "wf-old", "application_version": "v1"},
        {"dbos_workflow_id": "wf-new", "application_version": "v2"},
    ]
    with (
        patch.object(g, "_fetch_awaiting_rows", AsyncMock(return_value=rows)),
        patch.object(g, "release_parked_workflow", rel),
    ):
        assert await g.reap_stale_input_waits(current_version="v2") == 1
    rel.assert_awaited_once_with("wf-old")


async def test_preempted_reaper_releases_every_row_it_is_given():
    rel = AsyncMock()
    rows = [
        {"dbos_workflow_id": "wf-a", "issue_id": 1, "status": "cancelled"},
        {"dbos_workflow_id": "wf-b", "issue_id": 2, "status": "done"},
    ]
    with (
        patch.object(g, "_fetch_preempted_awaiting_rows", AsyncMock(return_value=rows)),
        patch.object(g, "release_parked_workflow", rel),
    ):
        assert await g.reap_preempted_input_waits() == 2
    assert [c.args[0] for c in rel.await_args_list] == ["wf-a", "wf-b"]


async def test_preempted_reaper_one_bad_row_does_not_stop_the_sweep():
    rows = [
        {"dbos_workflow_id": "wf-a", "issue_id": 1, "status": "cancelled"},
        {"dbos_workflow_id": "wf-b", "issue_id": 2, "status": "cancelled"},
    ]
    rel = AsyncMock(side_effect=[g.ParkedReleaseError("x"), None])
    with (
        patch.object(g, "_fetch_preempted_awaiting_rows", AsyncMock(return_value=rows)),
        patch.object(g, "release_parked_workflow", rel),
    ):
        assert await g.reap_preempted_input_waits() == 1
    assert rel.await_count == 2


async def test_preempted_query_filters_on_marker_preempt_status_and_live_workflow():
    fetch = AsyncMock(return_value=[])
    with patch("app.db.engine.fetch_all", fetch):
        await g._fetch_preempted_awaiting_rows()
    sql, params = fetch.await_args.args[0], fetch.await_args.args[1]
    assert "execution_state ? 'awaiting_input'" in sql
    assert "JOIN dbos.workflow_status" in sql
    assert "w.status IN ('PENDING', 'ENQUEUED')" in sql
    assert "i.status = ANY(:preempt)" in sql
    assert sorted(params["preempt"]) == ["cancelled", "closed", "done"]


def test_preempt_set_matches_the_lifecycle_set():
    from app.workflows.issue_lifecycle import PREEMPT_STATUSES

    assert frozenset(g.PREEMPT_STATUSES) == PREEMPT_STATUSES
