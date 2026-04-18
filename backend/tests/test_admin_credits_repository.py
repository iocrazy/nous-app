"""Unit tests for AdminCreditsRepository (packages + pricing)."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.credits_repository import AdminCreditsRepository


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._count: int | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self
        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data
            count = self._count
        return _R()


class _FakeClient:
    def __init__(self, query: _FakeQuery) -> None:
        self._query = query

    def table(self, name: str) -> _FakeQuery:
        self._query.calls.append(("table", (name,), {}))
        return self._query


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> AdminCreditsRepository:
    r = AdminCreditsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Packages ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_packages_orders_sort_order_asc(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "p1", "name": "Basic"}]
    rows = await repo.list_packages()
    assert rows == [{"id": "p1", "name": "Basic"}]

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("sort_order",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_list_packages_empty(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    assert await repo.list_packages() == []


@pytest.mark.asyncio
async def test_create_package_returns_first_row(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "p1", "name": "Basic"}]
    payload = {"name": "Basic", "points_amount": 1000, "price_cents": 999}
    created = await repo.create_package(payload)
    assert created == {"id": "p1", "name": "Basic"}

    insert = next(c for c in fake_query.calls if c[0] == "insert")
    assert insert[1] == (payload,)


@pytest.mark.asyncio
async def test_create_package_none_on_empty(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.create_package({"name": "x"}) is None


@pytest.mark.asyncio
async def test_update_package_returns_row(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "p1", "name": "Renamed"}]
    updated = await repo.update_package("p1", {"name": "Renamed"})
    assert updated == {"id": "p1", "name": "Renamed"}

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "p1") in eq_values


@pytest.mark.asyncio
async def test_update_package_none_on_empty(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.update_package("p1", {"name": "x"}) is None


@pytest.mark.asyncio
async def test_delete_package_filters_by_id(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    await repo.delete_package("p1")

    assert any(c[0] == "delete" for c in fake_query.calls)
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "p1") in eq_values


# ─── Pricing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_pricing_orders_by_action_type_asc(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"action_type": "transcribe", "points_cost": 10}]
    rows = await repo.list_pricing()
    assert rows == [{"action_type": "transcribe", "points_cost": 10}]

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("action_type",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_update_pricing_filters_on_action_type(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"action_type": "transcribe", "points_cost": 20}]
    updated = await repo.update_pricing(
        "transcribe", {"points_cost": 20, "description": "new"}
    )
    assert updated is not None
    assert updated["points_cost"] == 20

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("action_type", "transcribe") in eq_values


@pytest.mark.asyncio
async def test_update_pricing_none_on_empty(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert (
        await repo.update_pricing("transcribe", {"points_cost": 5})
    ) is None


# ─── Transactions ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_transactions_applies_team_and_type_filters(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_transactions(
        team_id="t1",
        type="consume",
        sort_by="created_at",
        sort_desc=True,
        offset=0,
        limit=10,
    )

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("team_id", "t1") in eq_values
    assert ("type", "consume") in eq_values


@pytest.mark.asyncio
async def test_list_transactions_skips_filters_when_none(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_transactions(
        team_id=None,
        type=None,
        sort_by="amount",
        sort_desc=False,
        offset=0,
        limit=50,
    )

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert eq_calls == []

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("amount",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_list_transactions_pagination_range(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_transactions(
        team_id=None,
        type=None,
        sort_by="created_at",
        sort_desc=True,
        offset=20,
        limit=10,
    )

    rng = next(c for c in fake_query.calls if c[0] == "range")
    assert rng[1] == (20, 29)


# ─── Orders ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_threads_all_filters(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_orders(
        payment_status="paid",
        payment_method="alipay",
        team_id="t1",
        sort_by="amount_cents",
        sort_desc=True,
        offset=0,
        limit=10,
    )

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("payment_status", "paid") in eq_values
    assert ("payment_method", "alipay") in eq_values
    assert ("team_id", "t1") in eq_values


# ─── Enrichment helpers ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_teams_by_ids_empty_short_circuits(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    assert await repo.get_teams_by_ids([]) == []


@pytest.mark.asyncio
async def test_get_teams_by_ids_uses_in_filter(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "t1", "name": "A"}]
    rows = await repo.get_teams_by_ids(["t1", "t2"])
    assert len(rows) == 1

    in_call = next(c for c in fake_query.calls if c[0] == "in_")
    assert in_call[1] == ("id", ["t1", "t2"])


@pytest.mark.asyncio
async def test_get_package_names_empty_short_circuits(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    assert await repo.get_package_names([]) == {}


@pytest.mark.asyncio
async def test_get_package_names_returns_id_to_name_map(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"id": "p1", "name": "Basic"},
        {"id": "p2", "name": "Pro"},
    ]
    names = await repo.get_package_names(["p1", "p2"])
    assert names == {"p1": "Basic", "p2": "Pro"}


# ─── Stats aggregates ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_quotas_balances_returns_rows(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"points_balance": 100}, {"points_balance": 200}]
    rows = await repo.all_quotas_balances()
    assert rows == [{"points_balance": 100}, {"points_balance": 200}]


@pytest.mark.asyncio
async def test_transactions_by_type_filters_on_type(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"amount": -5}]
    await repo.transactions_by_type("consume")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("type", "consume") in eq_values


@pytest.mark.asyncio
async def test_orders_by_status_without_since(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"amount_cents": 999}]
    await repo.orders_by_status(payment_status="paid")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("payment_status", "paid") in eq_values
    assert not any(c[0] == "gte" for c in fake_query.calls)


@pytest.mark.asyncio
async def test_orders_by_status_with_since_applies_gte(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.orders_by_status(
        payment_status="paid", since_iso="2026-04-01"
    )

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("paid_at", "2026-04-01")


@pytest.mark.asyncio
async def test_teams_count_returns_count(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 42
    assert await repo.teams_count() == 42


@pytest.mark.asyncio
async def test_orders_count_by_status_filters(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 5
    count = await repo.orders_count_by_status("pending")
    assert count == 5

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("payment_status", "pending") in eq_values


@pytest.mark.asyncio
async def test_revenue_chart_rows_filters_paid_since(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"paid_at": "2026-04-17", "amount_cents": 100}]
    await repo.revenue_chart_rows("2026-04-01")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("payment_status", "paid") in eq_values

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("paid_at", "2026-04-01")

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[2] == {"desc": False}


# ─── Order detail / update ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_order_returns_row(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "o1", "payment_status": "pending"}
    row = await repo.get_order("o1")
    assert row is not None
    assert row["payment_status"] == "pending"

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "o1") in eq_values


@pytest.mark.asyncio
async def test_update_order_filters_by_id(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    await repo.update_order("o1", {"payment_status": "paid"})

    upd = next(c for c in fake_query.calls if c[0] == "update")
    assert upd[1] == ({"payment_status": "paid"},)
    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("id", "o1") in eq_values


# ─── Team detail queries ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_returns_minimal_cols(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "t1", "name": "A", "is_personal": False}
    team = await repo.get_team("t1")
    assert team is not None
    assert team["name"] == "A"


@pytest.mark.asyncio
async def test_get_team_quota_empty_dict_on_missing(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    assert await repo.get_team_quota("t1") == {}


@pytest.mark.asyncio
async def test_recent_transactions_orders_desc_with_limit(
    repo: AdminCreditsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.recent_transactions("t1", limit=5)

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[2] == {"desc": True}
    lim = next(c for c in fake_query.calls if c[0] == "limit")
    assert lim[1] == (5,)
