"""Integration test for the atomic order-confirm-and-credit RPC (mig 260, BUG 7).

``rpc_confirm_order_and_credit(p_order_id BIGINT)`` replaces the old two-await
``update_order(...'paid'...)`` + ``add_points(...)`` block in both
payment_service.handle_callback and admin/credits_router.confirm_order. It marks
the order paid AND credits team_quotas AND writes the ``(reference_type='order')``
ledger row in ONE transaction, and is idempotent on the order:

  * partial-unique index ``idx_point_transactions_unique_order`` on
    (reference_type, reference_id) WHERE reference_type='order' enforces
    at-most-one credit per order.
  * a duplicate call returns already_credited=true, points_added=0, no balance
    change.
  * a 'paid' order with NO ledger row (the old crash gap) is healed: credited
    exactly once.

This drives the *real* mig-260 function body against a real PG, seeding
teams -> orders (+ optional team_quotas) and asserting balance + ledger
invariants.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_confirm_order_credit_rpc.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

# Bigints well inside int8 range; unique to this test.
_TEAM_ID = 9_260_000_000_000_001
_ORDER_ID = 9_260_000_000_000_011
_POINTS = 100

_MIG_260 = (
    Path(__file__).resolve().parents[3]
    / "supabase"
    / "migrations"
    / "260_atomic_confirm_order_credit.sql"
)


async def _apply_mig_260(connection: asyncpg.Connection) -> None:
    """Apply the real mig-260 body (index + function) verbatim."""
    sql = _MIG_260.read_text(encoding="utf-8")
    sql = sql.replace("NOTIFY pgrst, 'reload schema';", "")
    await connection.execute(sql)


@pytest.fixture
async def conn():
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration test")
    connection = await asyncpg.connect(_TEST_DSN)
    try:
        await _cleanup(connection)
        await _apply_mig_260(connection)
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
        "BUG7 Test Team",
        creator_id,
        "bug7inv01",
    )
    return creator_id


async def _seed_order(
    connection: asyncpg.Connection,
    creator_id: str,
    *,
    payment_status: str = "pending",
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


async def _balance(connection: asyncpg.Connection) -> int | None:
    return await connection.fetchval(
        "SELECT points_balance FROM team_quotas WHERE team_id = $1", _TEAM_ID
    )


async def _ledger_count(connection: asyncpg.Connection) -> int:
    return await connection.fetchval(
        "SELECT count(*) FROM point_transactions "
        "WHERE reference_type = 'order' AND reference_id = $1",
        str(_ORDER_ID),
    )


async def _call(connection: asyncpg.Connection) -> dict:
    row = await connection.fetchrow(
        "SELECT * FROM rpc_confirm_order_and_credit($1)", _ORDER_ID
    )
    return dict(row)


# ---------------------------------------------------------------------------


async def test_happy_path_pending_credits_once(conn):
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="pending")

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_credited"] is False
    assert r["points_added"] == _POINTS
    assert r["new_balance"] == _POINTS
    assert r["reason"] is None

    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 1

    status = await conn.fetchval(
        "SELECT payment_status FROM orders WHERE id = $1", _ORDER_ID
    )
    assert status == "paid"
    paid_at = await conn.fetchval("SELECT paid_at FROM orders WHERE id = $1", _ORDER_ID)
    assert paid_at is not None


async def test_idempotent_double_call_credits_once(conn):
    """REQUIRED: calling twice credits +N once; 2nd call is a no-op."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="pending")

    r1 = await _call(conn)
    assert r1["success"] is True
    assert r1["already_credited"] is False
    assert r1["points_added"] == _POINTS

    r2 = await _call(conn)
    assert r2["success"] is True
    assert r2["already_credited"] is True
    assert r2["points_added"] == 0
    assert r2["new_balance"] == _POINTS

    # Balance bumped exactly once; exactly one ledger row.
    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 1


async def test_crash_gap_paid_no_ledger_heals(conn):
    """Order pre-set 'paid' with NO ledger row (the crash gap) -> credit once."""
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="paid")
    # No ledger row, no team_quotas row -> simulate the crash between the two
    # old awaits.
    assert await _ledger_count(conn) == 0

    r = await _call(conn)
    assert r["success"] is True
    assert r["already_credited"] is False
    assert r["points_added"] == _POINTS
    assert r["new_balance"] == _POINTS

    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 1

    # A second call is now idempotent.
    r2 = await _call(conn)
    assert r2["already_credited"] is True
    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 1


@pytest.mark.parametrize("bad_status", ["expired", "failed", "refunded"])
async def test_non_creditable_status_rejected(conn, bad_status):
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status=bad_status)

    r = await _call(conn)
    assert r["success"] is False
    assert r["already_credited"] is False
    assert r["points_added"] == 0
    assert r["reason"] is not None
    assert bad_status in r["reason"]

    # No balance / ledger change; status untouched.
    assert await _balance(conn) is None
    assert await _ledger_count(conn) == 0
    status = await conn.fetchval(
        "SELECT payment_status FROM orders WHERE id = $1", _ORDER_ID
    )
    assert status == bad_status


async def test_missing_team_quota_auto_created(conn):
    creator_id = await _seed_team(conn)
    await _seed_order(conn, creator_id, payment_status="pending")
    # No team_quotas row exists yet.
    assert await _balance(conn) is None

    r = await _call(conn)
    assert r["success"] is True
    assert r["points_added"] == _POINTS
    # Auto-created via ON CONFLICT DO NOTHING + credited.
    assert await _balance(conn) == _POINTS
    assert await _ledger_count(conn) == 1


async def test_order_not_found(conn):
    r = await _call(conn)  # nothing seeded
    assert r["success"] is False
    assert r["already_credited"] is False
    assert r["points_added"] == 0
    assert r["reason"] == "Order not found"
