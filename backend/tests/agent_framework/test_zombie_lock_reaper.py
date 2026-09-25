"""input_gate.reap_zombie_locks — FH3 T6 (recon-5 §4, E3).

An issue whose execution lock outlived its workflow (CANCELLED / ERROR /
SUCCESS / no engine row) is locked forever: ``execute_issue``'s
``finally: clear_lock`` is a step and never lands after a cancel. Production
held five on 2026-09-25, four of them 16 days old. The reaper releases them
with a compare-and-swap UPDATE, never touches ``issues.status``, and never
cancels anything (the workflow is already terminal).
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from app.agent_framework import input_gate as g

pytestmark = pytest.mark.unit

LOCKED_AT = dt.datetime(2026, 9, 8, 19, 51, tzinfo=dt.timezone.utc)


def _row(issue_id=347463127022244, wf="issue-347463127022244-ee78e50f91d2", **kw):
    return {
        "issue_id": issue_id,
        "status": "needs_followup",
        "dbos_workflow_id": wf,
        "execution_locked_at": LOCKED_AT,
        "wf_status": "CANCELLED",
        **kw,
    }


class _Session:
    def __init__(self, rowcounts):
        self.statements = []
        self._rowcounts = list(rowcounts)

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        if "SET LOCAL ROLE" in str(stmt):
            return SimpleNamespace(rowcount=-1)
        return SimpleNamespace(rowcount=self._rowcounts.pop(0))


def _write_scope(session):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


# ── 1. candidate query ─────────────────────────────────────────────────────


async def test_candidate_query_shape():
    fetch = AsyncMock(return_value=[])
    with (
        patch("app.db.engine.fetch_all", fetch),
        patch("app.db.engine.is_configured", lambda: True),
    ):
        await g._fetch_zombie_lock_rows(limit=20, grace_s=600)
    sql, params = fetch.await_args.args[0], fetch.await_args.args[1]
    flat = " ".join(sql.split())
    assert "i.execution_locked_at IS NOT NULL" in flat
    assert "make_interval(secs => :grace_s)" in flat
    assert "LEFT JOIN dbos.workflow_status w" in flat
    assert "w.workflow_uuid IS NULL" in flat
    assert "w.status NOT IN ('PENDING', 'ENQUEUED', 'DELAYED')" in flat
    assert "NOT (COALESCE(i.execution_state, '{}'::jsonb) ? 'dispatching')" in flat
    assert "r.status = 'running'" in flat
    assert "r.issue_id = i.id OR r.conversation_id = i.ai_session_id" in flat
    assert "ORDER BY i.execution_locked_at" in flat
    assert "LIMIT :limit" in flat
    assert params == {"limit": 20, "grace_s": 600}


# ── 2. CAS statement ───────────────────────────────────────────────────────


def test_release_statement_is_a_compare_and_swap_that_leaves_status_alone():
    stmt = g.zombie_lock_release_stmt(
        issue_id=1, seen_locked_at=LOCKED_AT, seen_wf="wf-1"
    )
    sql = " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())
    where = sql.split(" WHERE ", 1)[1]
    assert "issues.id = " in where
    assert "issues.execution_locked_at = " in where
    assert "issues.dbos_workflow_id IS NOT DISTINCT FROM " in where
    set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "execution_locked_at=" in set_clause.replace(" ", "")
    assert "execution_state=" in set_clause.replace(" ", "")
    assert "status" not in set_clause.replace("execution_", "")
    assert set_clause.count(" - ") == 2  # jsonb minus, twice
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert "awaiting_input" in params.values()
    assert "dispatching" in params.values()


def test_release_statement_matches_a_missing_workflow_id():
    """``IS NOT DISTINCT FROM`` — a lock only a reply turn ever took has no
    ``dbos_workflow_id``; plain ``=`` against NULL would never match."""
    stmt = g.zombie_lock_release_stmt(
        issue_id=1, seen_locked_at=LOCKED_AT, seen_wf=None
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "IS NOT DISTINCT FROM" in sql


async def test_release_runs_as_service_role_first():
    session = _Session([1])
    with patch("app.db.session.write_scope", _write_scope(session)):
        assert await g._release_zombie_lock(_row()) is True
    assert "SET LOCAL ROLE service_role" in str(session.statements[0])
    assert len(session.statements) == 2


# ── 3–5. reaper loop ───────────────────────────────────────────────────────


async def test_released_rows_warn_raced_rows_do_not():
    rows = [_row(issue_id=1, wf="wf-a"), _row(issue_id=2, wf=None, wf_status=None)]
    session = _Session([1, 0])
    with (
        patch.object(g, "_fetch_zombie_lock_rows", AsyncMock(return_value=rows)),
        patch("app.db.session.write_scope", _write_scope(session)),
        patch.object(g.logger, "warning") as warn,
    ):
        assert await g.reap_zombie_locks() == 1
    msgs = [str(c.args[0]) for c in warn.call_args_list]
    assert len(msgs) == 1, msgs
    m = msgs[0]
    assert "released zombie lock" in m
    assert "issue=1" in m and "status=needs_followup" in m
    assert "wf=wf-a" in m and "wf_status=CANCELLED" in m and "locked_for=" in m


async def test_missing_engine_row_is_reported_as_missing():
    rows = [_row(issue_id=3, wf="wf-gone", wf_status=None)]
    with (
        patch.object(g, "_fetch_zombie_lock_rows", AsyncMock(return_value=rows)),
        patch.object(g, "_release_zombie_lock", AsyncMock(return_value=True)),
        patch.object(g.logger, "warning") as warn,
    ):
        assert await g.reap_zombie_locks() == 1
    assert "wf_status=missing" in str(warn.call_args.args[0])


async def test_one_bad_row_is_logged_and_the_sweep_goes_on():
    rows = [_row(issue_id=1), _row(issue_id=2)]
    release = AsyncMock(side_effect=[RuntimeError("db down"), True])
    with (
        patch.object(g, "_fetch_zombie_lock_rows", AsyncMock(return_value=rows)),
        patch.object(g, "_release_zombie_lock", release),
        patch.object(g.logger, "error") as err,
    ):
        assert await g.reap_zombie_locks() == 1
    assert release.await_count == 2
    assert any("issue=1" in str(c.args[0]) for c in err.call_args_list)


async def test_reaper_never_cancels_a_workflow():
    cancel = AsyncMock()
    with (
        patch.object(g, "_fetch_zombie_lock_rows", AsyncMock(return_value=[_row()])),
        patch.object(g, "_release_zombie_lock", AsyncMock(return_value=True)),
        patch.object(g, "_cancel_workflow", cancel),
        patch.object(g, "release_parked_workflow", cancel),
    ):
        assert await g.reap_zombie_locks() == 1
    cancel.assert_not_awaited()


async def test_a_failed_candidate_read_returns_zero_and_logs():
    with (
        patch.object(
            g, "_fetch_zombie_lock_rows", AsyncMock(side_effect=RuntimeError("x"))
        ),
        patch.object(g.logger, "error") as err,
    ):
        assert await g.reap_zombie_locks() == 0
    assert err.called


async def test_passes_limit_and_grace_through():
    fetch = AsyncMock(return_value=[])
    with patch.object(g, "_fetch_zombie_lock_rows", fetch):
        await g.reap_zombie_locks()
    fetch.assert_awaited_once_with(
        limit=g.ZOMBIE_LOCK_LIMIT, grace_s=g.ZOMBIE_LOCK_GRACE_SECONDS
    )
    assert (g.ZOMBIE_LOCK_LIMIT, g.ZOMBIE_LOCK_GRACE_SECONDS) == (20, 600)
