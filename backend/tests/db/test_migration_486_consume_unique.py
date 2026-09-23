"""mig 486 against a real Postgres: one ``consume`` per agent run.

Why this has to run on a live database
======================================
The guarantee is a **partial unique index**, i.e. Postgres deciding which rows
the predicate covers. A stubbed session has no index at all, so it can neither
reject the duplicate nor prove the neighbours (a refund for the same run, a
consume for some other reference type) are left alone.

Two kinds of case:

* ``test_applied_index_is_unique`` reads the catalog of the database as it
  stands — RED on a database that has not had 486 applied, GREEN after. It is
  the one that notices "the file merged but the migration never ran".
* The behaviour cases replay the migration body **from the file** inside a
  rolled-back transaction, so they prove what the file does rather than a
  retyped copy of it.

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_486_consume_unique.py -v

Skips cleanly when the DSN is unset. The schema-drift workflow runs it through
pytest-no-full-skip.sh, so a full skip there is RED, not green.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 486 needs a real database.",
)

_INDEX = "idx_point_transactions_agent_run_consume"

#: backend/tests/db/ → repo root → the migration under test.
_MIGRATION_SQL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "486_point_transactions_agent_run_consume_unique.sql"
)


def _body() -> str:
    """The migration with its ``--`` comment lines removed (none sit inside
    the COMMENT literal). Executed argument-free so asyncpg uses the simple
    protocol and accepts the multi-statement file."""
    return "\n".join(
        line
        for line in _MIGRATION_SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


@pytest.fixture
async def conn():
    """A connection whose writes — including the replayed DDL — are always
    rolled back."""
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _mk_team(conn) -> int:
    """``point_transactions.team_id`` is NOT NULL and FKs to ``teams``, which
    wants name / owner_id (→ ``auth.users``) / invite_code."""
    owner = uuid.uuid4()
    await conn.execute(
        "INSERT INTO auth.users (id, email) VALUES ($1, $2)",
        owner,
        f"consume-unique-{uuid.uuid4().hex[:8]}@test.dev",
    )
    return await conn.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code) "
        "VALUES ($1, $2, $3) RETURNING id",
        "Consume Unique Test Team",
        owner,
        uuid.uuid4().hex[:12],
    )


async def _ledger(conn, team: int, *, type_: str, ref_type: str, ref_id: str):
    """``amount`` / ``balance_after`` / ``type`` are the other NOT NULLs."""
    await conn.execute(
        "INSERT INTO public.point_transactions "
        "  (team_id, amount, balance_after, type, reference_type, reference_id) "
        "VALUES ($1, $2, 0, $3, $4, $5)",
        team,
        -10 if type_ == "consume" else 10,
        type_,
        ref_type,
        ref_id,
    )


@_skip
async def test_applied_index_is_unique(conn):
    """The database as it stands, no replay: 486 has actually run."""
    row = await conn.fetchrow(
        "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) AS pred "
        "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = $1",
        _INDEX,
    )
    assert row is not None, f"{_INDEX} is missing"
    assert row["indisunique"], f"{_INDEX} exists but is not UNIQUE — 486 not applied"
    assert "consume" in row["pred"] and "agent_run" in row["pred"]


@_skip
async def test_second_consume_for_the_same_run_is_rejected(conn):
    await conn.execute(_body())
    team = await _mk_team(conn)
    run = str(uuid.uuid4().int % 10**18)
    await _ledger(conn, team, type_="consume", ref_type="agent_run", ref_id=run)
    with pytest.raises(asyncpg.UniqueViolationError) as exc:
        await _ledger(conn, team, type_="consume", ref_type="agent_run", ref_id=run)
    assert exc.value.constraint_name == _INDEX


@_skip
async def test_consume_and_refund_for_the_same_run_coexist(conn):
    """A refund is the normal companion of a consume — the predicate must
    not reach it (and 477's refund index stays its own)."""
    await conn.execute(_body())
    team = await _mk_team(conn)
    run = str(uuid.uuid4().int % 10**18)
    await _ledger(conn, team, type_="consume", ref_type="agent_run", ref_id=run)
    await _ledger(conn, team, type_="refund", ref_type="agent_run", ref_id=run)
    n = await conn.fetchval(
        "SELECT count(*) FROM public.point_transactions WHERE reference_id = $1",
        run,
    )
    assert n == 2


@_skip
async def test_same_reference_id_under_another_reference_type_is_allowed(conn):
    """Uniqueness is per agent run, not per reference_id string."""
    await conn.execute(_body())
    team = await _mk_team(conn)
    ref = str(uuid.uuid4().int % 10**18)
    await _ledger(conn, team, type_="consume", ref_type="agent_run", ref_id=ref)
    await _ledger(conn, team, type_="consume", ref_type="media_task", ref_id=ref)
    await _ledger(conn, team, type_="consume", ref_type="media_task", ref_id=ref)
    n = await conn.fetchval(
        "SELECT count(*) FROM public.point_transactions WHERE reference_id = $1",
        ref,
    )
    assert n == 3
