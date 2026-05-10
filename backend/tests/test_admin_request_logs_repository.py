"""Unit tests for the three log repositories in request_logs_repository.py."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.request_logs_repository import (
    AppLogsRepository,
    FrontendErrorLogsRepository,
    RequestLogsRepository,
)


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


def _make_repo(cls: type, fake_query: _FakeQuery):
    r = cls()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


# ─── RequestLogsRepository ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_logs_status_group_2xx_uses_range(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, status_group="2xx")

    gte_values = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lt_values = [c[1] for c in fake_query.calls if c[0] == "lt"]
    assert ("status_code", 200) in gte_values
    assert ("status_code", 300) in lt_values


@pytest.mark.asyncio
async def test_request_logs_status_group_4xx(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, status_group="4xx")

    gte_values = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lt_values = [c[1] for c in fake_query.calls if c[0] == "lt"]
    assert ("status_code", 400) in gte_values
    assert ("status_code", 500) in lt_values


@pytest.mark.asyncio
async def test_request_logs_status_group_5xx(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, status_group="5xx")

    gte_values = [c[1] for c in fake_query.calls if c[0] == "gte"]
    lt_values = [c[1] for c in fake_query.calls if c[0] == "lt"]
    assert ("status_code", 500) in gte_values
    assert ("status_code", 600) in lt_values


@pytest.mark.asyncio
async def test_request_logs_ignores_unknown_status_group(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, status_group="9xx")

    # No status_code gte/lt filters when status_group is invalid
    assert not any(c[0] == "gte" and c[1][0] == "status_code" for c in fake_query.calls)


@pytest.mark.asyncio
async def test_request_logs_method_is_uppercased(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, method="get")

    eq = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq[1] == ("method", "GET")


@pytest.mark.asyncio
async def test_request_logs_stats_since_selects_minimal_columns(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(RequestLogsRepository, fake_query)
    fake_query._data = []
    since = datetime(2026, 4, 1, tzinfo=timezone.utc)
    await repo.stats_since(since)

    select = next(c for c in fake_query.calls if c[0] == "select")
    # Should be a compact column list, not "*"
    assert "method" in select[1][0]
    assert "status_code" in select[1][0]
    assert "response_time_ms" in select[1][0]


# ─── FrontendErrorLogsRepository ────────────────────────────────────


@pytest.mark.asyncio
async def test_frontend_error_logs_filters_by_error_type(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(FrontendErrorLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, error_type="runtime")

    eq = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq[1] == ("error_type", "runtime")


@pytest.mark.asyncio
async def test_frontend_error_logs_orders_desc_by_created_at(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(FrontendErrorLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50)

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)
    assert order[2] == {"desc": True}


# ─── AppLogsRepository ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_logs_excludes_noise_when_no_module_filter(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(AppLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50)

    neq_values = [c[1] for c in fake_query.calls if c[0] == "neq"]
    # Every module in NOISE_MODULES should be in a neq filter
    for noise in AppLogsRepository.NOISE_MODULES:
        assert ("module", noise) in neq_values


@pytest.mark.asyncio
async def test_app_logs_skips_noise_filter_when_module_filter_set(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(AppLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, module="my.mod")

    # User explicitly asked for a module; don't double-filter noise
    assert not any(c[0] == "neq" for c in fake_query.calls)


@pytest.mark.asyncio
async def test_app_logs_level_is_uppercased(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(AppLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, level="error")

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("level", "ERROR") in eq_values


@pytest.mark.asyncio
async def test_app_logs_has_exception_true_uses_neq_null(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(AppLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, has_exception=True)

    neq_values = [c[1] for c in fake_query.calls if c[0] == "neq"]
    assert ("exception", None) in neq_values


@pytest.mark.asyncio
async def test_app_logs_has_exception_false_uses_is_null(
    fake_query: _FakeQuery,
) -> None:
    repo = _make_repo(AppLogsRepository, fake_query)
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=1, page_size=50, has_exception=False)

    is_values = [c[1] for c in fake_query.calls if c[0] == "is_"]
    assert ("exception", "null") in is_values
