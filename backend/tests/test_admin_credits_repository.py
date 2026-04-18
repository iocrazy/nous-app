"""Unit tests for AdminCreditsRepository (packages + pricing)."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.credits_repository import AdminCreditsRepository


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self
        return _capture

    async def execute(self) -> Any:
        class _R:
            data = self._data
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
