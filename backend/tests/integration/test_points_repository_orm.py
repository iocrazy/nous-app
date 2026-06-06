"""Integration tests for PointsRepositoryOrm (Phase 2 H batch — MONEY) vs real PG.

★ THE MONEY SURFACE — the points capacity ledger. ★ Proves the REST → ORM swap
is invisible AND that STRATEGY-C value-type parity holds on the points tables —
with dedicated tests that:

  • INTEGER money columns (points_balance / amount / balance_after /
    storage_*_bytes / points_amount) come back as NATIVE int so the consumers'
    exact int math (``balance < cost``, ``balance + amount``, ``min``, ``sum``)
    keeps working — REST returned a JSON NUMBER for these, str() would break it.
  • A balance debit/credit round-trips the EXACT value (no precision loss).
  • The ONLY Numeric column (point_transactions.duration_seconds) is str()'d to
    match REST's JSON-string shape.
  • uuid columns (member_quotas.user_id, point_transactions.user_id, pricing /
    package / member ids) are STR (default-str-all-uuid sweep).
  • get_transactions(days=N) date-filter boundary binds a tz-aware datetime.
  • Writes COMMIT (no silent rollback — money!).
  • factory on/off.

Setup: requires INTEGRATION_DATABASE_URL + a real teams row (team_quotas.team_id
FK teams.id) + a real auth.users row (member/txn user_id). Skips cleanly:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_points_repository_orm.py -v
"""

from __future__ import annotations

import datetime as _dt
import decimal
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
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
async def seed_team(integration_db_url):
    """Create a throwaway team (team_quotas/point_transactions.team_id FK
    teams.id) + a real user. Cascade-cleans on teardown. Yields (team_id:int,
    user_id:uuid)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if user_row is None:
            pytest.skip("need >=1 auth.users row for member/txn user_id")
        user_id = user_row["id"]
        team_row = await conn.fetchrow(
            "INSERT INTO teams (name, owner_id) VALUES ($1, $2) RETURNING id",
            f"__test_orm_points_{uuid.uuid4().hex[:8]}",
            user_id,
        )
        team_id = team_row["id"]
        yield int(team_id), user_id
    finally:
        try:
            await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
        finally:
            await conn.close()


def _repo():
    from app.repositories.points_repository_orm import PointsRepositoryOrm

    return PointsRepositoryOrm()


# ─── team_quota create + balance round-trip (COMMIT, exact value) ────────


async def test_create_team_quota_commit_and_native_int(
    integration_db_url, patched_engine, seed_team
):
    team_id, _user = seed_team
    quota = await _repo().create_team_quota(
        team_id=str(team_id), points_balance=500, storage_limit_bytes=5368709120
    )
    # INTEGER/BigInteger money columns stay NATIVE int (REST returned numbers).
    assert type(quota["points_balance"]) is int
    assert quota["points_balance"] == 500
    assert type(quota["storage_limit_bytes"]) is int
    assert quota["storage_limit_bytes"] == 5368709120
    assert type(quota["team_id"]) is int  # bigint PK native (5.3 trap)

    # COMMITTED (no silent rollback — money!).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT points_balance FROM team_quotas WHERE team_id = $1", team_id
        )
    finally:
        await conn.close()
    assert persisted == 500


async def test_balance_debit_credit_round_trips_exact(
    integration_db_url, patched_engine, seed_team
):
    """A balance update round-trips the EXACT integer value — no precision loss,
    no str-vs-int drift. This is the money-precision guarantee."""
    team_id, _user = seed_team
    await _repo().create_team_quota(team_id=str(team_id), points_balance=1000)

    # Credit: 1000 + 250 = 1250.
    after_credit = await _repo().update_points_balance(str(team_id), 1250)
    assert after_credit["points_balance"] == 1250
    assert type(after_credit["points_balance"]) is int

    # Debit: 1250 - 1250 = 0 (exact zero, no float fuzz).
    after_debit = await _repo().update_points_balance(str(team_id), 0)
    assert after_debit["points_balance"] == 0

    # Read back through get_team_quota — still native int 0.
    fresh = await _repo().get_team_quota(str(team_id))
    assert fresh["points_balance"] == 0
    assert type(fresh["points_balance"]) is int

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT points_balance FROM team_quotas WHERE team_id = $1", team_id
        )
    finally:
        await conn.close()
    assert persisted == 0


# ─── transactions: native int amounts + Numeric str + uuid str ──────────


async def test_create_transaction_money_and_uuid_parity(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    await _repo().create_team_quota(team_id=str(team_id), points_balance=100)

    txn = await _repo().create_transaction(
        {
            "team_id": str(team_id),
            "user_id": str(user_id),
            "amount": -30,
            "balance_after": 70,
            "type": "consume",
            "reference_type": "ai_transcription",
            "reference_id": "res-1",
            "description": "test consume",
        }
    )
    # amount / balance_after (Integer) → native int (exact arithmetic columns).
    assert type(txn["amount"]) is int and txn["amount"] == -30
    assert type(txn["balance_after"]) is int and txn["balance_after"] == 70
    # bigint id / team_id → native int (5.3 trap).
    assert type(txn["id"]) is int
    assert type(txn["team_id"]) is int and txn["team_id"] == team_id
    # user_id (uuid) → str (default-str-all-uuid sweep).
    assert type(txn["user_id"]) is str and txn["user_id"] == str(user_id)
    # duration_seconds (the ONLY Numeric col) is NULL here → passes through None.
    assert txn["duration_seconds"] is None
    # created_at (timestamptz) → ISO str.
    assert type(txn["created_at"]) is str and "T" in txn["created_at"]

    # COMMITTED.
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT amount FROM point_transactions WHERE id = $1", txn["id"]
        )
    finally:
        await conn.close()
    assert persisted == -30


async def test_create_transaction_int_reference_id_coerced_to_str(
    integration_db_url, patched_engine, seed_team
):
    """REGRESSION LOCK (Critical money-integrity fix): the payment-purchase path
    feeds reference_id a native BIGINT int (order_id) — payment_service.
    handle_callback → add_points(reference_id=order_id) → points_service →
    create_transaction. reference_id is String(200); asyncpg's text codec is
    STRICT and rejects a native int ("expected str, got int") → the INSERT would
    raise and (because the balance update already committed) DROP the ledger row
    = money-integrity break. The repo MUST coerce reference_id → str at the write
    boundary, reproducing REST's stored "123456" shape. This INSERT must succeed
    and the value must round-trip as the str form."""
    team_id, user_id = seed_team
    await _repo().create_team_quota(team_id=str(team_id), points_balance=500)

    order_id = 7654321012345678  # a bigint snowflake order id, as a native int
    txn = await _repo().create_transaction(
        {
            "team_id": str(team_id),
            "user_id": str(user_id),
            "amount": 500,
            "balance_after": 1000,
            "type": "purchase",
            "reference_type": "purchase",
            "reference_id": order_id,  # ← native int, the real callback shape
            "description": f"Purchased 500 points (order {order_id})",
        }
    )
    # Coerced to str (REST "123456" shape), NOT a native int, NOT a raise.
    assert type(txn["reference_id"]) is str
    assert txn["reference_id"] == str(order_id)

    # Round-trips from the DB as the str form (the ledger row exists = committed).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT reference_id FROM point_transactions WHERE id = $1", txn["id"]
        )
    finally:
        await conn.close()
    assert persisted == str(order_id)


async def test_numeric_duration_seconds_is_str(
    integration_db_url, patched_engine, seed_team
):
    """point_transactions.duration_seconds is the ONLY Numeric column → str()'d
    to match REST's JSON-string shape (Decimal == str silent-killer class)."""
    team_id, user_id = seed_team
    # Seed a transaction with a non-null Numeric duration_seconds directly.
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO point_transactions "
            "(team_id, user_id, amount, balance_after, type, duration_seconds) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            team_id,
            user_id,
            -5,
            95,
            "consume",
            decimal.Decimal("12.50"),
        )
    finally:
        await conn.close()

    txns = await _repo().get_transactions(str(team_id))
    assert len(txns) == 1
    ds = txns[0]["duration_seconds"]
    # str (REST shape), NOT a native Decimal / float.
    assert type(ds) is str
    assert decimal.Decimal(ds) == decimal.Decimal("12.50")


# ─── get_transactions date-filter boundary (v3 tz-aware bind) ────────────


async def test_get_transactions_days_filter_boundary(
    integration_db_url, patched_engine, seed_team
):
    """days=N binds a tz-aware datetime for ``created_at >= cutoff``. A row
    created 10 days ago is EXCLUDED by days=7 and INCLUDED by days=30."""
    team_id, user_id = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        old_ts = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=10)
        await conn.execute(
            "INSERT INTO point_transactions "
            "(team_id, user_id, amount, balance_after, type, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            team_id,
            user_id,
            -1,
            99,
            "consume",
            old_ts,
        )
    finally:
        await conn.close()

    within_7 = await _repo().get_transactions(str(team_id), days=7)
    assert len(within_7) == 0  # 10 days old, excluded by the 7-day cutoff
    within_30 = await _repo().get_transactions(str(team_id), days=30)
    assert len(within_30) == 1  # included by the 30-day cutoff


# ─── member_quota upsert (ON CONFLICT) + increment ──────────────────────


async def test_upsert_member_quota_and_uuid_str(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    q1 = await _repo().upsert_member_quota(str(team_id), str(user_id), 1000)
    assert q1["monthly_points_limit"] == 1000
    assert type(q1["user_id"]) is str and q1["user_id"] == str(user_id)
    assert type(q1["id"]) is str  # member_quotas.id is uuid → str

    # Upsert again (same PK) → updates, no duplicate.
    q2 = await _repo().upsert_member_quota(str(team_id), str(user_id), 2000)
    assert q2["monthly_points_limit"] == 2000

    await _repo().increment_member_usage(str(team_id), str(user_id), 15)
    fresh = await _repo().get_member_quota(str(team_id), str(user_id))
    assert fresh["points_used_this_month"] == 15
    assert type(fresh["points_used_this_month"]) is int


# ─── usage stats aggregation uses native int math ───────────────────────


async def test_get_usage_stats_native_int_math(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    for amount, ttype in ((-10, "consume"), (-5, "consume"), (100, "purchase")):
        await _repo().create_transaction(
            {
                "team_id": str(team_id),
                "user_id": str(user_id),
                "amount": amount,
                "balance_after": 0,
                "type": ttype,
            }
        )
    stats = await _repo().get_usage_stats(str(team_id))
    assert stats["total_consumed"] == 15  # abs(-10) + abs(-5)
    assert stats["total_purchased"] == 100
    assert stats["by_type"]["consume"] == -15
    assert stats["by_type"]["purchase"] == 100


# ─── pricing / packages reads ───────────────────────────────────────────


async def test_get_all_pricing_returns_list(patched_engine):
    rows = await _repo().get_all_pricing()
    assert isinstance(rows, list)
    for r in rows:
        # points_cost (Integer) → native int; id (uuid) → str.
        assert type(r["points_cost"]) is int
        assert type(r["id"]) is str


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.points_repository import (
        PointsRepository,
        get_points_repository,
    )

    with patch("app.core.config.settings.USE_ORM_POINTS", False):
        assert type(get_points_repository()) is PointsRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.points_repository_orm import PointsRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_POINTS", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.points_repository import get_points_repository

        assert type(get_points_repository()) is PointsRepositoryOrm
