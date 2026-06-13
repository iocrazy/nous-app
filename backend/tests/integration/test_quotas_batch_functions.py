"""Integration tests for mig 287 batch quota functions vs real PG.

Money-proxy logic (points), so the SQL gets exercised against the dev
stack, not just mocked: grant idempotency (claim-first, re-run safe),
balance credit + transaction rows, reclaim with partial consumption,
balance cap, and gift bookkeeping.

Every test runs inside a transaction that is ROLLED BACK — the grant
function touches every personal team on the instance for the test
gift_date, so nothing may persist. A throwaway auth.users row (no
triggers on auth.users — verified) keeps our team's claims
deterministic under the UNIQUE(user_id, gift_date) constraint.

The migration itself (CREATE OR REPLACE, idempotent) is applied by the
fixture, so this also validates that 287 parses and runs.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_quotas_batch_functions.py -v
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "287_quotas_batch_functions.sql"
)
# Far past — can never collide with a real daily-workflow gift_date.
_GIFT_DATE = date(1999, 1, 1)

AMOUNT = 100


@pytest.fixture()
async def ctx():
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set")
    connection = await asyncpg.connect(_TEST_DSN)
    await connection.execute(_MIGRATION.read_text())  # idempotent
    tr = connection.transaction()
    await tr.start()
    user_id = await connection.fetchval(
        "INSERT INTO auth.users (id, email) "
        "VALUES (gen_random_uuid(), "
        "'tqb-' || floor(random()*1e9)::text || '@test.local') RETURNING id"
    )
    team_id = await connection.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code, kind) "
        "VALUES ('__test_quotas_batch_team', $1, "
        "'tqb-' || floor(random()*1e9)::text, 'personal') RETURNING id",
        user_id,
    )
    try:
        yield connection, user_id, team_id
    finally:
        await tr.rollback()
        await connection.close()


async def _grant(conn: asyncpg.Connection, gift_date: date):
    return await conn.fetchrow(
        "SELECT * FROM public.grant_daily_free_points_batch($1, $2)",
        AMOUNT,
        gift_date,
    )


async def _balance(conn: asyncpg.Connection, team_id: int):
    return await conn.fetchval(
        "SELECT points_balance FROM team_quotas WHERE team_id = $1", team_id
    )


async def test_grant_credits_and_is_idempotent(ctx) -> None:
    conn, user_id, team_id = ctx

    first = await _grant(conn, _GIFT_DATE)
    assert first["granted"] >= 1  # ours (other personal teams also count)

    assert await _balance(conn, team_id) == AMOUNT

    txn = await conn.fetchrow(
        "SELECT amount, balance_after, type, user_id FROM point_transactions "
        "WHERE team_id = $1 AND type = 'daily_gift'",
        team_id,
    )
    assert txn["amount"] == AMOUNT
    assert txn["balance_after"] == AMOUNT
    assert txn["user_id"] == user_id

    gift = await conn.fetchrow(
        "SELECT status, amount_granted FROM daily_point_gifts "
        "WHERE team_id = $1 AND gift_date = $2",
        team_id,
        _GIFT_DATE,
    )
    assert gift["status"] == "granted"
    assert gift["amount_granted"] == AMOUNT

    # Re-run: every claim conflicts → granted 0, no double credit.
    second = await _grant(conn, _GIFT_DATE)
    assert second["granted"] == 0
    assert await _balance(conn, team_id) == AMOUNT
    assert (
        await conn.fetchval(
            "SELECT COUNT(*) FROM point_transactions "
            "WHERE team_id = $1 AND type = 'daily_gift'",
            team_id,
        )
        == 1
    )


async def test_reclaim_partial_consumption(ctx) -> None:
    conn, user_id, team_id = ctx

    await _grant(conn, _GIFT_DATE)
    # Consume 30 of the 100 after the grant.
    await conn.execute(
        "INSERT INTO point_transactions "
        "(team_id, user_id, amount, balance_after, type, description) "
        "VALUES ($1, $2, -30, 70, 'consume', 'it consume')",
        team_id,
        user_id,
    )
    await conn.execute(
        "UPDATE team_quotas SET points_balance = 70 WHERE team_id = $1", team_id
    )

    row = await conn.fetchrow(
        "SELECT * FROM public.reclaim_daily_free_points_batch($1)", _GIFT_DATE
    )
    assert row["reclaimed_count"] >= 1
    assert row["total_reclaimed"] >= 70

    assert await _balance(conn, team_id) == 0  # the unused 70 clawed back

    gift = await conn.fetchrow(
        "SELECT status, amount_consumed, amount_reclaimed FROM daily_point_gifts "
        "WHERE team_id = $1 AND gift_date = $2",
        team_id,
        _GIFT_DATE,
    )
    assert gift["status"] == "reclaimed"
    assert gift["amount_consumed"] == 30
    assert gift["amount_reclaimed"] == 70

    reclaim_txn = await conn.fetchrow(
        "SELECT amount, balance_after FROM point_transactions "
        "WHERE team_id = $1 AND type = 'daily_gift_reclaim'",
        team_id,
    )
    assert reclaim_txn["amount"] == -70
    assert reclaim_txn["balance_after"] == 0

    # Second run: the gift is no longer 'granted' → untouched.
    row2 = await conn.fetchrow(
        "SELECT * FROM public.reclaim_daily_free_points_batch($1)", _GIFT_DATE
    )
    assert row2["reclaimed_count"] == 0


async def test_reclaim_caps_at_current_balance(ctx) -> None:
    conn, _user_id, team_id = ctx

    await _grant(conn, _GIFT_DATE)
    # Balance drained below the unused amount (spent through paths that
    # don't write 'consume' transactions).
    await conn.execute(
        "UPDATE team_quotas SET points_balance = 40 WHERE team_id = $1", team_id
    )

    await conn.fetchrow(
        "SELECT * FROM public.reclaim_daily_free_points_batch($1)", _GIFT_DATE
    )

    assert await _balance(conn, team_id) == 0  # capped at 40, never negative

    gift = await conn.fetchrow(
        "SELECT amount_reclaimed FROM daily_point_gifts "
        "WHERE team_id = $1 AND gift_date = $2",
        team_id,
        _GIFT_DATE,
    )
    assert gift["amount_reclaimed"] == 40


async def test_fully_consumed_gift_reclaims_nothing(ctx) -> None:
    conn, user_id, team_id = ctx

    await _grant(conn, _GIFT_DATE)
    await conn.execute(
        "INSERT INTO point_transactions "
        "(team_id, user_id, amount, balance_after, type, description) "
        "VALUES ($1, $2, -100, 0, 'consume', 'it consume all')",
        team_id,
        user_id,
    )
    await conn.execute(
        "UPDATE team_quotas SET points_balance = 0 WHERE team_id = $1", team_id
    )

    await conn.fetchrow(
        "SELECT * FROM public.reclaim_daily_free_points_batch($1)", _GIFT_DATE
    )

    gift = await conn.fetchrow(
        "SELECT status, amount_consumed, amount_reclaimed FROM daily_point_gifts "
        "WHERE team_id = $1 AND gift_date = $2",
        team_id,
        _GIFT_DATE,
    )
    # Fully used: still flipped to 'reclaimed', nothing clawed back,
    # and no reclaim transaction was written.
    assert gift["status"] == "reclaimed"
    assert gift["amount_consumed"] == 100
    assert gift["amount_reclaimed"] == 0
    assert (
        await conn.fetchval(
            "SELECT COUNT(*) FROM point_transactions "
            "WHERE team_id = $1 AND type = 'daily_gift_reclaim'",
            team_id,
        )
        == 0
    )
