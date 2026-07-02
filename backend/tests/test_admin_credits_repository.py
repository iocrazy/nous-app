"""Unit tests for AdminCreditsRepository (ORM 2.0, model-backed — ★ MONEY ★).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` and writes through ``write_scope()`` with
``select`` / ``insert`` / ``update`` / ``delete`` statements, and row objects are
converted to SELECT *-shaped dicts by the ``_parity`` value-type sweep. These
tests mock ``read_scope`` / ``write_scope`` with a fake session that captures
every emitted ``(sql, binds)`` pair and returns in-memory model instances, so the
compiled SQL shape + bind params AND the money-critical value-type parity
(Integer/BigInteger → native int; Numeric → str; uuid → str; timestamptz → ISO
str; the PR-E ``is_personal`` derivation) are asserted WITHOUT a live database
(the DSN-gated integration suite in
``tests/integration/test_admin_credits_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.

NOTE: order / team / point_transactions ids are BIGINT columns, so the repo
``int()``-coerces their str filter values (asyncpg int8-strict); the tests use
numeric-string ids for those. package / pricing ids are uuid columns (str binds
directly), so those use uuid strings.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import Any

import pytest

import app.repositories.admin.credits_repository as mod
from app.models import (
    Orders,
    PointPackages,
    PointPricing,
    PointTransactions,
    Teams,
)
from app.repositories.admin.credits_repository import AdminCreditsRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports ``.scalars().all()`` / ``.scalars().first()`` (model reads +
    RETURNING) and ``.all()`` (the get_package_names 2-tuple rows)."""

    def __init__(self, scalar_rows: list[Any], all_rows: list[Any]) -> None:
        self._scalar_rows = scalar_rows
        self._all_rows = all_rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def all(self) -> list[Any]:
        return self._all_rows


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.all_rows: list[Any] = []
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.all_rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append((str(stmt), stmt.compile().params))
        return self.scalar_value


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
def repo() -> AdminCreditsRepository:
    return AdminCreditsRepository()


# ─── Packages ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_packages_orders_sort_order_asc(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    pid = _uuid.uuid4()
    fake_session.scalar_rows = [PointPackages(id=pid, name="Basic", sort_order=0)]
    rows = await repo.list_packages()
    # uuid id → str; native int money columns stay int.
    assert rows[0]["id"] == str(pid)
    assert rows[0]["name"] == "Basic"

    sql, _ = fake_session.calls[-1]
    assert "point_packages" in sql
    assert "ORDER BY" in sql and "sort_order" in sql


@pytest.mark.asyncio
async def test_list_packages_empty(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.list_packages() == []


@pytest.mark.asyncio
async def test_create_package_filters_to_mapped_attrs(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    pid = _uuid.uuid4()
    fake_session.scalar_rows = [
        PointPackages(id=pid, name="Basic", points_amount=1000, price_cents=999)
    ]
    payload = {
        "name": "Basic",
        "points_amount": 1000,
        "price_cents": 999,
        "not_a_column": "dropped",  # phantom key filtered out
    }
    created = await repo.create_package(payload)
    assert created is not None
    assert created["id"] == str(pid)
    assert created["points_amount"] == 1000  # native int

    sql, binds = fake_session.calls[-1]
    assert "INSERT INTO public.point_packages" in sql
    values = set(binds.values())
    assert "Basic" in values and 1000 in values and 999 in values
    assert "dropped" not in values  # phantom column never bound


@pytest.mark.asyncio
async def test_create_package_none_on_empty(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.create_package({"name": "x"}) is None


@pytest.mark.asyncio
async def test_update_package_returns_row(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    pid = _uuid.uuid4()
    fake_session.scalar_rows = [PointPackages(id=pid, name="Renamed")]
    updated = await repo.update_package(str(pid), {"name": "Renamed"})
    assert updated is not None
    assert updated["name"] == "Renamed"

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.point_packages" in sql
    # uuid col — the str package_id binds directly (no int coercion).
    assert str(pid) in binds.values()


@pytest.mark.asyncio
async def test_update_package_none_on_empty(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.update_package(str(_uuid.uuid4()), {"name": "x"}) is None


@pytest.mark.asyncio
async def test_delete_package_filters_by_id(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    pid = _uuid.uuid4()
    await repo.delete_package(str(pid))

    sql, binds = fake_session.calls[-1]
    assert "DELETE FROM public.point_packages" in sql
    assert str(pid) in binds.values()


# ─── Pricing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_pricing_orders_by_action_type_asc(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        PointPricing(id=_uuid.uuid4(), action_type="transcribe", points_cost=10)
    ]
    rows = await repo.list_pricing()
    assert rows[0]["action_type"] == "transcribe"
    assert rows[0]["points_cost"] == 10  # native int

    sql, _ = fake_session.calls[-1]
    assert "point_pricing" in sql
    assert "ORDER BY" in sql and "action_type" in sql


@pytest.mark.asyncio
async def test_update_pricing_filters_on_action_type(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        PointPricing(id=_uuid.uuid4(), action_type="transcribe", points_cost=20)
    ]
    updated = await repo.update_pricing(
        "transcribe", {"points_cost": 20, "description": "new"}
    )
    assert updated is not None
    assert updated["points_cost"] == 20

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.point_pricing" in sql
    assert "transcribe" in binds.values()


@pytest.mark.asyncio
async def test_update_pricing_none_on_empty(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert (await repo.update_pricing("transcribe", {"points_cost": 5})) is None


# ─── Transactions ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_transactions_applies_team_and_type_filters(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_transactions(
        team_id="5",
        type="consume",
        sort_by="created_at",
        sort_desc=True,
        offset=0,
        limit=10,
    )

    sql, binds = fake_session.calls[-1]  # the paginated rows SELECT
    assert "point_transactions" in sql
    values = set(binds.values())
    assert 5 in values  # team_id str "5" → int (bigint bind)
    assert "consume" in values


@pytest.mark.asyncio
async def test_list_transactions_skips_filters_when_none(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_transactions(
        team_id=None,
        type=None,
        sort_by="amount",
        sort_desc=False,
        offset=20,
        limit=10,
    )

    sql, binds = fake_session.calls[-1]
    # no WHERE clause when both filters are None
    assert "WHERE" not in sql
    assert "amount ASC" in sql  # sort_by=amount, sort_desc=False
    assert 20 in binds.values() and 10 in binds.values()  # OFFSET / LIMIT


@pytest.mark.asyncio
async def test_list_transactions_money_parity(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    uid = _uuid.uuid4()
    fake_session.scalar_rows = [
        PointTransactions(
            id=123456789012345678,  # bigint snowflake > 2^53
            team_id=42,
            amount=-5,
            balance_after=95,
            type="consume",
            user_id=uid,
            duration_seconds=_decimal.Decimal("1.50"),
            created_at=_dt.datetime(2026, 4, 1, tzinfo=_dt.timezone.utc),
        )
    ]
    fake_session.scalar_value = 1
    rows, total = await repo.list_transactions(
        team_id=None,
        type=None,
        sort_by="created_at",
        sort_desc=True,
        offset=0,
        limit=10,
    )
    assert total == 1
    r = rows[0]
    # ★ MONEY: Integer/BigInteger stay native int (5.3 trap + sum/abs consumers).
    assert r["id"] == 123456789012345678 and type(r["id"]) is int
    assert r["team_id"] == 42 and type(r["team_id"]) is int
    assert r["amount"] == -5 and type(r["amount"]) is int
    assert r["balance_after"] == 95
    # Numeric duration_seconds → str (REST JSON-string shape).
    assert r["duration_seconds"] == "1.50"
    # uuid user_id → str (dict-key trap for email enrichment).
    assert r["user_id"] == str(uid)
    # timestamptz → ISO str.
    assert r["created_at"] == "2026-04-01T00:00:00+00:00"


# ─── Orders ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_threads_all_filters(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_orders(
        payment_status="paid",
        payment_method="alipay",
        team_id="7",
        sort_by="amount_cents",
        sort_desc=True,
        offset=0,
        limit=10,
    )

    sql, binds = fake_session.calls[-1]
    assert "orders" in sql
    values = set(binds.values())
    assert "paid" in values and "alipay" in values
    assert 7 in values  # team_id str "7" → int (bigint bind)


@pytest.mark.asyncio
async def test_get_order_coerces_bigint_id(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        Orders(id=98765, payment_status="pending", user_id=_uuid.uuid4())
    ]
    row = await repo.get_order("98765")
    assert row is not None
    assert row["payment_status"] == "pending"
    assert row["id"] == 98765 and type(row["id"]) is int

    sql, binds = fake_session.calls[-1]
    assert "orders" in sql
    assert 98765 in binds.values()  # str order_id → int (bigint bind)


@pytest.mark.asyncio
async def test_update_order_coerces_iso_ts_and_bigint_id(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    iso = "2026-04-17T12:00:00+00:00"
    await repo.update_order("98765", {"payment_status": "paid", "paid_at": iso})

    sql, binds = fake_session.calls[-1]
    assert "UPDATE public.orders" in sql
    values = list(binds.values())
    assert "paid" in values
    assert 98765 in values  # str order_id → int (bigint bind)
    # WRITE-side v3 mirror: ISO-string paid_at coerced to a native datetime.
    coerced = [v for v in values if isinstance(v, _dt.datetime)]
    assert coerced and coerced[0] == _dt.datetime(
        2026, 4, 17, 12, 0, tzinfo=_dt.timezone.utc
    )
    assert iso not in values  # the raw ISO string is NOT bound


# ─── Enrichment helpers ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_teams_by_ids_empty_short_circuits(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    assert await repo.get_teams_by_ids([]) == []


@pytest.mark.asyncio
async def test_get_teams_by_ids_derives_is_personal(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    owner = _uuid.uuid4()
    fake_session.scalar_rows = [
        Teams(id=1, name="Solo", kind="personal", owner_id=owner),
        Teams(id=2, name="Org", kind="standard", owner_id=owner),
    ]
    rows = await repo.get_teams_by_ids(["1", "2"])
    assert len(rows) == 2
    # PR-E derivation: is_personal from kind == "personal".
    assert rows[0]["is_personal"] is True
    assert rows[1]["is_personal"] is False
    assert rows[0]["id"] == 1 and type(rows[0]["id"]) is int
    assert rows[0]["owner_id"] == str(owner)

    sql, binds = fake_session.calls[-1]
    # bigint team ids int-coerced for the IN filter (expanded IN binds a list).
    assert [1, 2] in binds.values()


@pytest.mark.asyncio
async def test_get_package_names_empty_short_circuits(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    assert await repo.get_package_names([]) == {}


@pytest.mark.asyncio
async def test_get_package_names_returns_str_keyed_map(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    p1, p2 = _uuid.uuid4(), _uuid.uuid4()
    fake_session.all_rows = [(p1, "Basic"), (p2, "Pro")]
    names = await repo.get_package_names([str(p1), str(p2)])
    # Keyed on str(id) — matches the router's str(package_id) lookup.
    assert names == {str(p1): "Basic", str(p2): "Pro"}


# ─── Stats aggregates ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_quotas_balances_returns_native_int_rows(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [100, 200]
    rows = await repo.all_quotas_balances()
    assert rows == [{"points_balance": 100}, {"points_balance": 200}]
    assert type(rows[0]["points_balance"]) is int


@pytest.mark.asyncio
async def test_transactions_by_type_filters_on_type(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        PointTransactions(id=1, team_id=1, amount=-5, balance_after=0, type="consume")
    ]
    await repo.transactions_by_type("consume")

    sql, binds = fake_session.calls[-1]
    assert "point_transactions" in sql
    assert "consume" in binds.values()


@pytest.mark.asyncio
async def test_orders_by_status_without_since(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        Orders(id=1, payment_status="paid", amount_cents=999, user_id=_uuid.uuid4())
    ]
    await repo.orders_by_status(payment_status="paid")

    sql, binds = fake_session.calls[-1]
    assert "paid" in binds.values()
    assert "paid_at >=" not in sql  # no since filter


@pytest.mark.asyncio
async def test_orders_by_status_with_since_binds_datetime(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    await repo.orders_by_status(payment_status="paid", since_iso="2026-04-01")

    sql, binds = fake_session.calls[-1]
    assert "paid_at >=" in sql
    # v3 rule: a tz-aware datetime is bound (NOT the ISO string).
    dts = [v for v in binds.values() if isinstance(v, _dt.datetime)]
    assert dts and dts[0].tzinfo is not None
    assert "2026-04-01" not in binds.values()


@pytest.mark.asyncio
async def test_teams_count_returns_count(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 42
    assert await repo.teams_count() == 42


@pytest.mark.asyncio
async def test_orders_count_by_status_filters(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 5
    count = await repo.orders_count_by_status("pending")
    assert count == 5

    sql, binds = fake_session.calls[-1]
    assert "orders" in sql
    assert "pending" in binds.values()


@pytest.mark.asyncio
async def test_get_team_returns_row_with_is_personal(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    owner = _uuid.uuid4()
    fake_session.scalar_rows = [Teams(id=5, name="A", kind="personal", owner_id=owner)]
    team = await repo.get_team("5")
    assert team is not None
    assert team["name"] == "A"
    assert team["is_personal"] is True
    assert team["id"] == 5 and type(team["id"]) is int

    sql, binds = fake_session.calls[-1]
    assert 5 in binds.values()  # str team_id → int


@pytest.mark.asyncio
async def test_get_team_quota_empty_dict_on_missing(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_team_quota("5") == {}


@pytest.mark.asyncio
async def test_recent_transactions_orders_desc_with_limit(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    await repo.recent_transactions("5", limit=5)

    sql, binds = fake_session.calls[-1]
    assert "created_at DESC" in sql
    assert 5 in binds.values()  # team_id int + limit


@pytest.mark.asyncio
async def test_revenue_chart_rows_filters_paid_since_datetime(
    repo: AdminCreditsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [
        Orders(
            id=1,
            payment_status="paid",
            amount_cents=100,
            points_amount=10,
            user_id=_uuid.uuid4(),
            paid_at=_dt.datetime(2026, 4, 17, tzinfo=_dt.timezone.utc),
        )
    ]
    rows = await repo.revenue_chart_rows("2026-04-01")
    assert rows[0]["amount_cents"] == 100  # native int
    assert rows[0]["paid_at"] == "2026-04-17T00:00:00+00:00"  # ISO str

    sql, binds = fake_session.calls[-1]
    assert "paid" in binds.values()
    assert "paid_at >=" in sql and "ORDER BY" in sql
    dts = [v for v in binds.values() if isinstance(v, _dt.datetime)]
    assert dts and dts[0].tzinfo is not None
