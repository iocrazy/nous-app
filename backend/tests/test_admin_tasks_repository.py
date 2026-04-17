"""Unit tests for AdminTasksRepository.

The list() contract has the most moving parts (filters + sort + pagination),
so we exercise each knob and verify the builder call chain the repo emits.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.repositories.admin.tasks_repository import AdminTasksRepository


class _FakeQuery:
    """Builder-chain recorder. Every chained call becomes a (name, args)
    tuple in .calls; execute() returns an object with .data and .count."""

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
def repo(fake_query: _FakeQuery) -> AdminTasksRepository:
    r = AdminTasksRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_count_total(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 42
    assert await repo.count_total() == 42

    # Should only select id + count=exact, no filters
    select_calls = [c for c in fake_query.calls if c[0] == "select"]
    assert select_calls[0][1] == ("id",)
    assert select_calls[0][2] == {"count": "exact"}


@pytest.mark.asyncio
async def test_count_by_status(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 7
    count = await repo.count_by_status("pending")
    assert count == 7

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert eq_calls[0][1] == ("status", "pending")


@pytest.mark.asyncio
async def test_count_by_status_returns_zero_when_none(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = None
    assert await repo.count_by_status("running") == 0


@pytest.mark.asyncio
async def test_list_applies_filters_and_pagination(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "t1"}]
    fake_query._count = 1

    rows, total = await repo.list(
        page=3,
        page_size=20,
        status="processing",
        task_type="ai_transcription",
        search="foo",
        sort_by="started_at",
        sort_desc=False,
    )

    assert rows == [{"id": "t1"}]
    assert total == 1

    # Range must be (40, 59) for page=3, page_size=20
    range_call = next(c for c in fake_query.calls if c[0] == "range")
    assert range_call[1] == (40, 59)

    # Status filter
    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("status", "processing") in [c[1] for c in eq_calls]
    assert ("task_type", "ai_transcription") in [c[1] for c in eq_calls]

    # Search uses ilike on title
    ilike = next(c for c in fake_query.calls if c[0] == "ilike")
    assert ilike[1] == ("title", "%foo%")

    # Sort
    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("started_at",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_list_omits_filters_when_none(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list(page=1, page_size=20)

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    ilike_calls = [c for c in fake_query.calls if c[0] == "ilike"]
    assert eq_calls == []
    assert ilike_calls == []


@pytest.mark.asyncio
async def test_get_returns_row(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": "t1", "status": "pending"}
    row = await repo.get("t1")
    assert row == {"id": "t1", "status": "pending"}


@pytest.mark.asyncio
async def test_update_sends_changes(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.update("t1", {"status": "cancelled", "phase": "cancelled"})

    update_call = next(c for c in fake_query.calls if c[0] == "update")
    assert update_call[1] == ({"status": "cancelled", "phase": "cancelled"},)


@pytest.mark.asyncio
async def test_list_default_sort_by_created_at_desc(
    repo: AdminTasksRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list(page=1, page_size=20)

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)
    assert order[2] == {"desc": True}
