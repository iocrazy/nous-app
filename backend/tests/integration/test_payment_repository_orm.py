"""Integration tests for PaymentRepositoryOrm (Phase 2 H batch — MONEY) vs PG.

★ THE MONEY SURFACE — payment orders (the purchase ledger). ★ Proves the
REST → ORM swap is invisible AND that STRATEGY-C value-type parity holds on the
orders table — with dedicated tests that:

  • amount_cents / points_amount (Integer money) come back as NATIVE int so
    points_service.add_points' ``balance + amount`` exact int math keeps working
    (REST returned numbers; str() would break it). orders has NO Numeric column.
  • An order amount round-trips the EXACT value (no precision loss).
  • id / team_id (BigInteger) → native int (5.3 trap).
  • user_id / package_id (uuid) → str (default-str-all-uuid sweep).
  • datetime WRITE-binding: a NATIVE aware datetime (create_order's expired_at /
    update_order's paid_at) binds cleanly; reads come back ISO str.
  • expire_pending_orders date-filter (expired_at < NOW()) works.
  • Writes COMMIT (a payment-state write silently rolled back = paid order stuck
    pending).
  • factory on/off.

Setup: requires INTEGRATION_DATABASE_URL + a real teams row (orders.team_id FK
teams.id) + a real auth.users row (orders.user_id). Skips cleanly:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_payment_repository_orm.py -v
"""

from __future__ import annotations

import datetime as _dt
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
    """Throwaway team + real user (orders.team_id FK teams.id, orders.user_id FK
    auth.users). Cascade-cleans on teardown. Yields (team_id:int, user_id:uuid)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if user_row is None:
            pytest.skip("need >=1 auth.users row for orders.user_id")
        user_id = user_row["id"]
        team_row = await conn.fetchrow(
            "INSERT INTO teams (name, owner_id) VALUES ($1, $2) RETURNING id",
            f"__test_orm_payment_{uuid.uuid4().hex[:8]}",
            user_id,
        )
        team_id = team_row["id"]
        yield int(team_id), user_id
    finally:
        try:
            await conn.execute("DELETE FROM orders WHERE team_id = $1", team_id)
            await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
        finally:
            await conn.close()


def _repo():
    from app.repositories.payment_repository_orm import PaymentRepositoryOrm

    return PaymentRepositoryOrm()


def _order_data(team_id: int, user_id) -> dict:
    return {
        "team_id": str(team_id),
        "user_id": str(user_id),
        "points_amount": 500,
        "amount_cents": 1990,
        "currency": "CNY",
        "payment_method": "wechat",
        "payment_status": "pending",
        "trade_no": f"MH{uuid.uuid4().hex[:12]}",
        "expired_at": _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=30),
    }


# ─── create_order: COMMIT + money native int + uuid str + ts native bind ─


async def test_create_order_commit_money_and_uuid_parity(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    order = await _repo().create_order(_order_data(team_id, user_id))

    # Integer money columns → native int (the add_points balance-math columns).
    assert type(order["amount_cents"]) is int and order["amount_cents"] == 1990
    assert type(order["points_amount"]) is int and order["points_amount"] == 500
    # bigint id / team_id → native int (5.3 trap).
    assert type(order["id"]) is int
    assert type(order["team_id"]) is int and order["team_id"] == team_id
    # uuid → str (default-str-all-uuid sweep).
    assert type(order["user_id"]) is str and order["user_id"] == str(user_id)
    # status (plain String, NOT Enum) → bare str; handle_callback does == on it.
    assert order["payment_status"] == "pending"
    # timestamptz → ISO str on read.
    assert type(order["expired_at"]) is str and "T" in order["expired_at"]
    assert type(order["created_at"]) is str

    # COMMITTED (no silent rollback — money!).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT amount_cents FROM orders WHERE id = $1", order["id"]
        )
    finally:
        await conn.close()
    assert persisted == 1990


async def test_order_amount_round_trips_exact(patched_engine, seed_team):
    """The money amount round-trips the EXACT integer cents — no precision loss
    or str-vs-int drift across create → read → poll."""
    team_id, user_id = seed_team
    data = _order_data(team_id, user_id)
    data["amount_cents"] = 9999
    data["points_amount"] = 12345
    created = await _repo().create_order(data)
    assert created["amount_cents"] == 9999
    assert created["points_amount"] == 12345

    fetched = await _repo().get_order_by_id(str(created["id"]))
    assert fetched["amount_cents"] == 9999
    assert type(fetched["amount_cents"]) is int
    by_trade = await _repo().get_order_by_trade_no(data["trade_no"])
    assert by_trade["points_amount"] == 12345


# ─── update_order: paid_at native datetime write + COMMIT ────────────────


async def test_update_order_paid_commit(integration_db_url, patched_engine, seed_team):
    team_id, user_id = seed_team
    created = await _repo().create_order(_order_data(team_id, user_id))
    paid_at = _dt.datetime.now(_dt.timezone.utc)
    updated = await _repo().update_order(
        str(created["id"]),
        {"payment_status": "paid", "paid_at": paid_at},
    )
    assert updated["payment_status"] == "paid"
    assert type(updated["paid_at"]) is str and "T" in updated["paid_at"]
    # updated_at advanced (func.now()).
    assert updated["updated_at"] is not None

    conn = await asyncpg.connect(integration_db_url)
    try:
        status = await conn.fetchval(
            "SELECT payment_status FROM orders WHERE id = $1", created["id"]
        )
    finally:
        await conn.close()
    assert status == "paid"


async def test_update_order_accepts_iso_string_paid_at(patched_engine, seed_team):
    """_coerce_temporal: an ISO-string paid_at (the legacy serialized shape) must
    bind to the timestamptz column without raising."""
    team_id, user_id = seed_team
    created = await _repo().create_order(_order_data(team_id, user_id))
    iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    updated = await _repo().update_order(
        str(created["id"]), {"payment_status": "paid", "paid_at": iso}
    )
    assert updated["payment_status"] == "paid"
    assert updated["paid_at"] is not None


# ─── get_team_orders + expire_pending_orders ─────────────────────────────


async def test_get_team_orders(patched_engine, seed_team):
    team_id, user_id = seed_team
    await _repo().create_order(_order_data(team_id, user_id))
    await _repo().create_order(_order_data(team_id, user_id))
    orders = await _repo().get_team_orders(str(team_id))
    assert len(orders) == 2
    assert all(o["team_id"] == team_id for o in orders)
    assert all(type(o["amount_cents"]) is int for o in orders)


async def test_expire_pending_orders(integration_db_url, patched_engine, seed_team):
    """An already-expired pending order (expired_at in the past) is flipped to
    'expired'; a future-expiry pending order is left alone."""
    team_id, user_id = seed_team
    # Past-expiry pending order seeded directly.
    conn = await asyncpg.connect(integration_db_url)
    try:
        past = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)
        await conn.execute(
            "INSERT INTO orders (team_id, user_id, points_amount, amount_cents, "
            "payment_method, payment_status, expired_at, trade_no) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            team_id,
            user_id,
            500,
            1990,
            "wechat",
            "pending",
            past,
            f"MH{uuid.uuid4().hex[:12]}",
        )
    finally:
        await conn.close()
    # A still-valid pending order.
    fresh = await _repo().create_order(_order_data(team_id, user_id))

    count = await _repo().expire_pending_orders()
    assert count >= 1

    conn = await asyncpg.connect(integration_db_url)
    try:
        fresh_status = await conn.fetchval(
            "SELECT payment_status FROM orders WHERE id = $1", fresh["id"]
        )
    finally:
        await conn.close()
    assert fresh_status == "pending"  # future expiry untouched


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.payment_repository import (
        PaymentRepository,
        get_payment_repository,
    )

    with patch("app.core.config.settings.USE_ORM_PAYMENT", False):
        assert type(get_payment_repository()) is PaymentRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.payment_repository_orm import PaymentRepositoryOrm

    with (
        patch("app.core.config.settings.USE_ORM_PAYMENT", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.payment_repository import get_payment_repository

        assert type(get_payment_repository()) is PaymentRepositoryOrm
