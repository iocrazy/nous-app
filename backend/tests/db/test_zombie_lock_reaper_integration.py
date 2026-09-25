"""The zombie-lock reaper's SQL and the parked-wait lookup against the real
schema (FH3 T6, recon-5 §6 items 8–9).

The unit tests grep the SQL; only Postgres can say that the LEFT JOIN, the
``NOT IN`` over a missing engine row, the jsonb ``-`` with a text operand and
the ``IS NOT DISTINCT FROM`` CAS mean what the reaper needs. First test in the
repo to write rows into ``dbos.workflow_status`` (shape: ``ci_bootstrap.sql``).

Everything happens on one connection inside a transaction that is rolled
back, so nothing persists in the shared drift database; the candidate read is
filtered to this test's own issues. ``session_replication_role = replica``
(SET LOCAL) stands in for the reaper's ``SET LOCAL ROLE service_role``: the
drift DB's service_role has no grants, and replica mode suppresses the mig-170
allowlist trigger the same way (as in ``test_fh2_lifecycle_sql_integration``).
The reaper's loop (WARN / raced / ERROR-continue) is unit-tested.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
if not _TEST_DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

HOUR_AGO = dt.timedelta(hours=1)
MINUTE_AGO = dt.timedelta(minutes=1)


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


async def _sql(conn, sql: str, **params):
    from sqlalchemy import text

    return await conn.execute(text(sql), params)


@pytest.fixture
async def world(conn):
    uid = uuid.uuid4()
    await _sql(conn, "INSERT INTO auth.users (id) VALUES (:u)", u=uid)
    agent_id = (
        await _sql(
            conn,
            "INSERT INTO ai_agents (name, slug, user_id, created_by, is_system_preset)"
            " VALUES ('Zombie Lock Fixture', :s, :u, :u, false) RETURNING id",
            s=f"zl-{uuid.uuid4().hex[:10]}",
            u=uid,
        )
    ).scalar_one()
    return {"user": uid, "agent": agent_id, "prefix": f"zl-{uuid.uuid4().hex[:10]}-"}


async def _wf(conn, world, name: str, status: str | None) -> str:
    wf = world["prefix"] + name
    if status is not None:
        await _sql(
            conn,
            "INSERT INTO dbos.workflow_status (workflow_uuid, status, name)"
            " VALUES (:w, :s, 'execute_issue')",
            w=wf,
            s=status,
        )
    return wf


async def _issue(conn, world, *, wf, status, lock_age, state) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    return (
        await _sql(
            conn,
            """INSERT INTO issues (issue_number, identifier, title, status, priority,
                                   origin_kind, created_by_user_id,
                                   execution_locked_at, dbos_workflow_id,
                                   execution_state)
               VALUES ((SELECT COALESCE(MAX(issue_number), 0) + 1 FROM issues),
                       :ident, 'Zombie lock fixture', :status, 'medium', 'manual',
                       :u, :locked, :wf, CAST(:state AS jsonb))
               RETURNING id""",
            ident=f"ZL-{uuid.uuid4().hex[:8]}",
            status=status,
            u=world["user"],
            locked=None if lock_age is None else now - lock_age,
            wf=wf,
            state=json.dumps(state),
        )
    ).scalar_one()


def _awaiting(since: dt.datetime, *, answered: bool = False) -> dict:
    marker = {"since": since.isoformat(), "prompt": "Which one?"}
    if answered:
        marker["answered_at"] = since.isoformat()
    return {"awaiting_input": marker, "agent_outcome": "needs_input"}


async def _kinds(conn, world) -> dict[str, int]:
    """recon-5 §6 item 8's five kinds, plus a dispatch-in-flight row."""
    old = dt.datetime.now(dt.timezone.utc) - HOUR_AGO
    spec = {
        # name: (wf status, issue status, lock age, execution_state)
        "cancelled_old": ("CANCELLED", "needs_followup", HOUR_AGO, _awaiting(old)),
        "reply_turn": ("SUCCESS", "in_progress", HOUR_AGO, {}),
        "parked_pending": ("PENDING", "needs_followup", HOUR_AGO, _awaiting(old)),
        "no_wf_row": (None, "cancelled", HOUR_AGO, {"agent_outcome": "needs_input"}),
        "fresh_lock": ("CANCELLED", "needs_followup", MINUTE_AGO, {}),
        "dispatching": ("CANCELLED", "todo", HOUR_AGO, {"dispatching": {"wf": "x"}}),
    }
    ids = {}
    for name, (wf_status, status, lock_age, state) in spec.items():
        wf = await _wf(conn, world, name, wf_status)
        ids[name] = await _issue(
            conn, world, wf=wf, status=status, lock_age=lock_age, state=state
        )
    # A reply turn holds the lock without writing dbos_workflow_id; its live
    # run is what keeps the reaper away from the SUCCESS dispatch id.
    await _sql(
        conn,
        "INSERT INTO agent_runs (agent_id, user_id, status, trigger, issue_id)"
        " VALUES (:a, :u, 'running', 'issue_dispatch', :i)",
        a=world["agent"],
        u=world["user"],
        i=ids["reply_turn"],
    )
    return ids


async def _candidates(conn, ids: dict[str, int]) -> list[dict]:
    from app.agent_framework import input_gate as g

    rows = await _sql(conn, g._ZOMBIE_LOCK_ROWS_SQL, limit=100_000, grace_s=600)
    wanted = set(ids.values())
    return [dict(r) for r in rows.mappings() if r["issue_id"] in wanted]


async def _release(conn, row: dict) -> int:
    from app.agent_framework import input_gate as g

    stmt = g.zombie_lock_release_stmt(
        issue_id=row["issue_id"],
        seen_locked_at=row["execution_locked_at"],
        seen_wf=row["dbos_workflow_id"],
    )
    return (await conn.execute(stmt)).rowcount


async def _issues(conn, ids: dict[str, int]) -> dict[int, dict]:
    rows = await _sql(
        conn,
        "SELECT id, status, execution_locked_at, execution_state FROM issues"
        " WHERE id = ANY(:ids)",
        ids=list(ids.values()),
    )
    return {r["id"]: dict(r) for r in rows.mappings()}


async def test_only_terminal_or_missing_workflows_with_old_locks_are_released(
    conn, world
):
    ids = await _kinds(conn, world)
    before = {i: r["status"] for i, r in (await _issues(conn, ids)).items()}

    candidates = await _candidates(conn, ids)
    by_id = {r["issue_id"]: r for r in candidates}
    assert set(by_id) == {ids["cancelled_old"], ids["no_wf_row"]}
    assert by_id[ids["cancelled_old"]]["wf_status"] == "CANCELLED"
    assert by_id[ids["no_wf_row"]]["wf_status"] is None

    for row in candidates:
        assert await _release(conn, row) == 1

    after = await _issues(conn, ids)
    for name in ("cancelled_old", "no_wf_row"):
        row = after[ids[name]]
        assert row["execution_locked_at"] is None, name
        state = row["execution_state"]
        assert "awaiting_input" not in state and "dispatching" not in state, name
        assert state.get("agent_outcome") == "needs_input", name
    for name in ("reply_turn", "parked_pending", "fresh_lock", "dispatching"):
        assert after[ids[name]]["execution_locked_at"] is not None, name
    assert {i: r["status"] for i, r in after.items()} == before  # never touched


async def test_a_lock_retaken_between_read_and_write_is_not_released(conn, world):
    ids = await _kinds(conn, world)
    [row] = [
        r for r in await _candidates(conn, ids) if r["issue_id"] == ids["cancelled_old"]
    ]
    new_wf = await _wf(conn, world, "redispatch", "PENDING")
    await _sql(
        conn,
        "UPDATE issues SET execution_locked_at = clock_timestamp(),"
        " dbos_workflow_id = :wf WHERE id = :id",
        wf=new_wf,
        id=ids["cancelled_old"],
    )
    assert await _release(conn, row) == 0
    after = (await _issues(conn, ids))[ids["cancelled_old"]]
    assert after["execution_locked_at"] is not None
    assert "awaiting_input" in after["execution_state"]


async def test_a_lock_without_a_workflow_id_is_matched_by_the_cas(conn, world):
    """A lock only a reply turn ever took has ``dbos_workflow_id`` NULL: it is
    a candidate (LEFT JOIN, no engine row) and the CAS still matches it."""
    ids = {
        "null_wf": await _issue(
            conn, world, wf=None, status="needs_followup", lock_age=HOUR_AGO, state={}
        )
    }
    [row] = await _candidates(conn, ids)
    assert row["dbos_workflow_id"] is None and row["wf_status"] is None
    assert await _release(conn, row) == 1


async def test_parked_lookup_names_only_live_unanswered_waits(conn, world):
    """What the health sweeper's age backstop asks before cancelling."""
    from app.repositories.agent_run_inbox_repository import parked_workflow_ids_stmt
    from app.workflows import workflow_health_sweeper as hs

    now = dt.datetime.now(dt.timezone.utc)
    seven_h = now - dt.timedelta(hours=7)
    cases = {
        "parked": (_awaiting(seven_h), HOUR_AGO),
        "answered": (_awaiting(seven_h, answered=True), HOUR_AGO),
        "past_ttl": (_awaiting(now - dt.timedelta(hours=80)), HOUR_AGO),
        "no_lock": (_awaiting(seven_h), None),
        "bad_since": ({"awaiting_input": {"since": "soon"}}, HOUR_AGO),
    }
    wfs = {}
    for name, (state, lock_age) in cases.items():
        wfs[name] = await _wf(conn, world, f"hs-{name}", "PENDING")
        await _issue(
            conn,
            world,
            wf=wfs[name],
            status="needs_followup",
            lock_age=lock_age,
            state=state,
        )
    stmt = parked_workflow_ids_stmt(list(wfs.values()), hs._parked_floor(now))
    found = set((await conn.execute(stmt)).scalars().all())
    assert found == {wfs["parked"]}
