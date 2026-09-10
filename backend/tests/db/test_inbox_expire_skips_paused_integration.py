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


async def _issue(pg, user_id, *, paused: bool, locked: bool = False) -> int:
    return await pg.fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, paused_at,
                               execution_locked_at)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, $2, 'in_progress', 'medium', 'manual', $3, $4, $5)
           RETURNING id""",
        f"P2A5-{uuid.uuid4().hex[:8]}",
        "Pause sweep fixture",
        user_id,
        dt.datetime.now(dt.timezone.utc) if paused else None,
        dt.datetime.now(dt.timezone.utc) if locked else None,
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


@_skip
async def test_the_drain_scan_skips_an_issue_whose_turn_lock_is_held(orm_dsn, pg):
    """Task 7b defect C, against real Postgres.

    ``pending_issue_targets_stmt`` excludes locked issues with
    ``target_id NOT IN (SELECT id FROM issues WHERE execution_locked_at IS NOT
    NULL)``. The unit test greps the compiled SQL; only the database can settle
    that the exclusion narrows the result instead of emptying it — the failure
    mode of ``NOT IN`` is a NULL in the subquery turning the whole predicate
    into "match nothing", and a backstop that silently drains nobody reads
    exactly like a backstop with nothing to do.
    """
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    locked = await _issue(pg, user_id, paused=False, locked=True)
    idle = await _issue(pg, user_id, paused=False, locked=False)
    fresh = dt.timedelta(minutes=1)
    ids = {
        "locked": await _item(pg, target_kind="issue", target_id=locked, age=fresh),
        "idle": await _item(pg, target_kind="issue", target_id=idle, age=fresh),
    }
    try:
        targets = {
            int(t["target_id"])
            for t in await get_agent_run_inbox_repository().pending_issue_targets(
                limit=1000
            )
        }
        assert idle in targets, "an idle issue with a stranded item must be drained"
        assert locked not in targets, "the backstop must not race the running turn"

        # Positive control: the same issue becomes drainable the moment the
        # lock is released, so the exclusion is about the lock and not about
        # this row being unreachable for some other reason.
        #
        # ``session_replication_role = replica`` because mig 170's allowlist
        # trigger bypasses only service_role / supabase_admin, and this runner
        # connects as postgres — the column would otherwise refuse the write
        # with insufficient_privilege. SET **LOCAL**, so it dies with the
        # transaction instead of leaking into the rest of the session.
        async with pg.transaction():
            await pg.execute("SET LOCAL session_replication_role = replica")
            await pg.execute(
                "UPDATE issues SET execution_locked_at = NULL WHERE id = $1", locked
            )
        after = {
            int(t["target_id"])
            for t in await get_agent_run_inbox_repository().pending_issue_targets(
                limit=1000
            )
        }
        assert locked in after
    finally:
        await pg.execute(
            "DELETE FROM agent_run_inbox WHERE id = ANY($1::bigint[])",
            list(ids.values()),
        )
        await pg.execute(
            "DELETE FROM issues WHERE id = ANY($1::bigint[])", [locked, idle]
        )
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)
