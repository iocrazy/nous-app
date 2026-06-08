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


# ── version-aware ownership (post-deploy version-orphan) ───────────────
# DBOS partitions workflows by application_version. After a deploy the worker
# runs a NEW commit_sha, so a PENDING/ENQUEUED workflow still tagged with the
# OLD version is permanently orphaned — no executor of that version exists.
# It looks recoverable but never will be → NOT a real claim.


def test_version_orphan_pending_is_not_a_claim():
    # PENDING tagged with an old version while live is a new version → orphan.
    assert sweeper._dbos_claims_workflow("PENDING", "old_sha", "new_sha") is False


def test_matching_version_pending_is_a_claim():
    assert sweeper._dbos_claims_workflow("PENDING", "same", "same") is True


def test_unknown_version_fails_closed_to_owned():
    # current version unknown (dev / no build-info) → keep owned behavior.
    assert sweeper._dbos_claims_workflow("PENDING", None, "new") is True
    # workflow version unknown → keep owned behavior.
    assert sweeper._dbos_claims_workflow("PENDING", "old", None) is True
    # both unknown → owned.
    assert sweeper._dbos_claims_workflow("PENDING", None, None) is True


def test_terminal_is_no_claim_regardless_of_version():
    assert sweeper._dbos_claims_workflow("SUCCESS", "old", "new") is False


@pytest.mark.asyncio
async def test_dbos_still_owns_skips_marking(monkeypatch):
    """When DBOS reports PENDING (matching version), _dbos_still_owns is True →
    caller skips LOST. When SUCCESS/None → False → caller may mark."""
    monkeypatch.setattr(
        sweeper, "_resolve_pinned_app_version", lambda: "live_v", raising=False
    )

    monkeypatch.setattr(sweeper, "_dbos_status_row", _row_const("PENDING", "live_v"))
    assert await sweeper._dbos_still_owns("wf-x") is True

    monkeypatch.setattr(sweeper, "_dbos_status_row", _row_const("SUCCESS", "live_v"))
    assert await sweeper._dbos_still_owns("wf-x") is False

    # No dbos_workflow_id → current behavior preserved (no claim).
    assert await sweeper._dbos_still_owns(None) is False


@pytest.mark.asyncio
async def test_dbos_still_owns_version_orphan_not_owned(monkeypatch):
    """PENDING but tagged with an old app_version while live is new → the
    workflow is version-orphaned → _dbos_still_owns False → caller may mark."""
    monkeypatch.setattr(
        sweeper, "_resolve_pinned_app_version", lambda: "new_sha", raising=False
    )

    monkeypatch.setattr(sweeper, "_dbos_status_row", _row_const("PENDING", "old_sha"))
    assert await sweeper._dbos_still_owns("wf-x") is False

    # Matching version → still owned.
    monkeypatch.setattr(sweeper, "_dbos_status_row", _row_const("PENDING", "new_sha"))
    assert await sweeper._dbos_still_owns("wf-x") is True


def _row_const(status, app_version):
    async def _f(_wid):
        return (status, app_version)

    return _f
