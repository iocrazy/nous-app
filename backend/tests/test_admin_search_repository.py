"""Unit tests for AdminSearchRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.search_repository import AdminSearchRepository


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
def repo(fake_query: _FakeQuery) -> AdminSearchRepository:
    r = AdminSearchRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_request_logs_uses_timestamp_range(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "1"}]
    rows = await repo.request_logs("2026-01-01", "2026-01-02")
    assert rows == [{"id": "1"}]

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    lte = next(c for c in fake_query.calls if c[0] == "lte")
    assert gte[1] == ("timestamp", "2026-01-01")
    assert lte[1] == ("timestamp", "2026-01-02")


@pytest.mark.asyncio
async def test_request_logs_orders_desc(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.request_logs("x", "y")

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("timestamp",)
    assert order[2] == {"desc": True}


@pytest.mark.asyncio
async def test_app_logs_uses_logged_at(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.app_logs("a", "b")

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1][0] == "logged_at"


@pytest.mark.asyncio
async def test_frontend_logs_uses_created_at(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.frontend_logs("a", "b")

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1][0] == "created_at"

    tables = [c for c in fake_query.calls if c[0] == "table"]
    assert tables[0][1] == ("frontend_error_logs",)


@pytest.mark.asyncio
async def test_audit_logs_uses_admin_audit_logs_table(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.audit_logs("a", "b")

    tables = [c for c in fake_query.calls if c[0] == "table"]
    assert tables[0][1] == ("admin_audit_logs",)


@pytest.mark.asyncio
async def test_get_request_log_returns_first_row(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"request_id": "r-1", "method": "GET"},
        {"request_id": "r-1", "method": "POST"},
    ]
    log = await repo.get_request_log("r-1")
    assert log is not None
    assert log["method"] == "GET"


@pytest.mark.asyncio
async def test_get_request_log_none_when_empty(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.get_request_log("r-1") is None


@pytest.mark.asyncio
async def test_app_logs_by_request_id_filters_on_jsonb_path(
    repo: AdminSearchRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"level": "INFO"}]
    await repo.app_logs_by_request_id("r-1")

    flt = next(c for c in fake_query.calls if c[0] == "filter")
    assert flt[1] == ("extra->>request_id", "eq", "r-1")

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[2] == {"desc": False}
