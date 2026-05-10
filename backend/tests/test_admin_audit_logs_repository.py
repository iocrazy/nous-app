"""Unit tests for AuditLogsRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.audit_logs_repository import AuditLogsRepository


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
def repo(fake_query: _FakeQuery) -> AuditLogsRepository:
    r = AuditLogsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_list_threads_all_filters(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "a1"}]
    fake_query._count = 1
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 17, tzinfo=timezone.utc)

    rows, total = await repo.list(
        page=2,
        page_size=25,
        admin_id="admin-1",
        action="ban_user",
        target_type="user",
        start_date=start,
        end_date=end,
    )
    assert rows == [{"id": "a1"}]
    assert total == 1

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("admin_id", "admin-1") in eq_values
    assert ("action", "ban_user") in eq_values
    assert ("target_type", "user") in eq_values

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    lte = next(c for c in fake_query.calls if c[0] == "lte")
    assert gte[1] == ("created_at", start.isoformat())
    assert lte[1] == ("created_at", end.isoformat())


@pytest.mark.asyncio
async def test_list_pagination_math(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list(page=3, page_size=50)

    range_call = next(c for c in fake_query.calls if c[0] == "range")
    # page=3, page_size=50 → range(100, 149)
    assert range_call[1] == (100, 149)


@pytest.mark.asyncio
async def test_list_orders_newest_first(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list(page=1, page_size=10)

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)
    assert order[2] == {"desc": True}


@pytest.mark.asyncio
async def test_list_total_fallback_when_count_is_none(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "a1"}, {"id": "a2"}]
    fake_query._count = None
    rows, total = await repo.list(page=1, page_size=20)
    assert total == 2


@pytest.mark.asyncio
async def test_list_distinct_actions_empty_input(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.list_distinct_actions() == []


@pytest.mark.asyncio
async def test_list_distinct_actions_sorts_and_dedups(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"action": "ban_user"},
        {"action": "update_user"},
        {"action": "ban_user"},
        {"action": None},
    ]
    result = await repo.list_distinct_actions()
    assert result == ["ban_user", "update_user"]


@pytest.mark.asyncio
async def test_list_since_uses_gte(
    repo: AuditLogsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "a1"}]
    since = datetime(2026, 4, 10, tzinfo=timezone.utc)
    rows = await repo.list_since(since)
    assert rows == [{"id": "a1"}]

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("created_at", since.isoformat())
