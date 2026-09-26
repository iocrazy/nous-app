"""fh5 T5 against the CI-built schema: inbox items a run claimed but never got
an answer for go back to the queue, bounded, and never on a finished issue.

The unit tests pin the compiled SQL and the wiring; only Postgres can say that
the correlated ``NOT EXISTS (step_end …)``, the ``content || jsonb_build_object``
bump and the ``NOT (kind='issue' AND id IN (finished))`` exclusion select the
rows they mean to. The claim itself goes through the real ``claim_stmt``, the
failed / cancelled close through the real ``RunRecorder._finish`` UPDATE, and
the crash close through the real ``mark_worker_shutdown_ids`` (the same
``_after_crash_flip`` the heartbeat_lost sweep uses, scoped to one run so a
shared drift DB's other running rows are left alone).

Skips cleanly when ``INTEGRATION_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — inbox give-back needs a DB.",
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
async def world():
    """A user, an agent, and bookkeeping to delete what the test created."""
    conn = await asyncpg.connect(_TEST_DSN)
    user_id = uuid.uuid4()
    await conn.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    agent_id = await conn.fetchval(
        "INSERT INTO ai_agents (name, slug) VALUES ($1, $2) RETURNING id",
        "Inbox Give-back Agent",
        f"fh5-t5-{uuid.uuid4().hex[:8]}",
    )
    made: dict = {"conn": conn, "user": user_id, "agent": agent_id, "issues": []}
    try:
        yield made
    finally:
        await conn.execute(
            "DELETE FROM agent_run_inbox WHERE user_id = $1", str(user_id)
        )
        await conn.execute("DELETE FROM agent_runs WHERE agent_id = $1", agent_id)
        # the crash close writes an hourly sample keyed by the agent; deleting
        # the agent would SET NULL it onto another row's unique key
        await conn.execute("DELETE FROM ai_usage_hourly WHERE agent_id = $1", agent_id)
        for issue_id in made["issues"]:
            await conn.execute("DELETE FROM issues WHERE id = $1", issue_id)
        await conn.execute("DELETE FROM ai_agents WHERE id = $1", agent_id)
        await conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)
        await conn.close()


async def _issue(world, *, status: str = "in_progress", hidden: bool = False) -> int:
    issue_id = await world["conn"].fetchval(
        """INSERT INTO issues (issue_number, identifier, title, status, priority,
                               origin_kind, created_by_user_id, hidden_at)
           VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                   $1, $2, $3, 'medium', 'manual', $4, $5)
           RETURNING id""",
        f"FH5T5-{uuid.uuid4().hex[:8]}",
        "Inbox give-back fixture",
        status,
        world["user"],
        dt.datetime.now(dt.timezone.utc) if hidden else None,
    )
    world["issues"].append(issue_id)
    return issue_id


async def _run(world, *, status: str = "running") -> int:
    return await world["conn"].fetchval(
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger, heartbeat_at)"
        " VALUES ($1, $2, $3, 'issue_dispatch', now()) RETURNING id",
        world["agent"],
        world["user"],
        status,
    )


async def _item(world, issue_id: int, *, redelivered: int | None = None) -> int:
    content = '{"body": "focus on act two"}'
    if redelivered is not None:
        content = f'{{"body": "focus on act two", "redelivered": {redelivered}}}'
    return await world["conn"].fetchval(
        "INSERT INTO agent_run_inbox(target_kind, target_id, user_id, kind, content)"
        " VALUES ('issue', $1, $2, 'steer', $3::jsonb) RETURNING id",
        issue_id,
        str(world["user"]),
        content,
    )


async def _claim(issue_id: int, run_id: int, step: int) -> list[int]:
    from app.repositories.agent_run_inbox_repository import (
        get_agent_run_inbox_repository,
    )

    rows = await get_agent_run_inbox_repository().claim(
        targets=[("issue", issue_id)], run_id=run_id, turn=1, step=step
    )
    return [int(r["id"]) for r in rows]


async def _step_end(world, run_id: int, step: int) -> None:
    await world["conn"].execute(
        "INSERT INTO agent_run_transcript_events (run_id, seq, event_type, payload,"
        " turn, step) VALUES ($1, $2, 'step_end', '{}'::jsonb, 1, $2)",
        run_id,
        step,
    )


async def _row(world, item_id: int):
    return await world["conn"].fetchrow(
        "SELECT claimed_at, claimed_run_id, claimed_step, expired_at,"
        " (content->>'redelivered')::int AS redelivered, content->>'body' AS body"
        " FROM agent_run_inbox WHERE id = $1",
        item_id,
    )


async def _finish(world, run_id: int, claimed: list[int], status: str) -> None:
    from app.services.ai.runner.run_recorder import RunRecorder

    rec = RunRecorder(
        agent_id=world["agent"], user_id=world["user"], trigger="issue_dispatch"
    )
    rec.run_id = str(run_id)
    rec.note_inbox_claimed(claimed)
    with (
        patch("app.services.ai_usage.record_usage", AsyncMock()),
        patch("app.services.search.projection.project_run_best_effort", AsyncMock()),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
    ):
        await rec._finish(status=status)


# ── 1. failed run with no answer → pending again ─────────────────────────


@_skip
async def test_a_failed_run_gives_its_unanswered_claim_back(orm_dsn, world):
    issue_id = await _issue(world)
    item = await _item(world, issue_id)
    run_id = await _run(world)
    assert await _claim(issue_id, run_id, step=1) == [item]

    await _finish(world, run_id, [item], "failed")

    row = await _row(world, item)
    assert row["claimed_at"] is None and row["claimed_run_id"] is None
    assert row["claimed_step"] is None and row["expired_at"] is None
    assert row["redelivered"] == 1
    assert row["body"] == "focus on act two"  # the rest of content survives


# ── 2. answered at step 1, run dies at step 2 → stays claimed ────────────


@_skip
async def test_an_answered_claim_stays_consumed_when_the_run_dies_later(orm_dsn, world):
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    issue_id = await _issue(world)
    early = await _item(world, issue_id)
    run_id = await _run(world)
    assert await _claim(issue_id, run_id, step=1) == [early]
    await _step_end(world, run_id, step=1)
    late = await _item(world, issue_id)
    assert await _claim(issue_id, run_id, step=2) == [late]

    assert await get_agent_runs_repository().mark_worker_shutdown_ids([run_id]) == [
        run_id
    ]

    assert (await _row(world, early))["claimed_run_id"] == run_id
    late_row = await _row(world, late)
    assert late_row["claimed_at"] is None and late_row["redelivered"] == 1


# ── 3. cancelled run keeps its claim ─────────────────────────────────────


@_skip
async def test_a_cancelled_run_keeps_its_claim(orm_dsn, world):
    issue_id = await _issue(world)
    item = await _item(world, issue_id)
    run_id = await _run(world)
    await _claim(issue_id, run_id, step=1)

    await _finish(world, run_id, [item], "cancelled")

    row = await _row(world, item)
    assert row["claimed_run_id"] == run_id and row["redelivered"] is None


# ── 4. recovery run takes over the superseded run's item ─────────────────


@_skip
async def test_a_recovery_run_reclaims_the_superseded_runs_item(orm_dsn, world):
    from app.repositories import agent_run_inbox_redelivery as redelivery

    issue_id = await _issue(world)
    item = await _item(world, issue_id)
    dead = await _run(world)  # still 'running': the sweeper has not flipped it
    await _claim(issue_id, dead, step=1)
    recovery = await _run(world)
    assert await _claim(issue_id, recovery, step=1) == []  # the bug, before

    out = await redelivery.release_orphaned_claims([dead], reason="recovered")

    assert out.redelivered == (item,)
    assert await _claim(issue_id, recovery, step=1) == [item]


# ── 5. crash close (heartbeat_lost family) re-pends ──────────────────────


@_skip
async def test_a_worker_shutdown_close_gives_the_claim_back(orm_dsn, world):
    from app.repositories.agent_runs_repository import get_agent_runs_repository

    issue_id = await _issue(world)
    item = await _item(world, issue_id)
    run_id = await _run(world)
    await _claim(issue_id, run_id, step=1)

    await get_agent_runs_repository().mark_worker_shutdown_ids([run_id])

    status = await world["conn"].fetchval(
        "SELECT status FROM agent_runs WHERE id = $1", run_id
    )
    assert status == "heartbeat_lost"
    row = await _row(world, item)
    assert row["claimed_at"] is None and row["redelivered"] == 1


# ── 6. an item on a finished issue is never resurrected ──────────────────


@_skip
@pytest.mark.parametrize(
    "status,hidden", [("done", False), ("cancelled", False), ("in_progress", True)]
)
async def test_an_item_on_a_finished_issue_stays_claimed(
    orm_dsn, world, status, hidden
):
    issue_id = await _issue(world)
    item = await _item(world, issue_id)
    run_id = await _run(world)
    await _claim(issue_id, run_id, step=1)
    await world["conn"].execute(
        "UPDATE issues SET status = $2, hidden_at = $3 WHERE id = $1",
        issue_id,
        status,
        dt.datetime.now(dt.timezone.utc) if hidden else None,
    )

    await _finish(world, run_id, [item], "failed")

    row = await _row(world, item)
    assert row["claimed_run_id"] == run_id and row["redelivered"] is None


# ── cap: the third give-back expires with an ERROR ───────────────────────


@_skip
async def test_the_third_give_back_expires_the_item(orm_dsn, world):
    from app.repositories import agent_run_inbox_redelivery as redelivery

    issue_id = await _issue(world)
    item = await _item(world, issue_id, redelivered=2)
    run_id = await _run(world)
    await _claim(issue_id, run_id, step=1)

    out = await redelivery.release_claims_for_run(run_id, [item], reason="failed")

    assert out == redelivery.RedeliveryOutcome(expired=(item,))
    row = await _row(world, item)
    assert row["expired_at"] is not None and row["claimed_run_id"] == run_id
    assert await _claim(issue_id, await _run(world), step=1) == []
