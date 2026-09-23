"""Defect B (2026-09-23 prod S2), against real Postgres.

``disarm_agent_wakeups(issue_id)`` must stop the wake-ups the AGENT armed on
an issue and nothing else. The predicate reads two jsonb keys with ``->>``
(``issue_id`` stored as a JSON number, ``created_by`` as a string); only the
database can settle that the text comparison matches the number form, that a
user's wake-up on the same issue is left armed, and that another issue's
agent wake-up is not touched.

Transport: asyncpg on ``INTEGRATION_DATABASE_URL`` for fixtures and
assertions; the repository goes through ``app.db.session`` repointed at the
same DSN. Skips cleanly when the DSN is unset.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — wake-up disarm needs a DB.",
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


async def _wakeup(pg, user_id, *, issue_id: int, created_by: str | None) -> uuid.UUID:
    # Same shape the tool (created_by="agent") and the ⏰ popover write. The
    # mig-461 cron_or_once CHECK rejects a NULL cron without ``once: true``.
    payload = {"issue_id": issue_id, "text": "check back", "once": True}
    if created_by is not None:
        payload["created_by"] = created_by
    if created_by == "agent":
        payload["run_id"] = "333739667136736"
    return await pg.fetchval(
        """INSERT INTO user_schedules (user_id, name, cron_expr, task_type,
                                       payload, next_fire_at)
           VALUES ($1, 'wake', NULL, 'issue_wakeup', $2::jsonb, $3)
           RETURNING id""",
        user_id,
        json.dumps(payload),
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    )


@_skip
async def test_only_the_agent_wakeups_on_that_issue_are_disarmed(orm_dsn, pg):
    from app.repositories.user_schedules_repository import disarm_agent_wakeups

    user_id = uuid.uuid4()
    await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    issue_id = int(uuid.uuid4().int % 10**15)
    other_issue = issue_id + 1
    ids = {
        "agent_1": await _wakeup(pg, user_id, issue_id=issue_id, created_by="agent"),
        "agent_2": await _wakeup(pg, user_id, issue_id=issue_id, created_by="agent"),
        "user": await _wakeup(pg, user_id, issue_id=issue_id, created_by="user"),
        # A legacy row with no created_by reads as the user's (issues_router).
        "legacy": await _wakeup(pg, user_id, issue_id=issue_id, created_by=None),
        "other_issue": await _wakeup(
            pg, user_id, issue_id=other_issue, created_by="agent"
        ),
    }
    try:
        n = await disarm_agent_wakeups(issue_id, reason="issue_not_active")
        rows = {
            r["id"]: r
            for r in await pg.fetch(
                "SELECT id, enabled, pause_reason, paused_at FROM user_schedules"
                " WHERE id = ANY($1::uuid[])",
                list(ids.values()),
            )
        }
        assert n == 2
        for key in ("agent_1", "agent_2"):
            row = rows[ids[key]]
            assert row["enabled"] is False, key
            assert row["pause_reason"] == "issue_not_active", key
            # The Schedules block renders a reason only behind paused_at.
            assert row["paused_at"] is not None, key
        for key in ("user", "legacy", "other_issue"):
            row = rows[ids[key]]
            assert row["enabled"] is True, key
            assert row["pause_reason"] is None, key
            assert row["paused_at"] is None, key

        # A second disarm finds nothing left: an already-stopped row keeps the
        # reason it stopped with (a fired row stays "fired_once").
        assert await disarm_agent_wakeups(issue_id, reason="issue_terminal") == 0
        still = await pg.fetchval(
            "SELECT pause_reason FROM user_schedules WHERE id = $1", ids["agent_1"]
        )
        assert still == "issue_not_active"
    finally:
        await pg.execute(
            "DELETE FROM user_schedules WHERE id = ANY($1::uuid[])", list(ids.values())
        )
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)
