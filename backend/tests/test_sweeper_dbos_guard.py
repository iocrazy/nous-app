"""G3 — sweeper/reaper must consult DBOS before flipping a row to LOST.

`dbos.workflow_status.status` for the row's workflow_uuid decides ownership:
  * PENDING / ENQUEUED → DBOS still owns it / will recover it → SKIP marking.
  * SUCCESS / ERROR / CANCELLED / no row → DBOS has no live claim → may mark.

The decision is a pure helper so it's testable without a DB.
"""

import pytest

import app.workflows.scheduled_recovery as recovery
import app.workflows.workflow_health_sweeper as sweeper


def test_pending_means_dbos_still_owns():
    assert sweeper._dbos_claims_workflow("PENDING") is True
    assert sweeper._dbos_claims_workflow("ENQUEUED") is True
    # case-insensitive tolerance
    assert sweeper._dbos_claims_workflow("pending") is True


def test_terminal_or_missing_means_no_claim():
    assert sweeper._dbos_claims_workflow("SUCCESS") is False
    assert sweeper._dbos_claims_workflow("ERROR") is False
    assert sweeper._dbos_claims_workflow("CANCELLED") is False
    assert sweeper._dbos_claims_workflow(None) is False
    assert sweeper._dbos_claims_workflow("") is False


def test_recovery_reuses_same_decision():
    # Both modules share one helper to avoid drift.
    assert recovery._dbos_claims_workflow is sweeper._dbos_claims_workflow


@pytest.mark.asyncio
async def test_dbos_still_owns_skips_marking(monkeypatch):
    """When DBOS reports PENDING, _dbos_still_owns is True → caller skips
    LOST. When SUCCESS/None → False → caller may mark."""
    monkeypatch.setattr(sweeper, "_dbos_status", _const("PENDING"))
    assert await sweeper._dbos_still_owns("wf-x") is True

    monkeypatch.setattr(sweeper, "_dbos_status", _const("SUCCESS"))
    assert await sweeper._dbos_still_owns("wf-x") is False

    # No dbos_workflow_id → current behavior preserved (no claim).
    assert await sweeper._dbos_still_owns(None) is False


def _const(value):
    async def _f(_wid):
        return value

    return _f
