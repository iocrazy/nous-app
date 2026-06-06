"""Integration test for the atomic order-REFUND-and-debit RPC (mig 261).

``rpc_refund_order_and_debit(p_order_id BIGINT)`` replaces the old two-await
``update_order(...'refunded'...)`` + ``add_points(amount=-points, ...)`` block in
admin/credits_router.refund_order. The old path was doubly broken: the two
writes were non-atomic, AND ``add_points`` rejects ``amount <= 0`` (silent
no-op), so refunds marked the order refunded but NEVER clawed back the points.

The RPC marks the order refunded AND debits team_quotas AND writes the
``(reference_type='order_refund')`` ledger row in ONE transaction, with:

  * POLICY = CLAMP AT 0: deduct = LEAST(points_amount, current_balance); the
    balance never goes negative; already-spent points are not clawed back.
  * partial-unique index ``idx_point_transactions_unique_order_refund`` on
    (reference_type, reference_id) WHERE reference_type='order_refund' enforces
    at-most-one refund per order — distinct from the confirm path's 'order'.
  * a duplicate call returns already_refunded=true, points_debited=0, no balance
    change.
  * a 'refunded' order with NO ledger row (the crash gap) is healed once.

This drives the *real* mig-261 function body against a real PG.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_refund_order_debit_rpc.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Bigints well inside int8 range; unique to this test.
_TEAM_ID = 9_261_000_000_000_001
_ORDER_ID = 9_261_000_000_000_011
_POINTS = 100

_MIG_261 = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "261_atomic_refund_order_debit.sql"
)


async def _apply_mig_261(connection: asyncpg.Connection) -> None:
    """Apply the real mig-261 body (index + function) verbatim."""
    sql = _MIG_261.read_text(encoding="utf-8")
    sql = sql.replace("NOTIFY pgrst, 'reload schema';", "")
    await connection.execute(sql)


@pytest.fixture
async def conn():
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration test")
    connection = await asyncpg.connect(_TEST_DSN)
    try:
        await _cleanup(connection)
        await _apply_mig_261(connection)
        yield connection
    finally:
        await _cleanup(connection)
        await connection.close()


async def _cleanup(connection: asyncpg.Connection) -> None:
    await connection.execute(
        "DELETE FROM point_transactions WHERE team_id = $1", _TEAM_ID
    )
    await connection.execute("DELETE FROM orders WHERE id = $1", _ORDER_ID)
    await connection.execute("DELETE FROM team_quotas WHERE team_id = $1", _TEAM_ID)
    await connection.execute("DELETE FROM teams WHERE id = $1", _TEAM_ID)


async def _seed_team(connection: asyncpg.Connection) -> str:
    creator_id = await connection.fetchval("SELECT id FROM auth.users LIMIT 1")
    if creator_id is None:
        pytest.skip("no auth.users row to satisfy teams.owner_id / orders.user_id FK")
    await connection.execute(
        "INSERT INTO teams (id, name, owner_id, invite_code) VALUES ($1, $2, $3, $4)",
        _TEAM_ID,
        "Refund Test Team",
        creator_id,
        "rfndinv01",
    )
    return creator_id


async def _seed_order(
    connection: asyncpg.Connection,
    creator_id: str,
    *,
    payment_status: str = "paid",
    points: int = _POINTS,
) -> None:
    await connection.execute(
        """
        INSERT INTO orders
            (id, team_id, user_id, points_amount, amount_cents,
             payment_method, payment_status, expired_at)
        VALUES ($1, $2, $3, $4, 990, 'alipay', $5, now() + interval '1 hour')
        """,
        _ORDER_ID,
        _TEAM_ID,
        creator_id,
        points,
        payment_status,
    )


async def _seed_quota(connection: asyncpg.Connection, balance: int) -> None:
    await connection.execute(
        "INSERT INTO team_quotas (team_id, points_balance) VALUES ($1, $2)",
        _TEAM_ID,
        balance,
    )


async def _balance(connection: asyncpg.Connection) -> int | None:
    return await connection.fetchval(
        "SELECT points_balance FROM team_quotas WHERE team_id = $1", _TEAM_ID
    )


async def _ledger_count(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(
        "SELECT count(*) FROM point_transactions "
        "WHERE reference_type = 'order_refund' AND reference_id = $1",
        str(_ORDER_ID),
    )


async def _call(connection: asyncpg.Connection) -> dict:
    row = await connection.fetchrow(
        "SELECT * FROM rpc_refund_order_and_debit($1)", _ORDER_ID
    )
    return dict(row)


# ---------------------------------------------------------------------------


async def test_happy_path_paid_debits_once(conn):
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="paid")
    await _seed_quota(conn, _POINTS)  # balance >= points

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_refunded"] is False
    assert r["points_debited"] == _POINTS
    assert r["new_balance"] == 0
    assert r["reason"] is None

    assert await _balance(conn) == 0
    assert await _ledger_count(conn) == 1

    status = await conn.fetchval(
        "SELECT payment_status FROM orders WHERE id = $1", _ORDER_ID
    )
    assert status == "refunded"

    # Ledger row carries the negative (debit) amount.
    amt = await conn.fetchval(
        "SELECT amount FROM point_transactions "
        "WHERE reference_type = 'order_refund' AND reference_id = $1",
        str(_ORDER_ID),
    )
    assert amt == -_POINTS


async def test_clamp_at_zero_when_balance_below_points(conn):
    """POLICY: balance < points_amount -> deduct only the balance, never negative."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="paid", points=_POINTS)
    await _seed_quota(conn, 30)  # less than the 100-point order

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_refunded"] is False
    assert r["points_debited"] == 30  # clamped to available balance
    assert r["new_balance"] == 0  # never negative

    assert await _balance(conn) == 0
    assert await _ledger_count(conn) == 1

    amt = await conn.fetchval(
        "SELECT amount FROM point_transactions "
        "WHERE reference_type = 'order_refund' AND reference_id = $1",
        str(_ORDER_ID),
    )
    assert amt == -30


async def test_idempotent_double_call_debits_once(conn):
    """REQUIRED: calling twice debits -N once; 2nd call is a no-op."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="paid")
    await _seed_quota(conn, _POINTS)

    r1 = await _call(conn)
    assert r1["success"] is True
    assert r1["already_refunded"] is False
    assert r1["points_debited"] == _POINTS
    assert r1["new_balance"] == 0

    r2 = await _call(conn)
    assert r2["success"] is True
    assert r2["already_refunded"] is True
    assert r2["points_debited"] == 0
    assert r2["new_balance"] == 0

    # Balance debited exactly once; exactly one ledger row.
    assert await _balance(conn) == 0
    assert await _ledger_count(conn) == 1


async def test_crash_gap_refunded_no_ledger_heals(conn):
    """Order pre-set 'refunded' with NO ledger row (the crash gap) -> debit once."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="refunded")
    await _seed_quota(conn, _POINTS)
    assert await _ledger_count(conn) == 0

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_refunded"] is False
    assert r["points_debited"] == _POINTS
    assert r["new_balance"] == 0

    assert await _balance(conn) == 0
    assert await _ledger_count(conn) == 1

    # A second call is now idempotent.
    r2 = await _call(conn)
    assert r2["already_refunded"] is True
    assert await _balance(conn) == 0
    assert await _ledger_count(conn) == 1


@pytest.mark.parametrize("bad_status", ["pending", "failed", "expired"])
async def test_non_refundable_status_rejected(conn, bad_status):
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status=bad_status)
    await _seed_quota(conn, _POINTS)

    r = await _call(conn)
    assert r["success"] is False
    assert r["already_refunded"] is False
    assert r["points_debited"] == 0
    assert r["reason"] is not None
    assert bad_status in r["reason"]

    # No balance / ledger change; status untouched.
    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 0
    status = await conn.fetchval(
        "SELECT payment_status FROM orders WHERE id = $1", _ORDER_ID
    )
    assert status == bad_status


async def test_no_team_quota_deducts_zero(conn):
    """No team_quotas row -> nothing to debit, success with new_balance 0."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="paid")
    # No team_quotas row exists.
    assert await _balance(conn) is None

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_refunded"] is False
    assert r["points_debited"] == 0
    assert r["new_balance"] == 0

    # Order still marked refunded; ledger row written with amount 0.
    status = await conn.fetchval(
        "SELECT payment_status FROM orders WHERE id = $1", _ORDER_ID
    )
    assert status == "refunded"
    assert await _ledger_count(conn) == 1
    assert await _balance(conn) is None  # no quota row created


async def test_order_not_found(conn):
    r = await _call(conn)  # nothing seeded
    assert r["success"] is False
    assert r["already_refunded"] is False
    assert r["points_debited"] == 0
    assert r["reason"] == "Order not found"
