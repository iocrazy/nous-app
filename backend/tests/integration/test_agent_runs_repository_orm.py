"""Integration tests for AgentRunsRepositoryOrm (Task 5.3) against real PG.

The agent_runs repository runs on the SQLAlchemy 2.0 ORM session layer now —
``read_scope()`` for reads, ``write_scope()`` (which COMMITS) for writes. These
tests run real SQL against a real Postgres to prove:

  - The migrated read methods keep their exact dict return shapes (SELECT *
    for list_by_agent / get_by_id / list_children; the 9-column projection for
    monthly_usage_by_agent). The swap must be invisible to call sites.
  - **THE P0 REGRESSION**: ``request_cancel`` / ``mark_heartbeat_lost`` actually
    PERSIST. The old asyncpg path ran the write on a bare ``connect()`` (no txn)
    and silently rolled back on close, so a fresh read saw the OLD value. The
    ``*_commits_*`` tests open a SEPARATE fresh asyncpg connection and confirm
    the new value is there.
  - **TYPE PARITY**: ``status`` comes back as bare ``str`` (it's a Text column
    with a CHECK constraint, not a PG/SQLAlchemy enum), and no value is a
    SQLAlchemy MetaData object.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub schema.
Skips otherwise. Use the dev stack:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_agent_runs_repository_orm.py -v

Seed rows are tagged with a per-run prefix for predictable cleanup; the fixture
deletes them after each test even on failure.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_AGENT_PREFIX = "__test_orm_runs_agent_"


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN so
    read_scope()/write_scope() hit the test DB. Resets both singletons."""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def cleanup_test_rows(integration_db_url):
    """Delete agent_runs + ai_agents tagged with the test prefix
    (agent_runs cascade-deletes via the agent_id FK)."""
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM ai_agents WHERE name LIKE $1", _AGENT_PREFIX + "%"
        )
    finally:
        await conn.close()


async def _seed_agent_and_user(conn) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a throwaway ai_agent (cleaned up by the fixture) + grab a real
    user to satisfy the agent_runs FKs. Returns (agent_id, user_id)."""
    user_id = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not user_id:
        pytest.skip("No auth.users rows to satisfy FKs")
    agent_id = await conn.fetchval(
        "INSERT INTO ai_agents (name) VALUES ($1) RETURNING id",
        f"{_AGENT_PREFIX}{uuid.uuid4().hex[:8]}",
    )
    return agent_id, user_id


async def _insert_run(conn, agent_id, user_id, **overrides) -> dict:
    """INSERT one agent_runs row and return it as a dict."""
    defaults = {
        "agent_id": agent_id,
        "user_id": user_id,
        "status": "running",
        "trigger": "test",
    }
    defaults.update(overrides)
    cols = list(defaults.keys())
    vals = list(defaults.values())
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    row = await conn.fetchrow(
        f"INSERT INTO agent_runs ({col_list}) VALUES ({placeholders}) RETURNING *",
        *vals,
    )
    return dict(row)


def _repo():
    from app.repositories.agent_runs_repository_orm import AgentRunsRepositoryOrm

    return AgentRunsRepositoryOrm()


# ─── Reads ─────────────────────────────────────────────────────────────


async def test_get_by_id_str_input_and_type_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """agent_runs.id is BIGINT; str path-param must coerce. AND: status must
    surface as bare str (Text column, but pin it so a future enum migration
    doesn't silently leak an enum member)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        run = await _insert_run(conn, agent_id, user_id)
    finally:
        await conn.close()

    result = await _repo().get_by_id(str(run["id"]), user_id=user_id)
    assert result is not None, "get_by_id failed with str input"
    assert int(result["id"]) == run["id"]
    assert result["status"] == "running"
    assert type(result["status"]) is str
    # SELECT * parity — full column set, no MetaData leak.
    assert "metadata_json" in result
    assert "cancel_requested" in result
    assert result["cancel_requested"] is False


async def test_get_by_id_wrong_user_reads_none(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """user_id filter doubles as authz: a stray run_id from another user
    reads as None (404), not 403 — no existence leak."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        run = await _insert_run(conn, agent_id, user_id)
    finally:
        await conn.close()

    other_user = uuid.uuid4()
    result = await _repo().get_by_id(str(run["id"]), user_id=other_user)
    assert result is None


async def test_list_by_agent_shape_and_total(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """{"items": [...], "total": N} shape; count + page agree; ordered
    newest-first by started_at."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        base = datetime.now(timezone.utc)
        for i in range(3):
            await _insert_run(
                conn,
                agent_id,
                user_id,
                started_at=base - timedelta(minutes=i),
            )
    finally:
        await conn.close()

    result = await _repo().list_by_agent(agent_id=agent_id, user_id=user_id)
    assert set(result.keys()) == {"items", "total"}
    assert result["total"] == 3
    assert isinstance(result["total"], int)
    assert len(result["items"]) == 3
    # Newest-first: started_at descending.
    started = [r["started_at"] for r in result["items"]]
    assert started == sorted(started, reverse=True)
    # SELECT * dict, not an ORM object.
    assert isinstance(result["items"][0], dict)
    assert result["items"][0]["status"] == "running"


async def test_list_children_direct_only(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Direct children of one parent run, newest first."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        parent = await _insert_run(conn, agent_id, user_id)
        for _ in range(2):
            await _insert_run(conn, agent_id, user_id, parent_run_id=parent["id"])
    finally:
        await conn.close()

    children = await _repo().list_children(str(parent["id"]), user_id=user_id)
    assert len(children) == 2
    assert all(int(c["parent_run_id"]) == parent["id"] for c in children)


async def test_monthly_usage_projection_shape(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """The 9-column projection — exact keys, raw rows (caller groups)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        now = datetime.now(timezone.utc)
        await _insert_run(
            conn,
            agent_id,
            user_id,
            started_at=now,
            prompt_tokens=10,
            completion_tokens=5,
        )
    finally:
        await conn.close()

    month_start = datetime.now(timezone.utc) - timedelta(days=1)
    month_end = datetime.now(timezone.utc) + timedelta(days=1)
    rows = await _repo().monthly_usage_by_agent(
        month_start=month_start, month_end=month_end
    )
    # Filter via the STRING contract (PostgREST renders uuid as a JSON str) —
    # NOT `r["agent_id"] == agent_id` (UUID==UUID would pass even if the repo
    # leaked native UUIDs, masking the consumer-path regression).
    ours = [r for r in rows if r["agent_id"] == str(agent_id)]
    assert len(ours) == 1
    row = ours[0]
    assert set(row.keys()) == {
        "agent_id",
        "user_id",
        "team_id",
        "project_id",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost_cents",
    }
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    # total_tokens is a Computed column (prompt + completion).
    assert row["total_tokens"] == 15
    assert type(row["status"]) is str


async def test_monthly_usage_matches_rest_value_types_consumer_path(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """REST→ORM value-type parity for the /usage USER-scope consumer's
    PYTHON-level type-sensitive ops (ai_library_router.get_usage). The live
    baseline is the REST (supabase-py/PostgREST) base; the consumer relies on:

      - user-scope filter:  str(r.get("user_id")) == str(user_uuid)
                            → needs user_id comparable to a uuid str
      - agent enrichment:   UUID(str(r["agent_id"]))
                            → needs agent_id parseable as a uuid
      - cost rollup:        float(r["cost_cents"])
                            → REST renders numeric as a JSON str; float() works

    PostgREST renders uuid as a JSON str and numeric as a JSON str, so the repo
    coerces user_id / agent_id / cost_cents to str. If it instead leaked native
    uuid.UUID / Decimal the user-scope filter / enrichment would silently break.
    This test exercises those exact ops (not just dict-key shape).

    NOTE: team_id / project_id are deliberately NOT covered here — they are
    bigint and the REST base returned them as native int; see
    ``test_monthly_usage_team_project_scope_int_parity``."""
    cost = Decimal("0.50")
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        await _insert_run(
            conn,
            agent_id,
            user_id,
            started_at=datetime.now(timezone.utc),
            prompt_tokens=100,
            completion_tokens=50,
            cost_cents=cost,
        )
    finally:
        await conn.close()

    month_start = datetime.now(timezone.utc) - timedelta(days=1)
    month_end = datetime.now(timezone.utc) + timedelta(days=1)
    rows = await _repo().monthly_usage_by_agent(
        month_start=month_start, month_end=month_end
    )
    ours = [r for r in rows if str(r.get("agent_id")) == str(agent_id)]
    assert len(ours) == 1
    row = ours[0]

    # 1378 consumer path: user_id must be a bare str that equals str(user_uuid).
    assert type(row["user_id"]) is str, (
        f"user_id leaked {type(row['user_id']).__name__} — the user-scope "
        f"filter (r['user_id'] == str(user_uuid)) returns ZERO for every user."
    )
    assert row["user_id"] == str(user_id)

    # 1412 consumer path: UUID(agent_id) must parse (PostgREST returns str).
    assert type(row["agent_id"]) is str
    assert UUID(row["agent_id"]) == agent_id  # would raise on a native UUID

    # cost_cents: PostgREST renders numeric as a JSON str; float() must still
    # work (the consumer does float(r["cost_cents"])).
    assert (
        type(row["cost_cents"]) is str
    ), "cost_cents must match the REST numeric→str contract"
    assert float(row["cost_cents"]) == 0.5


async def test_monthly_usage_team_project_scope_int_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """REST→ORM value-type parity for the /usage TEAM and PROJECT scopes.

    The consumer endpoint declares ``team_id: int | None`` / ``project_id: int
    | None`` and filters with a BARE int compare (NO str() wrapping, unlike the
    user_id line):

        elif scope == "team":    rows = [r for r in rows if r.get("team_id") == team_id]
        else:                    rows = [r for r in rows if r.get("project_id") == project_id]

    team_id / project_id are bigint; the backend supabase-py base returned them
    as native Python int (JSON number → int — the bigint→str precision concern
    is a FRONTEND/JS issue via bigIntSafeFetch, not backend). So the repo must
    surface them as int. If they're coerced to str, ``"123" == 123`` is False
    and the team/project usage dashboards return ZERO. This pins the int type +
    the int equality the consumer's filter performs."""
    team_id = 123456789012345
    project_id = 987654321098765
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        await _insert_run(
            conn,
            agent_id,
            user_id,
            started_at=datetime.now(timezone.utc),
            team_id=team_id,
            project_id=project_id,
        )
    finally:
        await conn.close()

    month_start = datetime.now(timezone.utc) - timedelta(days=1)
    month_end = datetime.now(timezone.utc) + timedelta(days=1)
    rows = await _repo().monthly_usage_by_agent(
        month_start=month_start, month_end=month_end
    )
    ours = [r for r in rows if str(r.get("agent_id")) == str(agent_id)]
    assert len(ours) == 1
    row = ours[0]

    # team-scope filter: r.get("team_id") == team_id (int == int).
    assert type(row["team_id"]) is int, (
        f"team_id leaked {type(row['team_id']).__name__} — the team-scope "
        f"filter (r['team_id'] == team_id:int) returns ZERO; bigint must stay int."
    )
    assert row["team_id"] == team_id  # the consumer's exact bare-int compare

    # project-scope filter: r.get("project_id") == project_id (int == int).
    assert type(row["project_id"]) is int, (
        f"project_id leaked {type(row['project_id']).__name__} — the "
        f"project-scope filter returns ZERO; bigint must stay int."
    )
    assert row["project_id"] == project_id


# ─── Writes (committing — fixes the P0) ─────────────────────────────────


async def _fetch_run_raw(integration_db_url: str, run_id: int) -> dict | None:
    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow("SELECT * FROM agent_runs WHERE id = $1", run_id)
        return dict(row) if row else None
    finally:
        await conn.close()


async def test_request_cancel_commits_P0_REGRESSION(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """THE P0 REGRESSION TEST. The old asyncpg path ran the UPDATE on a bare
    connect() and silently rolled back on close. This flips cancel_requested,
    opens a SEPARATE fresh connection, and asserts the new value persisted."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        run = await _insert_run(conn, agent_id, user_id)
    finally:
        await conn.close()

    ok = await _repo().request_cancel(str(run["id"]), user_id=user_id)
    assert ok is True

    # Fresh, independent connection — proves the commit, not a buffer.
    persisted = await _fetch_run_raw(integration_db_url, run["id"])
    assert persisted is not None
    assert persisted["cancel_requested"] is True, (
        "request_cancel did not persist — silent-rollback P0 is back. The "
        "write must go through write_scope() (which commits)."
    )


async def test_request_cancel_no_match_returns_false(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """A terminal (non-running) run, or one owned by another user, must not
    flip — returns False."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        run = await _insert_run(conn, agent_id, user_id, status="completed")
    finally:
        await conn.close()

    ok = await _repo().request_cancel(str(run["id"]), user_id=user_id)
    assert ok is False
    # Wrong user on a running row → also False.
    conn = await asyncpg.connect(integration_db_url)
    try:
        run2 = await _insert_run(conn, agent_id, user_id)
    finally:
        await conn.close()
    assert await _repo().request_cancel(str(run2["id"]), user_id=uuid.uuid4()) is False


async def test_mark_heartbeat_lost_commits_and_counts(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Bulk flip stale running rows → heartbeat_lost. Returns the count AND
    commits (fresh read-back confirms status flipped + ended_at set)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        fresh = datetime.now(timezone.utc)
        stale_run = await _insert_run(conn, agent_id, user_id, heartbeat_at=old)
        # A fresh-heartbeat running row that must NOT be swept.
        live_run = await _insert_run(conn, agent_id, user_id, heartbeat_at=fresh)
    finally:
        await conn.close()

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
    count = await _repo().mark_heartbeat_lost(stale_before=stale_before)
    assert isinstance(count, int)
    assert count >= 1

    swept = await _fetch_run_raw(integration_db_url, stale_run["id"])
    assert swept["status"] == "heartbeat_lost"
    assert swept["error_code"] == "heartbeat_lost"
    assert swept["ended_at"] is not None

    # The live row is untouched.
    untouched = await _fetch_run_raw(integration_db_url, live_run["id"])
    assert untouched["status"] == "running"


async def test_mark_heartbeat_lost_idempotent(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """A second sweep with the same cutoff matches 0 rows (already flipped)
    — naturally idempotent, safe under DBOS/sweeper retry."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        agent_id, user_id = await _seed_agent_and_user(conn)
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        await _insert_run(conn, agent_id, user_id, heartbeat_at=old)
    finally:
        await conn.close()

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
    first = await _repo().mark_heartbeat_lost(stale_before=stale_before)
    assert first >= 1
    # Second run: the row is no longer 'running' → 0 matches for OUR seed.
    second = await _repo().mark_heartbeat_lost(stale_before=stale_before)
    # Other concurrent rows may exist on dev, but our seeded row contributes 0.
    assert isinstance(second, int)
