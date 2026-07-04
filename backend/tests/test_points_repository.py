"""Unit tests for PointsRepository (ORM 2.0, model-backed — ★ MONEY ★).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` and writes through ``write_scope()`` with
``select`` / ``insert`` / ``update`` statements, and row objects are converted to
SELECT *-shaped dicts by the ``_parity`` value-type sweep. These tests mock
``read_scope`` / ``write_scope`` with a fake session that captures every emitted
``(sql, binds)`` pair and returns in-memory model instances, so the compiled SQL
shape + bind params AND the money-critical value-type parity (Integer/BigInteger
→ native int; the ONLY Numeric ``duration_seconds`` → str; uuid → str;
timestamptz → ISO str; the reference_id int→str write-boundary coercion; the
days=N tz-aware datetime bind) are asserted WITHOUT a live database (the
DSN-gated integration suite in
``tests/integration/test_points_repository_orm.py`` exercises the real
round-trip, and ``tests/test_points_service.py`` pins the atomic consume/refund
RPC contract at the service layer). This keeps fast, always-run coverage of the
collapsed ORM bodies.

NOTE: team_quotas.team_id / point_transactions.team_id are BIGINT columns, so the
repo ``int()``-coerces their str filter values (asyncpg int8-strict); the tests
use numeric-string ids for those. pricing / package / member ids are uuid columns
(str binds directly), so those use uuid strings.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import Any

import pytest

import app.repositories.points_repository as mod
from app.models import (
    MemberQuotas,
    PointPackages,
    PointPricing,
    PointTransactions,
    TeamQuotas,
)
from app.repositories.points_repository import PointsRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports ``.scalars().all()`` / ``.scalars().first()`` (model reads +
    RETURNING) and ``.all()`` (the get_admin_overview / get_usage_stats 2-tuple
    ``(amount, type)`` rows)."""

    def __init__(self, scalar_rows: list[Any], all_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._all_rows = all_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def all(self) -> list[Any]:
        return self._all_rows


class _FakeSession:
    """Captures execute (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.all_rows: list[Any] = []

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.all_rows)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> PointsRepository:
    return PointsRepository()


# ─── Pricing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_pricing_filters_active_and_native_int(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    pid = _uuid.uuid4()
    fake_session.scalar_rows = [
        PointPricing(id=pid, action_type="ai_transcription", points_cost=10)
    ]
    row = await repo.get_pricing("ai_transcription")
    assert row is not None
    assert row["id"] == str(pid)  # uuid → str sweep
    assert type(row["points_cost"]) is int and row["points_cost"] == 10

    sql, binds = fake_session.calls[-1]
    assert "point_pricing" in sql
    assert "is_active" in sql
    assert "ai_transcription" in binds.values()


@pytest.mark.asyncio
async def test_get_pricing_none_when_missing(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_pricing("nope") is None


@pytest.mark.asyncio
async def test_get_all_pricing_empty(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_all_pricing() == []


# ─── Packages ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_packages_orders_sort_order_asc(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    pkg_id = _uuid.uuid4()
    fake_session.scalar_rows = [
        PointPackages(id=pkg_id, name="Basic", points_amount=1000, sort_order=0)
    ]
    rows = await repo.get_active_packages()
    assert rows[0]["id"] == str(pkg_id)  # uuid → str
    assert type(rows[0]["points_amount"]) is int and rows[0]["points_amount"] == 1000

    sql, _ = fake_session.calls[-1]
    assert "point_packages" in sql
    assert "ORDER BY" in sql and "sort_order" in sql


@pytest.mark.asyncio
async def test_get_package_by_id_binds_uuid_str(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    pkg_id = _uuid.uuid4()
    fake_session.scalar_rows = [PointPackages(id=pkg_id, name="Pro")]
    pkg = await repo.get_package_by_id(str(pkg_id))
    assert pkg is not None and pkg["id"] == str(pkg_id)

    sql, binds = fake_session.calls[-1]
    assert "point_packages" in sql
    # uuid col — the str package_id binds directly (no int coercion).
    assert str(pkg_id) in binds.values()


# ─── Team Quotas ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_quota_coerces_team_id_to_int(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        TeamQuotas(team_id=5, points_balance=100, storage_limit_bytes=5368709120)
    ]
    quota = await repo.get_team_quota("5")
    assert quota is not None
    # BigInteger / Integer money columns → native int (the 5.3 trap).
    assert type(quota["team_id"]) is int and quota["team_id"] == 5
    assert type(quota["points_balance"]) is int and quota["points_balance"] == 100
    assert type(quota["storage_limit_bytes"]) is int

    sql, binds = fake_session.calls[-1]
    assert "team_quotas" in sql
    assert 5 in binds.values()  # team_id str "5" → int bind (bigint)


@pytest.mark.asyncio
async def test_create_team_quota_inserts_native_int(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        TeamQuotas(team_id=7, points_balance=500, storage_limit_bytes=5368709120)
    ]
    out = await repo.create_team_quota(team_id="7", points_balance=500)
    assert type(out["points_balance"]) is int and out["points_balance"] == 500

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.team_quotas" in sql
    values = set(binds.values())
    assert 7 in values and 500 in values  # team_id coerced to int, balance native


@pytest.mark.asyncio
async def test_update_points_balance_updates_native_int(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [TeamQuotas(team_id=5, points_balance=1250)]
    out = await repo.update_points_balance("5", 1250)
    assert type(out["points_balance"]) is int and out["points_balance"] == 1250

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.team_quotas" in sql
    values = set(binds.values())
    assert 1250 in values and 5 in values


@pytest.mark.asyncio
async def test_update_storage_used_updates_bigint(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    big = 9_000_000_000_000  # > 2^32, a real byte count
    fake_session.scalar_rows = [TeamQuotas(team_id=5, storage_used_bytes=big)]
    out = await repo.update_storage_used("5", big)
    assert type(out["storage_used_bytes"]) is int and out["storage_used_bytes"] == big

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.team_quotas" in sql
    assert big in binds.values()


# ─── Member Quotas ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_member_quota_uuid_user_id_to_str(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    mid = _uuid.uuid4()
    uid = _uuid.uuid4()
    fake_session.scalar_rows = [
        MemberQuotas(id=mid, team_id=5, user_id=uid, points_used_this_month=15)
    ]
    q = await repo.get_member_quota("5", str(uid))
    assert q is not None
    assert type(q["id"]) is str and q["id"] == str(mid)  # uuid → str
    assert type(q["user_id"]) is str and q["user_id"] == str(uid)
    assert type(q["points_used_this_month"]) is int

    sql, binds = fake_session.calls[-1]
    assert "member_quotas" in sql
    assert 5 in binds.values()  # team_id → int
    assert str(uid) in binds.values()  # user_id str binds against Uuid col


@pytest.mark.asyncio
async def test_upsert_member_quota_on_conflict(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    mid = _uuid.uuid4()
    uid = _uuid.uuid4()
    fake_session.scalar_rows = [
        MemberQuotas(id=mid, team_id=5, user_id=uid, monthly_points_limit=1000)
    ]
    q = await repo.upsert_member_quota("5", str(uid), 1000)
    assert q["monthly_points_limit"] == 1000
    assert type(q["user_id"]) is str

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.member_quotas" in sql
    assert "ON CONFLICT" in sql
    values = set(binds.values())
    assert 5 in values and 1000 in values


@pytest.mark.asyncio
async def test_increment_member_usage_read_then_write(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    uid = _uuid.uuid4()
    # First execute (read via get_member_quota) returns the current row; the
    # subsequent UPDATE returns nothing meaningful (same configured rows).
    fake_session.scalar_rows = [
        MemberQuotas(
            id=_uuid.uuid4(), team_id=5, user_id=uid, points_used_this_month=15
        )
    ]
    await repo.increment_member_usage("5", str(uid), 10)

    # Last call is the UPDATE writing back 15 + 10 = 25 (native int math).
    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.member_quotas" in sql
    assert 25 in binds.values()


# ─── Transactions ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_transaction_money_and_uuid_parity(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    uid = _uuid.uuid4()
    ts = _dt.datetime(2026, 7, 3, 12, 0, tzinfo=_dt.timezone.utc)
    fake_session.scalar_rows = [
        PointTransactions(
            id=123456789012345678,
            team_id=5,
            user_id=uid,
            amount=-30,
            balance_after=70,
            type="consume",
            duration_seconds=_decimal.Decimal("12.50"),
            created_at=ts,
        )
    ]
    txn = await repo.create_transaction(
        {
            "team_id": "5",
            "user_id": str(uid),
            "amount": -30,
            "balance_after": 70,
            "type": "consume",
            "reference_type": "ai_transcription",
            "reference_id": "res-1",
            "description": "test consume",
            "not_a_column": "dropped",  # phantom key filtered out
        }
    )
    # Integer money cols → native int; bigint id/team_id → native int (5.3 trap).
    assert type(txn["amount"]) is int and txn["amount"] == -30
    assert type(txn["balance_after"]) is int and txn["balance_after"] == 70
    assert type(txn["id"]) is int
    assert type(txn["team_id"]) is int
    # uuid user_id → str; the ONLY Numeric duration_seconds → str; ts → ISO str.
    assert type(txn["user_id"]) is str and txn["user_id"] == str(uid)
    assert type(txn["duration_seconds"]) is str and txn["duration_seconds"] == "12.50"
    assert type(txn["created_at"]) is str and "T" in txn["created_at"]

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.point_transactions" in sql
    values = set(binds.values())
    assert 5 in values  # team_id str "5" → int bind (bigint)
    assert "dropped" not in values  # phantom column never bound


@pytest.mark.asyncio
async def test_create_transaction_int_reference_id_coerced_to_str(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    """REGRESSION LOCK (Critical money-integrity fix): the payment-purchase path
    feeds reference_id a native BIGINT int (order_id). reference_id is
    String(200); asyncpg's text codec is STRICT and rejects a native int, so the
    repo MUST coerce reference_id → str at the write boundary (reproducing REST's
    stored "123456" shape). The bind must be the str form, never the raw int."""
    uid = _uuid.uuid4()
    order_id = 7654321012345678  # a bigint snowflake order id, native int
    fake_session.scalar_rows = [
        PointTransactions(
            id=1,
            team_id=5,
            user_id=uid,
            amount=500,
            type="purchase",
            reference_id=str(order_id),
        )
    ]
    txn = await repo.create_transaction(
        {
            "team_id": "5",
            "user_id": str(uid),
            "amount": 500,
            "balance_after": 1000,
            "type": "purchase",
            "reference_type": "purchase",
            "reference_id": order_id,  # ← native int, the real callback shape
            "description": f"Purchased 500 points (order {order_id})",
        }
    )
    assert type(txn["reference_id"]) is str and txn["reference_id"] == str(order_id)

    _sql, binds = fake_session.calls[-1]
    values = set(binds.values())
    assert str(order_id) in values  # coerced str bound
    assert order_id not in values  # the raw int is NEVER bound (would raise)


@pytest.mark.asyncio
async def test_get_transactions_days_filter_binds_tz_aware_datetime(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    """days=N must bind a tz-aware ``datetime`` (NOT an ISO string) for the
    ``created_at >= cutoff`` timestamptz comparison — the v3 temporal rule."""
    fake_session.scalar_rows = []
    await repo.get_transactions(
        team_id="5", type_filter="consume", search="foo", days=7
    )

    sql, binds = fake_session.calls[-1]
    assert "point_transactions" in sql
    values = list(binds.values())
    assert 5 in values  # team_id → int
    assert "consume" in values
    assert any("foo" in str(v) for v in values)  # ilike search bind
    assert any(isinstance(v, _dt.datetime) for v in values)  # tz-aware datetime bind
    # And it is timezone-aware (not a naive datetime / not a str).
    dt_binds = [v for v in values if isinstance(v, _dt.datetime)]
    assert dt_binds and dt_binds[0].tzinfo is not None


@pytest.mark.asyncio
async def test_get_usage_stats_native_int_aggregation(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    # get_usage_stats reads (amount, type) 2-tuples via ``.all()``.
    fake_session.all_rows = [(-10, "consume"), (-5, "consume"), (100, "purchase")]
    stats = await repo.get_usage_stats("5")
    assert stats["total_consumed"] == 15  # abs(-10) + abs(-5), native int math
    assert stats["total_purchased"] == 100
    assert stats["by_type"]["consume"] == -15
    assert stats["by_type"]["purchase"] == 100


@pytest.mark.asyncio
async def test_get_admin_overview_native_int_aggregation(
    repo: PointsRepository, fake_session: _FakeSession
) -> None:
    # First execute (balances) reads via ``.scalars().all()``; second (txns)
    # reads (amount, type) via ``.all()`` — the fake returns both on every call.
    fake_session.scalar_rows = [100, 250]
    fake_session.all_rows = [(-30, "consume"), (500, "purchase")]
    overview = await repo.get_admin_overview()
    assert overview["total_points_in_system"] == 350  # 100 + 250
    assert overview["active_teams_count"] == 2
    assert overview["total_consumed"] == 30
    assert overview["total_purchased"] == 500
    assert overview["total_transactions_count"] == 2


# ─── factory (ORM-only, post-rollout) ───────────────────────────────


def test_factory_returns_orm_repository() -> None:
    """Per-domain rollout flag ``USE_ORM_POINTS`` retired → factory
    unconditionally returns the ORM-backed PointsRepository."""
    from app.repositories.points_repository import PointsRepository as _Repo
    from app.repositories.points_repository import (
        get_points_repository,
    )

    assert type(get_points_repository()) is _Repo
