"""DB-backed test for ``IssueRepository.list_for_user``'s ``pending_wakeups``
count (harness 2b-2 Task 6 — the Quick "Scheduled N" chip).

WHY DB-BACKED
─────────────
The count is one aggregate over ``user_schedules`` keyed on
``payload->>'issue_id'``. That join is a Postgres fact, not a SQLAlchemy one:
``->>`` yields TEXT, so the comparison only matches when the issue id is bound
as a **string**; a bigint bind compiles fine and silently counts nothing. A
stubbed session cannot tell those two apart, which is exactly the gap
CLAUDE.md's "单测里的 session 是桩的，跑绿不代表 Postgres 接受" warns about.

Point it at any CI-way Postgres (ci_bootstrap.sql → schema_baseline.sql →
migrations above the watermark):

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/repositories/test_issue_list_pending_wakeups.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset, same as
tests/db/test_assets_repository_integration.py.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — pending_wakeups needs a real DB.",
)


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine at the test DSN, then restore + dispose."""
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def _mk_issue(pg: asyncpg.Connection, user_id: uuid.UUID, title: str) -> int:
    row = await pg.fetchrow(
        """
        INSERT INTO public.issues (title, status, priority, origin_kind,
                                   origin_fingerprint, created_by_user_id)
        VALUES ($1, 'todo', 'medium', 'manual', $2, $3)
        RETURNING id
        """,
        title,
        uuid.uuid4().hex,
        user_id,
    )
    return int(row["id"])


async def _mk_wakeup(
    pg: asyncpg.Connection, user_id: uuid.UUID, issue_id: int, *, enabled: bool
) -> uuid.UUID:
    row = await pg.fetchrow(
        """
        INSERT INTO public.user_schedules
            (name, cron_expr, task_type, payload, enabled, next_fire_at, user_id)
        VALUES ($1, NULL, 'issue_wakeup', $2::jsonb, $3, $4, $5)
        RETURNING id
        """,
        f"wake {issue_id}",
        f'{{"issue_id": {issue_id}, "text": "check the render", "once": true}}',
        enabled,
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        user_id,
    )
    return row["id"]


@_skip
async def test_pending_wakeups_counts_only_enabled_issue_wakeups(orm_dsn, pg):
    from app.repositories.issue_repository import IssueRepository

    user_id = uuid.uuid4()
    armed_id = await _mk_issue(pg, user_id, "Armed Issue")
    quiet_id = await _mk_issue(pg, user_id, "Quiet Issue")
    try:
        await _mk_wakeup(pg, user_id, armed_id, enabled=True)
        # A disabled row is a wake-up that already fired (or was stopped): it
        # must NOT keep the chip lit.
        await _mk_wakeup(pg, user_id, quiet_id, enabled=False)

        items, _total = await IssueRepository().list_for_user(str(user_id), limit=50)
        by_id = {int(i["id"]): i for i in items}

        assert by_id[armed_id]["pending_wakeups"] == 1
        assert by_id[quiet_id]["pending_wakeups"] == 0
    finally:
        await pg.execute(
            "DELETE FROM public.user_schedules WHERE user_id = $1", user_id
        )
        await pg.execute(
            "DELETE FROM public.issues WHERE id = ANY($1::bigint[])",
            [armed_id, quiet_id],
        )


@_skip
async def test_pending_wakeups_sums_several_and_ignores_other_task_types(orm_dsn, pg):
    from app.repositories.issue_repository import IssueRepository

    user_id = uuid.uuid4()
    issue_id = await _mk_issue(pg, user_id, "Busy Issue")
    try:
        await _mk_wakeup(pg, user_id, issue_id, enabled=True)
        await _mk_wakeup(pg, user_id, issue_id, enabled=True)
        # An agent_routine that produced this issue is not a pending wake-up.
        await pg.execute(
            """
            INSERT INTO public.user_schedules
                (name, cron_expr, task_type, payload, enabled, next_fire_at, user_id)
            VALUES ('routine', '0 9 * * 1', 'agent_routine', $1::jsonb, true, $2, $3)
            """,
            f'{{"last_issue_id": {issue_id}, "issue_id": {issue_id}}}',
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            user_id,
        )

        items, _total = await IssueRepository().list_for_user(str(user_id), limit=50)
        row = next(i for i in items if int(i["id"]) == issue_id)
        assert row["pending_wakeups"] == 2
    finally:
        await pg.execute(
            "DELETE FROM public.user_schedules WHERE user_id = $1", user_id
        )
        await pg.execute("DELETE FROM public.issues WHERE id = $1", issue_id)
