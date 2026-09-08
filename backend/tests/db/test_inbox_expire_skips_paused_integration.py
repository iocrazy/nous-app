"""DB-backed check for the orphan-inbox sweep's paused-issue exclusion
(phase 2a Task 5).

``expire_stale_stmt(..., skip_paused_issues=True)`` is a pure builder; its
unit test greps the compiled SQL. That cannot settle whether Postgres reads
``NOT (target_kind = 'issue' AND target_id IN (SELECT id FROM issues WHERE
paused_at IS NOT NULL))`` the way the sweeper means it — in particular that a
non-issue target (or an issue that is NOT paused) is still expired and that
NULL handling inside ``NOT (... IN (...))`` never turns the exclusion into
"expire nothing". So the statement runs here, against the CI-built schema.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixtures and
assertions; the repository goes through ``app.db.session`` repointed at the
same DSN (``orm_dsn``). Skips cleanly when the DSN is unset.
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
    reason="INTEGRATION_DATABASE_URL not set — inbox sweep integration needs a DB.",
)


@pytest.fixture
async def orm_dsn():
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


async def _issue(pg, user_id, *, paused: bool) -> int:
    return await pg.fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, paused_at)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, $2, 'in_progress', 'medium', 'manual', $3, $4)
           RETURNING id""",
        f"P2A5-{uuid.uuid4().hex[:8]}",
        "Pause sweep fixture",
        user_id,
        dt.datetime.now(dt.timezone.utc) if paused else None,
    )


async def _item(pg, *, target_kind: str, target_id: int, age: dt.timedelta) -> int:
    return await pg.fetchval(
        "INSERT INTO agent_run_inbox(target_kind, target_id, user_id, kind, content,"
        " created_at) VALUES ($1, $2, gen_random_uuid(), 'steer', '{}'::jsonb, $3)"
        " RETURNING id",
        target_kind,
        target_id,
        dt.datetime.now(dt.timezone.utc) - age,
    )


@_skip
async def test_sweep_expires_orphans_but_leaves_paused_issue_items(orm_dsn, pg):
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    paused = await _issue(pg, user_id, paused=True)
    active = await _issue(pg, user_id, paused=False)
    conv_target = int(uuid.uuid4().int % 10**12)
    two_days = dt.timedelta(days=2)
    ids = {
        "paused_old": await _item(
            pg, target_kind="issue", target_id=paused, age=two_days
        ),
        "active_old": await _item(
            pg, target_kind="issue", target_id=active, age=two_days
        ),
        "active_fresh": await _item(
            pg, target_kind="issue", target_id=active, age=dt.timedelta(minutes=1)
        ),
        "conv_old": await _item(
            pg, target_kind="conversation", target_id=conv_target, age=two_days
        ),
    }
    try:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
        n = await get_agent_run_inbox_repository().expire_stale(
            older_than=cutoff, skip_paused_issues=True
        )
        rows = await pg.fetch(
            "SELECT id, expired_at FROM agent_run_inbox WHERE id = ANY($1::bigint[])",
            list(ids.values()),
        )
        expired = {r["id"] for r in rows if r["expired_at"] is not None}
        # only the two old orphans (active issue + conversation target)
        assert expired == {ids["active_old"], ids["conv_old"]}
        assert n == 2
        # …and without the flag the paused issue's item is swept too
        n2 = await get_agent_run_inbox_repository().expire_stale(
            older_than=cutoff, skip_paused_issues=False
        )
        assert n2 == 1
        still = await pg.fetchval(
            "SELECT expired_at FROM agent_run_inbox WHERE id = $1", ids["paused_old"]
        )
        assert still is not None
    finally:
        await pg.execute(
            "DELETE FROM agent_run_inbox WHERE id = ANY($1::bigint[])",
            list(ids.values()),
        )
        await pg.execute(
            "DELETE FROM issues WHERE id = ANY($1::bigint[])", [paused, active]
        )
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)
