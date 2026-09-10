"""backfill_agent_runs_issue_id — the one-off for rows the creation-time stamp
came too late for.

``agent_runs.issue_id`` is now written by ``RunRecorder`` at creation (phase
2b-2 §4.2), but every issue run recorded before that — plus every run whose
post-turn ``backfill_issue_id`` never got to fire (crash, cancel, killed
worker) — is still NULL. The durable link is the run's conversation: it is
the issue's ``ai_session_id``.

Statements are asserted by COMPILING them, not by running them: the point is
that this is ORM-built (no ``text()``) and that the WHERE re-checks
``issue_id IS NULL`` so a replayed batch is a no-op rather than a clobber.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from app.workflows import backfill_agent_runs_issue_id as bf

pytestmark = pytest.mark.unit


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_the_update_is_orm_built_and_idempotent_by_construction():
    sql = _sql(bf.backfill_stmt(limit=500))
    assert "UPDATE public.agent_runs SET issue_id=" in sql
    # The link: the issue whose ai_session_id IS this run's conversation.
    assert "ai_session_id" in sql and "conversation_id" in sql
    # Re-checked in the UPDATE itself, not only in the candidate scan — two
    # overlapping batches must not clobber a value someone else just wrote.
    assert sql.count("issue_id IS NULL") >= 2


def test_no_raw_sql_anywhere_in_the_module():
    """裸 SQL 全量 ORM 化 (2026-08-04): new code adds no ``text()``."""
    import pathlib

    source = pathlib.Path(bf.__file__).read_text("utf-8")
    assert "text(" not in source
    assert "scoped_sql" not in source


def test_the_candidate_scan_skips_runs_with_no_conversation():
    sql = _sql(bf.candidates_stmt(limit=10))
    assert "conversation_id IS NOT NULL" in sql
    assert "LIMIT" in sql
    assert "ORDER BY public.agent_runs.id" in sql


class _Result:
    rowcount = 3

    def scalars(self):
        return self

    def all(self):
        return [1, 2, 3]


class _Session:
    def __init__(self, log, kind):
        self.log = log
        self.kind = kind

    async def execute(self, stmt):
        self.log.append(self.kind)
        return _Result()


class _Scope:
    def __init__(self, log, kind):
        self.log = log
        self.kind = kind

    async def __aenter__(self):
        return _Session(self.log, self.kind)

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def scopes(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(bf, "read_scope", lambda: _Scope(log, "read"))
    monkeypatch.setattr(bf, "write_scope", lambda: _Scope(log, "write"))
    return log


async def test_dry_run_never_opens_a_write_scope(scopes):
    assert await bf._one_batch(dry_run=True, limit=10) == 3
    assert scopes == ["read"]


async def test_a_live_batch_writes_and_reports_its_rowcount(scopes):
    assert await bf._one_batch(dry_run=False, limit=10) == 3
    assert scopes == ["write"]


async def test_a_dry_run_that_exactly_fills_the_limit_still_reports_exhausted(
    monkeypatch,
):
    """`would_stamp == limit` used to report `exhausted: False`, sending the
    operator round again for nothing. The scan asks for one MORE than it
    reports, so "is there anything past this page" is answered, not guessed."""
    seen: list[int] = []

    async def _batch(dry_run, limit):
        seen.append(limit)
        return 10  # exactly the caller's limit, one short of the probe

    monkeypatch.setattr(bf, "_one_batch", _batch)
    would, exhausted = await bf._dry_run_scan(10)
    assert seen == [11]
    assert (would, exhausted) == (10, True)


async def test_a_dry_run_with_more_rows_than_the_limit_reports_not_exhausted(
    monkeypatch,
):
    async def _batch(dry_run, limit):
        return 11  # the probe row came back → there is at least one more page

    monkeypatch.setattr(bf, "_one_batch", _batch)
    assert await bf._dry_run_scan(10) == (10, False)


def test_the_backfill_is_registered_under_its_name():
    from app.api.admin.backfill_router import _BACKFILLS

    assert _BACKFILLS["agent_runs_issue_id"] is bf.backfill_agent_runs_issue_id_workflow
