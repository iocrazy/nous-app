"""Unit tests for MonitoringRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.monitoring_repository import MonitoringRepository


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
def repo(fake_query: _FakeQuery) -> MonitoringRepository:
    r = MonitoringRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_request_logs_between_applies_window(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"path": "/x"}]
    start = datetime(2026, 4, 16, tzinfo=timezone.utc)
    end = datetime(2026, 4, 17, tzinfo=timezone.utc)
    rows = await repo.request_logs_between(start, end)
    assert rows == [{"path": "/x"}]

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    lte = next(c for c in fake_query.calls if c[0] == "lte")
    assert gte[1] == ("timestamp", start.isoformat())
    assert lte[1] == ("timestamp", end.isoformat())


@pytest.mark.asyncio
async def test_request_logs_between_orders_ascending(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("timestamp",)
    assert order[2] == {"desc": False}


@pytest.mark.asyncio
async def test_request_logs_between_default_limit(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    limit = next(c for c in fake_query.calls if c[0] == "limit")
    assert limit[1] == (10000,)


@pytest.mark.asyncio
async def test_request_logs_between_custom_limit(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.request_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
        limit=500,
    )

    limit = next(c for c in fake_query.calls if c[0] == "limit")
    assert limit[1] == (500,)


@pytest.mark.asyncio
async def test_app_logs_between_orders_desc_and_default_limit(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.app_logs_between(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("logged_at",)
    assert order[2] == {"desc": True}

    limit = next(c for c in fake_query.calls if c[0] == "limit")
    assert limit[1] == (5000,)


@pytest.mark.asyncio
async def test_frontend_error_count_uses_count_exact(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 9
    count = await repo.frontend_error_count(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    assert count == 9

    select = next(c for c in fake_query.calls if c[0] == "select")
    assert select[2] == {"count": "exact"}


@pytest.mark.asyncio
async def test_frontend_error_count_returns_zero_when_count_is_none(
    repo: MonitoringRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = None
    count = await repo.frontend_error_count(
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 4, 2, tzinfo=timezone.utc),
    )
    assert count == 0
