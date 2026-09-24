"""fh2 T3 statements against the real schema, inside one rolled-back transaction.

The unit tests grep compiled SQL. That cannot settle whether Postgres accepts
the jsonb arithmetic (``->>`` cast to int, ``jsonb_build_object`` on a bound
text key), whether the RETURNING reads the NEW value, or whether the lookup
and the backlink behave as the recovery link needs. So each builder runs here.

Everything happens on one connection in a transaction that is rolled back, so
nothing persists in the shared drift database. ``session_replication_role =
replica`` (SET LOCAL) suppresses the mig-170 allowlist trigger the way the
service_role write would — the drift DB's service_role has no grants.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
if not _TEST_DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)


@pytest.fixture
async def conn():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    dsn = _TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(dsn)
    async with engine.connect() as c:
        tx = await c.begin()
        await c.execute(text("SET LOCAL session_replication_role = replica"))
        try:
            yield c
        finally:
            await tx.rollback()
    await engine.dispose()


async def _issue(conn) -> int:
    from sqlalchemy import text

    return (
        await conn.execute(
            text(
                "INSERT INTO issues (issue_number, identifier, title, execution_state,"
                " created_by_user_id)"
                " VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),"
                " :ident, 'fh2 T3 fixture', '{\"turn\": 3}'::jsonb,"
                " (SELECT id FROM auth.users LIMIT 1)) RETURNING id"
            ),
            {"ident": f"FH2T3-{uuid.uuid4().hex[:8]}"},
        )
    ).scalar_one()


async def _run(conn, issue_id: int, *, meta: dict, status: str = "running") -> int:
    import json

    from sqlalchemy import text

    agent_id = (await conn.execute(text("SELECT id FROM ai_agents LIMIT 1"))).scalar()
    user_id = (await conn.execute(text("SELECT id FROM auth.users LIMIT 1"))).scalar()
    if agent_id is None or user_id is None:
        pytest.skip("drift DB has no ai_agents / auth.users row for the FKs")
    return (
        await conn.execute(
            text(
                "INSERT INTO agent_runs (agent_id, user_id, status, trigger, issue_id,"
                " metadata_json) VALUES (:a, :u, :s, 'issue_dispatch', :i,"
                " CAST(:m AS jsonb)) RETURNING id"
            ),
            {
                "a": agent_id,
                "u": user_id,
                "s": status,
                "i": issue_id,
                "m": json.dumps(meta),
            },
        )
    ).scalar_one()


async def test_step_attempt_counter_increments_resets_and_keeps_other_keys(conn):
    from sqlalchemy import text

    from app.services.issues.execution_state import increment_step_attempt_stmt

    issue_id = await _issue(conn)

    async def bump(key):
        return (await conn.execute(increment_step_attempt_stmt(issue_id, key))).scalar()

    assert [await bump("wf:7") for _ in range(3)] == ["1", "2", "3"]
    assert await bump("wf:8") == "1"  # a new step replaces the old key
    state = (
        await conn.execute(
            text("SELECT execution_state FROM issues WHERE id = :i"), {"i": issue_id}
        )
    ).scalar_one()
    assert state == {"turn": 3, "step_attempts": {"wf:8": 1}}


async def test_prior_run_lookup_and_backlink(conn):
    from sqlalchemy import text

    from app.services.ai.runner.step_recovery import prior_run_stmt, supersede_stmt

    issue_id = await _issue(conn)
    await _run(conn, issue_id, meta={"dbos_step_key": "wf:5"})
    old = await _run(
        conn, issue_id, meta={"dbos_step_key": "wf:7", "view": {"steps": 2}}
    )

    row = (
        await conn.execute(prior_run_stmt("wf:7", issue_id=issue_id, user_id=None))
    ).first()
    assert (int(row[0]), row[1]) == (old, "running")
    none = (
        await conn.execute(prior_run_stmt("wf:9", issue_id=issue_id, user_id=None))
    ).first()
    assert none is None

    await conn.execute(supersede_stmt(old, "999"))
    meta, status = (
        await conn.execute(
            text("SELECT metadata_json, status FROM agent_runs WHERE id = :i"),
            {"i": old},
        )
    ).one()
    assert meta == {
        "dbos_step_key": "wf:7",
        "view": {"steps": 2},
        "superseded_by": "999",
    }
    assert status == "running"  # status is left to heartbeat_lost


async def test_worker_shutdown_flip_takes_only_running_rows(conn):
    from sqlalchemy import text

    from app.repositories.agent_runs_repository import worker_shutdown_stmt

    issue_id = await _issue(conn)
    live = await _run(conn, issue_id, meta={})
    done = await _run(conn, issue_id, meta={}, status="completed")

    rows = (await conn.execute(worker_shutdown_stmt([live, done]))).fetchall()
    assert [int(r.id) for r in rows] == [live]
    got = (
        await conn.execute(
            text(
                "SELECT status, error_code, turn_end_reason, ended_at IS NOT NULL"
                " FROM agent_runs WHERE id = :i"
            ),
            {"i": live},
        )
    ).one()
    assert tuple(got) == ("heartbeat_lost", "worker_shutdown", "heartbeat_lost", True)
    untouched = (
        await conn.execute(
            text("SELECT status, error_code FROM agent_runs WHERE id = :i"), {"i": done}
        )
    ).one()
    assert tuple(untouched) == ("completed", None)
