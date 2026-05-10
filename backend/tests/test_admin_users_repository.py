"""Unit tests for AdminUsersRepository.

We don't talk to a real Supabase here — we inject a fake client that
records the call chain. That's enough to pin the query shape (filters,
ordering, range pagination) without flakiness.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.repositories.admin.users_repository import AdminUsersRepository


class _FakeQuery:
    """Records every chained call and returns self so chains work.

    `execute()` returns an object with `.data` and `.count` attributes
    set by the test.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._data: Any = []
        self._count: int | None = None
        self._maybe_single = False
        self.execute = AsyncMock(side_effect=self._exec)

    def _record(self, name: str, *args: Any, **kwargs: Any) -> "_FakeQuery":
        self.calls.append((name, args, kwargs))
        return self

    def __getattr__(self, name: str):
        # Accept any builder call (select, eq, ilike, order, range, ...).
        # We route them all through _record and ignore in the return value.
        def _capture(*args: Any, **kwargs: Any) -> "_FakeQuery":
            return self._record(name, *args, **kwargs)

        return _capture

    def _result_class(self) -> Any:
        class _R:
            data = self._data
            count = self._count

        return _R()

    async def _exec(self) -> Any:
        return self._result_class()


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
def repo(fake_query: _FakeQuery) -> AdminUsersRepository:
    r = AdminUsersRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_list_with_filters_applies_search_and_role(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "u1"}]
    fake_query._count = 1

    rows, total = await repo.list_with_filters(
        page=2, page_size=10, search="alice", role="admin"
    )

    assert rows == [{"id": "u1"}]
    assert total == 1

    call_names = [c[0] for c in fake_query.calls]
    assert "table" in call_names
    assert "select" in call_names
    assert "ilike" in call_names
    assert "eq" in call_names
    assert "order" in call_names
    assert "range" in call_names

    # Verify pagination (page=2, page_size=10 → range(10, 19))
    range_args = next(args for name, args, _ in fake_query.calls if name == "range")
    assert range_args == (10, 19)

    # Verify search filter
    ilike_call = next(
        (args, kwargs) for name, args, kwargs in fake_query.calls if name == "ilike"
    )
    assert ilike_call[0] == ("username", "%alice%")


@pytest.mark.asyncio
async def test_list_returns_empty_on_no_rows(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    fake_query._count = 0
    rows, total = await repo.list_with_filters(page=1, page_size=20)
    assert rows == []
    assert total == 0


@pytest.mark.asyncio
async def test_get_by_id_returns_row(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "u1", "username": "alice"}

    user = await repo.get_by_id("u1")
    assert user == {"id": "u1", "username": "alice"}


@pytest.mark.asyncio
async def test_get_by_id_returns_none_on_missing(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = None
    user = await repo.get_by_id("nope")
    assert user is None


@pytest.mark.asyncio
async def test_get_by_id_returns_none_on_exception(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query.execute = AsyncMock(side_effect=RuntimeError("db down"))
    user = await repo.get_by_id("u1")
    assert user is None


@pytest.mark.asyncio
async def test_exists_true_and_false(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "u1"}
    assert await repo.exists("u1") is True

    fake_query._data = None
    assert await repo.exists("u1") is False


@pytest.mark.asyncio
async def test_update_returns_first_row(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "u1", "role": "admin"}]

    row = await repo.update("u1", {"role": "admin"})
    assert row == {"id": "u1", "role": "admin"}


@pytest.mark.asyncio
async def test_update_returns_none_on_empty_result(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    row = await repo.update("u1", {"role": "admin"})
    assert row is None


@pytest.mark.asyncio
async def test_set_banned_delegates_to_update(
    repo: AdminUsersRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "u1", "is_banned": True}]

    row = await repo.set_banned("u1", True)
    assert row == {"id": "u1", "is_banned": True}
