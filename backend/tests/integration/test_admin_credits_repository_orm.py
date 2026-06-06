"""Integration tests for AdminCreditsRepositoryOrm (Phase 2 admin wave — ★ MONEY ★).

Admin credit-administration surface. Proves the REST → ORM swap is invisible AND
that STRATEGY-C value-type parity holds on the credits tables — with dedicated
tests that:

  • INTEGER money columns (points_balance / amount / balance_after / amount_cents
    / points_amount / points_cost / price_cents) come back NATIVE int so the
    router's exact int math (sum / abs / +=) keeps working — REST returned a JSON
    NUMBER for these, str() would break it.
  • A package + balance round-trip returns the EXACT value (no precision loss) and
    COMMITS (no silent rollback — money!).
  • point_transactions.duration_seconds (the ONLY Numeric) is str()'d (REST shape).
  • uuid columns (txn/order user_id, package_id, package/pricing ids, teams
    owner_id) are STR (default-str-all-uuid sweep; several are dict-keys in the
    router enrichment).
  • timestamptz (orders.paid_at / created_at) → ISO str (the router reparses
    paid_at via datetime.fromisoformat).
  • PR-E is_personal is DERIVED from teams.kind=='personal' and injected.
  • orders_by_status(since_iso) date-filter boundary binds a tz-aware datetime.
  • update_order coerces an ISO-str paid_at → datetime (write-side v3 mirror).
  • factory on/off.

Setup: requires INTEGRATION_DATABASE_URL + a real auth.users row (txn/order
user_id) + a teams row (FK target). Skips cleanly:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_admin_credits_repository_orm.py -v
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
    """Create a throwaway team (point_transactions/orders/team_quotas.team_id FK
    teams.id) + a real user. Cascade-cleans on teardown. Yields (team_id:int,
    user_id:uuid)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_row = await conn.fetchrow("SELECT id FROM auth.users LIMIT 1")
        if user_row is None:
            pytest.skip("need >=1 auth.users row for txn/order user_id")
        user_id = user_row["id"]
        team_row = await conn.fetchrow(
            "INSERT INTO teams (name, owner_id, kind) VALUES ($1, $2, $3) RETURNING id",
            f"__test_orm_admincredits_{uuid.uuid4().hex[:8]}",
            user_id,
            "collaborative",
        )
        team_id = team_row["id"]
        yield int(team_id), user_id
    finally:
        try:
            await conn.execute("DELETE FROM teams WHERE id = $1", team_id)
        finally:
            await conn.close()


@pytest.fixture
async def seed_package(integration_db_url):
    """A throwaway point_packages row (uuid id). Cleans on teardown. Yields the
    uuid id."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        pid = await conn.fetchval(
            "INSERT INTO point_packages "
            "(name, points_amount, price_cents) VALUES ($1, $2, $3) RETURNING id",
            f"__test_pkg_{uuid.uuid4().hex[:8]}",
            1000,
            990,
        )
        yield pid
    finally:
        await conn.execute("DELETE FROM point_packages WHERE id = $1", pid)
        await conn.close()


def _repo():
    from app.repositories.admin.credits_repository_orm import (
        AdminCreditsRepositoryOrm,
    )

    return AdminCreditsRepositoryOrm()


# ─── package CRUD: create COMMITS + native int money + uuid id str ───────


async def test_create_package_commit_native_int_uuid_str(
    integration_db_url, patched_engine
):
    created = await _repo().create_package(
        {
            "name": f"__test_pkg_{uuid.uuid4().hex[:8]}",
            "points_amount": 5000,
            "price_cents": 4900,
            "sort_order": 3,
            "is_active": True,
        }
    )
    assert created is not None
    pid = created["id"]
    try:
        # INTEGER money columns → native int (REST returned numbers).
        assert (
            type(created["points_amount"]) is int and created["points_amount"] == 5000
        )
        assert type(created["price_cents"]) is int and created["price_cents"] == 4900
        assert type(created["sort_order"]) is int and created["sort_order"] == 3
        # uuid id → str (default-str-all-uuid sweep).
        assert type(created["id"]) is str

        # COMMITTED (no silent rollback — money table).
        conn = await asyncpg.connect(integration_db_url)
        try:
            persisted = await conn.fetchval(
                "SELECT price_cents FROM point_packages WHERE id = $1::uuid", pid
            )
        finally:
            await conn.close()
        assert persisted == 4900
    finally:
        conn = await asyncpg.connect(integration_db_url)
        try:
            await conn.execute("DELETE FROM point_packages WHERE id = $1::uuid", pid)
        finally:
            await conn.close()


async def test_update_package_exact_value_round_trip(
    integration_db_url, patched_engine, seed_package
):
    """Price update round-trips the EXACT integer cents — no precision loss."""
    pid = str(seed_package)
    updated = await _repo().update_package(
        pid,
        {"name": "__test_pkg_renamed", "points_amount": 2000, "price_cents": 1990},
    )
    assert updated is not None
    assert type(updated["price_cents"]) is int and updated["price_cents"] == 1990
    assert updated["points_amount"] == 2000

    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT price_cents FROM point_packages WHERE id = $1::uuid", seed_package
        )
    finally:
        await conn.close()
    assert persisted == 1990


async def test_get_package_names_str_keyed(
    integration_db_url, patched_engine, seed_package
):
    """get_package_names keys the map on str(uuid id) — the same key the router
    looks up with str(package_id)."""
    pid = str(seed_package)
    names = await _repo().get_package_names([pid])
    assert pid in names  # str-keyed
    assert isinstance(names[pid], str)


# ─── transactions: native int amounts + uuid str + Numeric str ──────────


async def test_transactions_money_uuid_numeric_parity(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO point_transactions "
            "(team_id, user_id, amount, balance_after, type, duration_seconds) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            team_id,
            user_id,
            -30,
            70,
            "consume",
            decimal.Decimal("12.50"),
        )
    finally:
        await conn.close()

    rows, total = await _repo().list_transactions(
        team_id=str(team_id),
        type=None,
        sort_by="created_at",
        sort_desc=True,
        offset=0,
        limit=50,
    )
    assert total == 1 and len(rows) == 1
    r = rows[0]
    # amount / balance_after (Integer) → native int (exact arithmetic columns).
    assert type(r["amount"]) is int and r["amount"] == -30
    assert type(r["balance_after"]) is int and r["balance_after"] == 70
    # bigint id / team_id → native int (5.3 trap).
    assert type(r["id"]) is int
    assert type(r["team_id"]) is int and r["team_id"] == team_id
    # user_id (uuid) → str (default-str-all-uuid sweep; dict-key enrichment).
    assert type(r["user_id"]) is str and r["user_id"] == str(user_id)
    # duration_seconds (the ONLY Numeric) → str (REST JSON-string shape).
    assert type(r["duration_seconds"]) is str
    assert decimal.Decimal(r["duration_seconds"]) == decimal.Decimal("12.50")
    # created_at (timestamptz) → ISO str.
    assert type(r["created_at"]) is str and "T" in r["created_at"]


async def test_transactions_by_type_native_int(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        for amt in (-10, -5):
            await conn.execute(
                "INSERT INTO point_transactions "
                "(team_id, user_id, amount, balance_after, type) "
                "VALUES ($1, $2, $3, $4, $5)",
                team_id,
                user_id,
                amt,
                0,
                "consume",
            )
    finally:
        await conn.close()

    rows = await _repo().transactions_by_type("consume")
    mine = [r for r in rows if r["team_id"] == team_id]
    # Router does sum(abs(r.get("amount") or 0)) — requires native int.
    assert sum(abs(r["amount"]) for r in mine) == 15
    for r in mine:
        assert type(r["amount"]) is int


# ─── orders: native int cents + paid_at ISO str + date-filter boundary ───


async def test_orders_by_status_native_int_and_paid_at_iso(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    now = _dt.datetime.now(_dt.timezone.utc)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO orders "
            "(team_id, user_id, points_amount, amount_cents, payment_method, "
            "payment_status, paid_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            team_id,
            user_id,
            1000,
            4900,
            "wechat",
            "paid",
            now,
        )
    finally:
        await conn.close()

    rows = await _repo().orders_by_status(payment_status="paid")
    mine = [r for r in rows if r["team_id"] == team_id]
    assert len(mine) == 1
    o = mine[0]
    assert type(o["amount_cents"]) is int and o["amount_cents"] == 4900
    assert type(o["points_amount"]) is int and o["points_amount"] == 1000
    # paid_at → ISO str (router reparses via datetime.fromisoformat).
    assert type(o["paid_at"]) is str
    _dt.datetime.fromisoformat(o["paid_at"].replace("Z", "+00:00"))  # must parse
    # user_id (uuid) → str.
    assert type(o["user_id"]) is str and o["user_id"] == str(user_id)


async def test_orders_by_status_since_iso_boundary(
    integration_db_url, patched_engine, seed_team
):
    """since_iso binds a tz-aware datetime for paid_at >= cutoff. A 10-day-old
    paid order is EXCLUDED by a 7-day cutoff and INCLUDED by a 30-day cutoff."""
    team_id, user_id = seed_team
    old_ts = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=10)
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO orders "
            "(team_id, user_id, points_amount, amount_cents, payment_method, "
            "payment_status, paid_at) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            team_id,
            user_id,
            500,
            990,
            "alipay",
            "paid",
            old_ts,
        )
    finally:
        await conn.close()

    cut7 = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).isoformat()
    cut30 = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=30)).isoformat()
    within7 = await _repo().orders_by_status(payment_status="paid", since_iso=cut7)
    within30 = await _repo().orders_by_status(payment_status="paid", since_iso=cut30)
    assert not any(r["team_id"] == team_id for r in within7)
    assert any(r["team_id"] == team_id for r in within30)


async def test_update_order_iso_str_paid_at_coerced(
    integration_db_url, patched_engine, seed_team
):
    """update_order receives paid_at/updated_at as ISO STRINGS (router shape).
    The write-side v3 mirror coerces them to datetime for asyncpg's strict
    timestamptz codec — the UPDATE must succeed and COMMIT."""
    team_id, user_id = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        oid = await conn.fetchval(
            "INSERT INTO orders "
            "(team_id, user_id, points_amount, amount_cents, payment_method, "
            "payment_status) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            team_id,
            user_id,
            1000,
            4900,
            "wechat",
            "pending",
        )
    finally:
        await conn.close()

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    await _repo().update_order(
        str(oid),
        {"payment_status": "paid", "paid_at": now_iso, "updated_at": now_iso},
    )

    conn = await asyncpg.connect(integration_db_url)
    try:
        row = await conn.fetchrow(
            "SELECT payment_status, paid_at FROM orders WHERE id = $1", oid
        )
    finally:
        await conn.close()
    assert row["payment_status"] == "paid"
    assert row["paid_at"] is not None  # committed as a real timestamptz


async def test_get_order_native_int_id(integration_db_url, patched_engine, seed_team):
    team_id, user_id = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        oid = await conn.fetchval(
            "INSERT INTO orders "
            "(team_id, user_id, points_amount, amount_cents, payment_method, "
            "payment_status) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            team_id,
            user_id,
            1000,
            4900,
            "wechat",
            "pending",
        )
    finally:
        await conn.close()

    order = await _repo().get_order(str(oid))
    assert order is not None
    assert type(order["id"]) is int and order["id"] == oid
    assert order["payment_status"] == "pending"

    missing = await _repo().get_order(
        "0"
    )  # no such bigint id → None (maybe_single parity)
    assert missing is None


# ─── teams: bigint id native int + PR-E is_personal derivation ───────────


async def test_get_team_is_personal_derived(
    integration_db_url, patched_engine, seed_team
):
    team_id, _user = seed_team
    team = await _repo().get_team(str(team_id))
    assert team is not None
    # bigint id → native int.
    assert type(team["id"]) is int and team["id"] == team_id
    # PR-E quirk: is_personal DERIVED from kind=='personal' (kind='collaborative').
    assert team["is_personal"] is False
    assert team["kind"] == "collaborative"
    # owner_id (uuid) → str.
    assert type(team["owner_id"]) is str


async def test_get_teams_by_ids_is_personal_derived(
    integration_db_url, patched_engine, seed_team
):
    team_id, _user = seed_team
    teams = await _repo().get_teams_by_ids([str(team_id)])
    assert len(teams) == 1
    assert teams[0]["is_personal"] is False
    assert type(teams[0]["id"]) is int


# ─── team_quota + stats counts ───────────────────────────────────────────


async def test_get_team_quota_native_int(integration_db_url, patched_engine, seed_team):
    team_id, _user = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO team_quotas (team_id, points_balance) VALUES ($1, $2)",
            team_id,
            777,
        )
    finally:
        await conn.close()

    quota = await _repo().get_team_quota(str(team_id))
    assert type(quota["points_balance"]) is int and quota["points_balance"] == 777
    assert type(quota["storage_limit_bytes"]) is int  # BigInteger native int

    # Missing team → empty dict (legacy maybe_single None → {}).
    empty = await _repo().get_team_quota("0")
    assert empty == {}


async def test_all_quotas_balances_shape_native_int(
    integration_db_url, patched_engine, seed_team
):
    team_id, _user = seed_team
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO team_quotas (team_id, points_balance) VALUES ($1, $2)",
            team_id,
            123,
        )
    finally:
        await conn.close()

    rows = await _repo().all_quotas_balances()
    # Shape parity: list of {points_balance: int}; router sums them.
    assert all("points_balance" in r for r in rows)
    assert all(type(r["points_balance"]) is int for r in rows)
    assert sum(r["points_balance"] for r in rows) >= 123


async def test_teams_count_and_orders_count(
    integration_db_url, patched_engine, seed_team
):
    team_id, user_id = seed_team
    n_teams = await _repo().teams_count()
    assert type(n_teams) is int and n_teams >= 1

    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO orders "
            "(team_id, user_id, points_amount, amount_cents, payment_method, "
            "payment_status) VALUES ($1, $2, $3, $4, $5, $6)",
            team_id,
            user_id,
            1000,
            4900,
            "wechat",
            "pending",
        )
    finally:
        await conn.close()
    n_pending = await _repo().orders_count_by_status("pending")
    assert type(n_pending) is int and n_pending >= 1


# ─── factory on/off ─────────────────────────────────────────────────────


def test_factory_off_returns_legacy():
    from unittest.mock import patch

    from app.repositories.admin.credits_repository import (
        AdminCreditsRepository,
        get_admin_credits_repository,
    )

    with patch("app.core.config.settings.USE_ORM_ADMIN_CREDITS", False):
        assert type(get_admin_credits_repository()) is AdminCreditsRepository


def test_factory_on_returns_orm(integration_db_url):
    from unittest.mock import patch

    from app.repositories.admin.credits_repository_orm import (
        AdminCreditsRepositoryOrm,
    )

    with (
        patch("app.core.config.settings.USE_ORM_ADMIN_CREDITS", True),
        patch("app.db.engine.is_configured", return_value=True),
    ):
        from app.repositories.admin.credits_repository import (
            get_admin_credits_repository,
        )

        assert type(get_admin_credits_repository()) is AdminCreditsRepositoryOrm
