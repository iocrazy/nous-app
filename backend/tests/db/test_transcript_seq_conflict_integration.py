"""DB-backed proof that two ``RunEventWriter``s on one run lose no events.

WHY THIS FILE EXISTS
────────────────────
The defect this covers is **caused by an index**: ``UNIQUE (run_id, seq)`` on
``agent_run_transcript_events`` (mig 397). The unit test next door models that
index in a stub, which is the right place to pin the retry logic — but a stub
index proves nothing about whether Postgres + asyncpg actually surface the
violation as a SQLAlchemy ``IntegrityError`` at the point ``append`` catches it.
If the driver raised something else, the retry would never run and the unit
tests would stay green while production kept swallowing events (the same shape
as CLAUDE.md's 「边界 mock 必须用真实 JSON 形状」).

So this file drives the REAL writers against a REAL database, interleaved the
way the deliverables registry and a live recorder interleave in production
(真栈 run 348401200407189).

Point it at any CI-way Postgres, same as the other files here:

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_transcript_seq_conflict_integration.py -v

Skips cleanly when INTEGRATION_DATABASE_URL is unset.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — transcript seq tests need a DB.",
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


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """One ``agent_runs`` row. Both FKs above it are real and must be satisfied
    (``agent_runs_agent_id_fkey``), so the agent row comes first."""
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"seq-conflict-agent-{uuid.uuid4().hex[:12]}",
    )
    run_id = await pg.fetchval(
        """
        INSERT INTO public.agent_runs (agent_id, user_id, status, trigger)
        VALUES ($1, $2, 'running', 'test')
        RETURNING id
        """,
        agent_id,
        uuid.uuid4(),
    )
    try:
        yield {"run": run_id, "agent": agent_id}
    finally:
        await pg.execute("DELETE FROM public.agent_runs WHERE id = $1", run_id)
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


@_skip
async def test_a_second_writer_never_eats_the_live_writers_event(orm_dsn, fx, pg):
    """The production interleaving, against the real unique index.

    Without the re-seed + retry in ``append`` the last assertion fails with
    three rows instead of four: the live writer's ``tool_call`` collided with
    the registry's ``deliverable`` and was dropped with only a warning.
    """
    from app.services.ai.runner.run_recorder import RunEventWriter

    run = int(fx["run"])
    live = RunEventWriter(run, seq_start=0)
    assert await live.append("user", {"content": "go"}) == 1
    assert await live.append("step_start", {"turn": 1, "step": 1}) == 2

    # The deliverables registry opens its own writer, seeded from max(seq).
    external = await RunEventWriter.for_run(run)
    assert (
        await external.append(
            "deliverable",
            {"kind": "script_shot", "ref_id": "9", "version": 1, "title": "MEDIUM"},
            turn=1,
            step=1,
        )
        == 3
    )

    # The live writer still thinks 3 is free. Postgres says otherwise.
    assert await live.append("tool_call", {"name": "UpdateShot"}) == 4
    assert live.foreign_writer_seen is True

    rows = await pg.fetch(
        """
        SELECT seq, event_type FROM public.agent_run_transcript_events
        WHERE run_id = $1 ORDER BY seq
        """,
        run,
    )
    assert [r["seq"] for r in rows] == [1, 2, 3, 4]
    assert [r["event_type"] for r in rows] == [
        "user",
        "step_start",
        "deliverable",
        "tool_call",
    ]


@_skip
async def test_the_late_writer_is_covered_by_the_same_retry(orm_dsn, fx, pg):
    """Reverse order: the live run keeps writing after ``for_run`` seeded, so
    it is the LATE writer that collides. ``GenerateShotImage`` dispatching to
    DBOS while its parent keeps iterating is exactly this."""
    from app.services.ai.runner.run_recorder import RunEventWriter

    run = int(fx["run"])
    live = RunEventWriter(run, seq_start=0)
    await live.append("user", {"content": "go"})

    external = await RunEventWriter.for_run(run)  # seeds at 1
    await live.append("step_start", {"turn": 1, "step": 1})
    await live.append("step_end", {"turn": 1, "step": 1})

    assert (
        await external.append(
            "deliverable",
            {"kind": "generated_media", "ref_id": "77", "version": 1},
            turn=1,
            step=1,
        )
        == 4
    )
    assert external.foreign_writer_seen is True

    seqs = [
        r["seq"]
        for r in await pg.fetch(
            "SELECT seq FROM public.agent_run_transcript_events "
            "WHERE run_id = $1 ORDER BY seq",
            run,
        )
    ]
    assert seqs == [1, 2, 3, 4]
