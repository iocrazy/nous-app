"""fh4 T2 (E2e) against real Postgres: the unclaimed-result alert marks each
stale, unclaimed, unexpired ``subagent_result`` exactly once, and nothing
else. The jsonb ``?`` predicate and the ``||`` merge are what only the
database can settle."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from tests.db import test_inbox_expire_skips_paused_integration as _base

# The DB fixtures and the skip marker are the paused-sweep test's, re-exported
# by assignment so pytest registers them here.
_skip = _base._skip
orm_dsn = _base.orm_dsn
pg = _base.pg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _item(pg, *, kind, age, claimed=False, expired=False, content=None):
    now = dt.datetime.now(dt.timezone.utc)
    return await pg.fetchval(
        "INSERT INTO agent_run_inbox(target_kind, target_id, user_id, kind, content,"
        " created_at, claimed_at, expired_at)"
        " VALUES ('conversation', 424242, gen_random_uuid(), $1, $2::jsonb, $3, $4, $5)"
        " RETURNING id",
        kind,
        json.dumps(content or {"summary": "s", "dedupe_key": f"k-{now.timestamp()}"}),
        now - age,
        now if claimed else None,
        now if expired else None,
    )


@_skip
async def test_alert_marks_each_stale_unclaimed_result_once(orm_dsn, pg):
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    hour = dt.timedelta(hours=1)
    ids = {
        "stale": await _item(pg, kind="subagent_result", age=hour),
        "fresh": await _item(pg, kind="subagent_result", age=dt.timedelta(minutes=5)),
        "claimed": await _item(pg, kind="subagent_result", age=hour, claimed=True),
        "expired": await _item(pg, kind="subagent_result", age=hour, expired=True),
        "steer": await _item(pg, kind="steer", age=hour),
    }
    try:
        repo = get_agent_run_inbox_repository()
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
        first = await repo.mark_unclaimed_results_alerted(older_than=cutoff)
        marked = {int(r["id"]) for r in first} & set(ids.values())
        assert marked == {ids["stale"]}
        second = await repo.mark_unclaimed_results_alerted(older_than=cutoff)
        assert not ({int(r["id"]) for r in second} & set(ids.values()))
        content = await pg.fetchval(
            "SELECT content FROM agent_run_inbox WHERE id = $1", ids["stale"]
        )
        content = json.loads(content) if isinstance(content, str) else content
        # The merge keeps the original keys, including the dedupe key.
        assert content["summary"] == "s" and "dedupe_key" in content
        assert "unclaimed_alerted_at" in content
    finally:
        await pg.execute(
            "DELETE FROM agent_run_inbox WHERE id = ANY($1::bigint[])",
            list(ids.values()),
        )
