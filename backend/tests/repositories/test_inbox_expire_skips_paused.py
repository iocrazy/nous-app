"""Phase 2a Task 5: the orphan-inbox sweeper must not expire items queued on a
PAUSED issue — those are waiting for resume, not orphaned."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import agent_run_inbox_repository as repo

pytestmark = pytest.mark.unit
OLDER = dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect())).lower()


def test_expire_stmt_excludes_items_targeting_paused_issues():
    sql = _sql(repo.expire_stale_stmt(OLDER, skip_paused_issues=True))
    assert "update public.agent_run_inbox" in sql
    assert "claimed_at is null" in sql and "expired_at is null" in sql
    # a correlated exclusion on issues.paused_at, keyed by the issue target
    assert "paused_at is not null" in sql
    assert "target_kind" in sql and "not (" in sql or "not exists" in sql


def test_expire_stmt_without_the_flag_is_the_plain_sweep():
    sql = _sql(repo.expire_stale_stmt(OLDER, skip_paused_issues=False))
    assert "paused_at" not in sql


async def test_sweeper_step_asks_to_skip_paused_issues(monkeypatch):
    from app.workflows import agent_runs_sweeper as sw

    fake = SimpleNamespace(expire_stale=AsyncMock(return_value=1))
    monkeypatch.setattr(repo, "get_agent_run_inbox_repository", lambda: fake)
    assert await sw.expire_orphan_inbox_step() == 1
    assert fake.expire_stale.await_args.kwargs["skip_paused_issues"] is True
