"""Unit tests for NousRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.nous_repository import NousRepository


class _FakeQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._raises: Exception | None = None

    def __getattr__(self, name: str):
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            self.calls.append((name, args, kwargs))
            return self
        return _capture

    async def execute(self) -> Any:
        if self._raises is not None:
            raise self._raises

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
def repo(fake_query: _FakeQuery) -> NousRepository:
    r = NousRepository()

    async def _get_client():
        return _FakeClient(fake_query)

    r._get_client = _get_client  # type: ignore[method-assign]
    return r


# ─── list_enabled ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_enabled_filters_is_enabled_true(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "m1", "name": "nous-base"}]
    rows = await repo.list_enabled()
    assert rows == [{"id": "m1", "name": "nous-base"}]

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("is_enabled", True) in eq_values


@pytest.mark.asyncio
async def test_list_enabled_with_category_applies_extra_eq(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_enabled("transcription")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("is_enabled", True) in eq_values
    assert ("category", "transcription") in eq_values


@pytest.mark.asyncio
async def test_list_enabled_returns_empty_on_error(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._raises = RuntimeError("boom")
    assert await repo.list_enabled() == []


# ─── get_by_name ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_name_returns_row(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "m1", "name": "nous-base", "api_key": "sk"}
    row = await repo.get_by_name("nous-base")
    assert row is not None
    assert row["api_key"] == "sk"


@pytest.mark.asyncio
async def test_get_by_name_none_on_error(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._raises = RuntimeError("boom")
    assert await repo.get_by_name("nous-base") is None


# ─── list_all ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_all_orders_by_sort_order(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "m1"}]
    await repo.list_all()

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("sort_order",)


@pytest.mark.asyncio
async def test_list_all_no_is_enabled_filter(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_all()

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("is_enabled", True) not in eq_values


# ─── CRUD ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_first_row(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "m1", "name": "nous-new"}]
    row = await repo.create({"name": "nous-new"})
    assert row == {"id": "m1", "name": "nous-new"}

    insert = next(c for c in fake_query.calls if c[0] == "insert")
    assert insert[1] == ({"name": "nous-new"},)


@pytest.mark.asyncio
async def test_create_none_on_empty(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.create({"name": "x"}) is None


@pytest.mark.asyncio
async def test_update_sets_updated_at_to_now(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "m1"}]
    await repo.update("m1", {"display_name": "Renamed"})

    upd = next(c for c in fake_query.calls if c[0] == "update")
    args = upd[1][0]
    assert args["display_name"] == "Renamed"
    assert args["updated_at"] == "now()"


@pytest.mark.asyncio
async def test_update_none_on_empty(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.update("m1", {"x": "y"}) is None


@pytest.mark.asyncio
async def test_delete_returns_true_on_success(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    assert await repo.delete("m1") is True


@pytest.mark.asyncio
async def test_delete_returns_false_on_exception(
    repo: NousRepository, fake_query: _FakeQuery
) -> None:
    fake_query._raises = RuntimeError("boom")
    assert await repo.delete("m1") is False
