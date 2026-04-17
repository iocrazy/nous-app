"""Unit tests for AdminVideosRepository."""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.videos_repository import AdminVideosRepository


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
def repo(fake_query: _FakeQuery) -> AdminVideosRepository:
    r = AdminVideosRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Stats ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_total_selects_id_with_exact_count(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 250
    assert await repo.count_total() == 250

    select = next(c for c in fake_query.calls if c[0] == "select")
    assert select[1] == ("id",)
    assert select[2] == {"count": "exact"}


@pytest.mark.asyncio
async def test_count_by_status_uses_eq(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 12
    assert await repo.count_by_status("completed") == 12

    eq_call = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq_call[1] == ("video_download_status", "completed")


@pytest.mark.asyncio
async def test_counts_by_statuses_returns_mapping(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 5
    result = await repo.counts_by_statuses(["completed", "pending", "failed"])
    assert result == {"completed": 5, "pending": 5, "failed": 5}


@pytest.mark.asyncio
async def test_sum_storage_bytes_filters_positive(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"datasize_bytes": 100},
        {"datasize_bytes": 200},
        {"datasize_bytes": None},  # skipped by `or 0`
    ]
    total = await repo.sum_storage_bytes()
    assert total == 300

    gt_call = next(c for c in fake_query.calls if c[0] == "gt")
    assert gt_call[1] == ("datasize_bytes", 0)


# ─── List ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_with_filters_applies_search_or(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(
        page=1, page_size=20, search="cat"
    )

    or_call = next(c for c in fake_query.calls if c[0] == "or_")
    assert "title.ilike.%cat%" in or_call[1][0]
    assert "platform_id.ilike.%cat%" in or_call[1][0]


@pytest.mark.asyncio
async def test_list_with_filters_invalid_sort_falls_back(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(
        page=1, page_size=20, sort_by="DROP TABLE; --"
    )

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)


@pytest.mark.asyncio
async def test_list_with_filters_pagination_math(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(page=5, page_size=10)

    range_call = next(c for c in fake_query.calls if c[0] == "range")
    # page=5, page_size=10 → range(40, 49)
    assert range_call[1] == (40, 49)


@pytest.mark.asyncio
async def test_list_with_filters_threads_status_and_platform(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_with_filters(
        page=1,
        page_size=20,
        video_download_status="failed",
        source_platform="douyin",
    )

    eq_values = [c[1] for c in fake_query.calls if c[0] == "eq"]
    assert ("video_download_status", "failed") in eq_values
    assert ("source_platform", "douyin") in eq_values


# ─── Get / mutations ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_id_returns_row(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = {"id": 1, "title": "Hello"}
    row = await repo.get_by_id(1)
    assert row == {"id": 1, "title": "Hello"}


@pytest.mark.asyncio
async def test_get_by_id_swallows_exception(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    async def _raises():
        raise RuntimeError("db down")
    fake_query.execute = _raises  # type: ignore[assignment]

    row = await repo.get_by_id(1)
    assert row is None


@pytest.mark.asyncio
async def test_delete_returns_true_when_data_returned(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": 1}]
    assert await repo.delete(1) is True


@pytest.mark.asyncio
async def test_delete_returns_false_on_empty_result(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    assert await repo.delete(1) is False


@pytest.mark.asyncio
async def test_reset_for_retry_sends_correct_payload(
    repo: AdminVideosRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": 1}]
    await repo.reset_for_retry(1)

    update_call = next(c for c in fake_query.calls if c[0] == "update")
    assert update_call[1] == (
        {"video_download_status": "pending", "error_message": None},
    )
