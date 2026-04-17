"""Unit tests for AdminTranscodeRepository.

Focuses on the routing and filter logic — the 'status_filter="null"'
branch vs named statuses, the min_size_mb conversion (MB→bytes),
the parallel status_counts fanout, and settings load/upsert shape.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.repositories.admin.transcode_repository import (
    AdminTranscodeRepository,
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


@pytest.fixture
def fake_query() -> _FakeQuery:
    return _FakeQuery()


@pytest.fixture
def repo(fake_query: _FakeQuery) -> AdminTranscodeRepository:
    r = AdminTranscodeRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


# ─── Stats ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_total_video_versions_filters_video_mime(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 100
    count = await repo.count_total_video_versions()
    assert count == 100

    like_call = next(c for c in fake_query.calls if c[0] == "like")
    assert like_call[1] == ("mime_type", "video/%")


@pytest.mark.asyncio
async def test_count_by_status_filters_both_mime_and_status(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 5
    count = await repo.count_by_status("completed")
    assert count == 5

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("transcode_status", "completed") in [c[1] for c in eq_calls]


@pytest.mark.asyncio
async def test_status_counts_returns_mapping(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 3  # every count returns 3 in the fake
    result = await repo.status_counts(["completed", "failed"])
    assert result == {"completed": 3, "failed": 3}


# ─── List ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_null_status_uses_is_null(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_video_versions(
        page=1, page_size=20, status_filter="null"
    )

    is_calls = [c for c in fake_query.calls if c[0] == "is_"]
    assert ("transcode_status", "null") in [c[1] for c in is_calls]


@pytest.mark.asyncio
async def test_list_named_status_uses_eq(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_video_versions(
        page=1, page_size=20, status_filter="completed"
    )

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("transcode_status", "completed") in [c[1] for c in eq_calls]


@pytest.mark.asyncio
async def test_list_min_size_converts_mb_to_bytes(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_video_versions(page=1, page_size=20, min_size_mb=100)

    gte_calls = [c for c in fake_query.calls if c[0] == "gte"]
    assert ("file_size_bytes", 100 * 1024 * 1024) in [c[1] for c in gte_calls]


@pytest.mark.asyncio
async def test_list_invalid_sort_field_falls_back_to_created_at(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_video_versions(
        page=1, page_size=20, sort_by="DROP TABLE"
    )

    order = next(c for c in fake_query.calls if c[0] == "order")
    assert order[1] == ("created_at",)


@pytest.mark.asyncio
async def test_list_pagination_math(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    fake_query._count = 0
    await repo.list_video_versions(page=4, page_size=25)

    range_call = next(c for c in fake_query.calls if c[0] == "range")
    # page=4, page_size=25 → range(75, 99)
    assert range_call[1] == (75, 99)


# ─── Batch + mutations ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_versions_for_batch_retry_failed(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [{"id": "v1", "resource_id": "r1"}]
    rows = await repo.list_versions_for_batch("retry_failed")
    assert rows == [{"id": "v1", "resource_id": "r1"}]

    eq_calls = [c for c in fake_query.calls if c[0] == "eq"]
    assert ("transcode_status", "failed") in [c[1] for c in eq_calls]


@pytest.mark.asyncio
async def test_list_versions_for_batch_transcode_new(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.list_versions_for_batch("transcode_new")

    is_calls = [c for c in fake_query.calls if c[0] == "is_"]
    assert ("transcode_status", "null") in [c[1] for c in is_calls]


@pytest.mark.asyncio
async def test_mark_pending_updates_status(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.mark_pending("v1")

    update_call = next(c for c in fake_query.calls if c[0] == "update")
    assert update_call[1] == ({"transcode_status": "pending"},)


# ─── Settings ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_settings_returns_key_value_map(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"key": "transcode_enabled", "value": True},
        {"key": "transcode_tiers", "value": "720p"},
    ]
    settings = await repo.load_settings()
    assert settings["transcode_enabled"] is True
    assert settings["transcode_tiers"] == "720p"


@pytest.mark.asyncio
async def test_upsert_setting_sends_payload(
    repo: AdminTranscodeRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    await repo.upsert_setting("transcode_enabled", False, "admin-1")

    upsert_call = next(c for c in fake_query.calls if c[0] == "upsert")
    assert upsert_call[1] == (
        {
            "key": "transcode_enabled",
            "value": False,
            "updated_by": "admin-1",
        },
    )
