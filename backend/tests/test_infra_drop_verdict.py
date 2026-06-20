"""Verdict taxonomy P0 — infra-drop is 'lost' (retryable), NOT 'failed'.

A worker dying (heartbeat stale → E3) or redeploying before a workflow ran
(version-orphan → E2) is infra dropping the work, not the workflow faulting.
The terminal task_tracking status must be 'lost' (UI shows interrupted +
Retry), never 'failed' ('failed' is reserved for E1 = a real DBOS ERROR with
the decoded reason, written by reap_stuck_pending_tasks_step Pass A).

These lock the relabel so a future edit can't silently regress infra-drop back
to 'failed'. The writers do a DB UPDATE, so we capture the SQL via a fake
engine rather than hitting a database.
"""

import pytest

import app.workflows.workflow_health_sweeper as sweeper


def _patch_engine(monkeypatch, captured, *, execute_returns=1):
    async def fake_execute(sql, params=None):
        captured.append(sql)
        return execute_returns

    monkeypatch.setattr("app.db.engine.execute", fake_execute, raising=False)


@pytest.mark.asyncio
async def test_mark_lost_writes_status_lost_not_failed(monkeypatch):
    captured: list[str] = []
    _patch_engine(monkeypatch, captured)

    await sweeper._mark_lost(
        {"dbos_workflow_id": "wf-1", "task_type": "download", "title": "x"}
    )

    joined = " ".join(captured)
    assert "status = 'lost'" in joined
    assert "status = 'failed'" not in joined
    assert "phase = 'lost'" in joined
    assert "error_code = 'worker_lost'" in joined


@pytest.mark.asyncio
async def test_cancel_dbos_zombie_reconciles_to_lost_not_failed(monkeypatch):
    captured: list[str] = []
    # First execute = the dbos.workflow_status CANCEL (returns 1 → proceed).
    _patch_engine(monkeypatch, captured)

    ok = await sweeper._cancel_dbos_zombie("wf-2")
    assert ok is True

    tt_writes = [s for s in captured if "task_tracking" in s]
    assert tt_writes, "expected a task_tracking reconcile write"
    joined = " ".join(tt_writes)
    assert "status = 'lost'" in joined
    assert "status = 'failed'" not in joined


@pytest.mark.asyncio
async def test_cancel_dbos_zombie_noop_when_already_terminal(monkeypatch):
    """If the engine CANCEL touches 0 rows (raced to terminal), no reconcile
    write happens and the call reports it didn't cancel."""
    captured: list[str] = []
    _patch_engine(monkeypatch, captured, execute_returns=0)

    ok = await sweeper._cancel_dbos_zombie("wf-3")
    assert ok is False
    assert not [s for s in captured if "task_tracking" in s]
