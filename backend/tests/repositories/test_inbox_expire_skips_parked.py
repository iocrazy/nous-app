"""FH3 T1: the orphan-inbox sweep must not expire items queued on an issue that
is GENUINELY parked on the needs_input gate — and must still expire the ones
on an issue that only LOOKS parked because a dead workflow left its lock.

The gate waits ``NEEDS_INPUT_RECV_TTL_HOURS`` (72 h) per round, so a wake-up
queued while the issue waits on a person used to be thrown away at 24 h, long
before the person could answer. Skipping on the bare turn lock would be worse:
production held five locks for up to 16 days behind CANCELLED workflows, and
their items would then never expire and never drain. So the skip is the SQL
form of ``is_parked_on_input`` with an upper bound on the marker's ``since``.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import agent_run_inbox_repository as repo

pytestmark = pytest.mark.unit
OLDER = dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc)
FLOOR = dt.datetime(2026, 9, 4, tzinfo=dt.timezone.utc)


def _sql(stmt) -> str:
    """Compiled SQL with its bind values appended — the jsonb path keys
    (``awaiting_input``, ``since`` …) are bound parameters, not literals."""
    compiled = stmt.compile(dialect=postgresql.dialect())
    params = {k: v for k, v in compiled.params.items() if isinstance(v, str)}
    return f"{compiled} -- {params}".lower()


def _plain(**kw) -> str:
    return _sql(repo.expire_stale_stmt(OLDER, skip_paused_issues=True, **kw))


def test_the_parked_exclusion_is_the_parked_predicate_with_a_ttl_bound():
    sql = _plain(skip_parked_issues=True, parked_floor=FLOOR)
    # lock + an unanswered awaiting_input marker + since newer than the floor
    assert "execution_locked_at is not null" in sql
    assert "awaiting_input" in sql
    assert "answered_at" in sql and "is null" in sql
    assert "since" in sql
    # the bound compares the marker time against the floor as a timestamp
    assert "timestamp with time zone" in sql


def test_the_lock_is_never_excluded_on_its_own():
    """The bare lock would pin the dead-workflow issues forever (recon §4):
    every issue subquery that mentions the lock must also carry the marker."""
    sql = _plain(skip_parked_issues=True, parked_floor=FLOOR)
    subqueries = sql.split(" -- ")[0].split("select public.issues.id")[1:]
    assert len(subqueries) == 2, "paused + parked, one subquery each"
    for chunk in subqueries:
        where = chunk.split("returning")[0]
        if "execution_locked_at" in where:
            assert "execution_state ?" in where, where
            assert "->>" in where and "case when" in where, where


def test_without_the_parked_flag_the_statement_is_unchanged():
    before = _sql(repo.expire_stale_stmt(OLDER, skip_paused_issues=True))
    assert _plain(skip_parked_issues=False) == before
    assert "awaiting_input" not in before


def test_the_parked_flag_needs_a_floor():
    with pytest.raises(ValueError):
        repo.expire_stale_stmt(OLDER, skip_paused_issues=True, skip_parked_issues=True)


async def test_the_sweeper_skips_parked_issues_up_to_the_gate_ttl(monkeypatch):
    from app.core.config import settings
    from app.workflows import agent_runs_sweeper as sw

    fake = SimpleNamespace(expire_stale=AsyncMock(return_value=0))
    monkeypatch.setattr(repo, "get_agent_run_inbox_repository", lambda: fake)
    before = dt.datetime.now(dt.timezone.utc)
    await sw.expire_orphan_inbox_step()
    kw = fake.expire_stale.await_args.kwargs
    assert kw["skip_paused_issues"] is True
    assert kw["skip_parked_issues"] is True
    expected = before - dt.timedelta(
        hours=settings.NEEDS_INPUT_RECV_TTL_HOURS,
        seconds=sw.PARKED_EXPIRY_GRACE_SECONDS,
    )
    assert abs((kw["parked_floor"] - expected).total_seconds()) < 5
    assert sw.PARKED_EXPIRY_GRACE_SECONDS == 3600


async def test_the_repository_passes_the_parked_arguments_through(monkeypatch):
    seen: list = []

    class _Session:
        async def execute(self, stmt):
            seen.append(_sql(stmt))
            return SimpleNamespace(all=lambda: [])

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(repo, "write_scope", lambda: _Scope())
    await repo.AgentRunInboxRepository().expire_stale(
        older_than=OLDER, skip_parked_issues=True, parked_floor=FLOOR
    )
    assert "awaiting_input" in seen[0]


# ── the scan's inactive-issue expiry ──────────────────────────────────────


def test_expire_agent_items_touches_only_pending_agent_items_on_one_target():
    sql = _sql(repo.expire_agent_items_stmt("issue", 42))
    assert "update public.agent_run_inbox" in sql
    assert "claimed_at is null" in sql and "expired_at is null" in sql
    assert "target_kind = " in sql and "target_id = " in sql
    # content -> 'source' ->> 'created_by' = 'agent'
    assert "content[" in sql and "->>" in sql
    for key in ("'source'", "'created_by'", "'agent'"):
        assert key in sql, key
    assert "returning" in sql


def test_the_inactive_statuses_live_in_the_dbos_free_module():
    """The sweeper must not import ``scheduled_master`` (it registers DBOS
    workflows at import); the wake-up guard and the sweeper read one tuple."""
    from app.repositories import user_schedules_repository as us
    from app.workflows import scheduled_master as sm

    assert us.AGENT_WAKEUP_INACTIVE_STATUSES == ("in_review", "needs_followup")
    assert sm._AGENT_WAKEUP_INACTIVE_STATUSES is us.AGENT_WAKEUP_INACTIVE_STATUSES
