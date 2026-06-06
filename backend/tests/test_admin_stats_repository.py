"""Unit tests for AdminStatsRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.repositories.admin.stats_repository import AdminStatsRepository


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
def repo(fake_query: _FakeQuery) -> AdminStatsRepository:
    r = AdminStatsRepository()

    async def _client():
        return _FakeClient(fake_query)

    r._client = _client  # type: ignore[method-assign]
    return r


@pytest.mark.asyncio
async def test_count_user_profiles_no_filter(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 500
    count = await repo.count_user_profiles()
    assert count == 500

    # No `gte` filter when since is None
    assert not any(c[0] == "gte" for c in fake_query.calls)


@pytest.mark.asyncio
async def test_count_user_profiles_with_since_filter(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 12
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    count = await repo.count_user_profiles(since=since)
    assert count == 12

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("created_at", since.isoformat())


@pytest.mark.asyncio
async def test_count_parsed_media_status_filter(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 100
    count = await repo.count_parsed_media(video_download_status="completed")
    assert count == 100

    eq_call = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq_call[1] == ("video_download_status", "completed")


@pytest.mark.asyncio
async def test_count_parsed_media_combines_status_and_since(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._count = 7
    since = datetime(2026, 4, 1, tzinfo=timezone.utc)
    await repo.count_parsed_media(video_download_status="completed", since=since)

    assert any(
        c[0] == "eq" and c[1] == ("video_download_status", "completed")
        for c in fake_query.calls
    )
    assert any(
        c[0] == "gte" and c[1] == ("created_at", since.isoformat())
        for c in fake_query.calls
    )


@pytest.mark.asyncio
async def test_count_teams(repo: AdminStatsRepository, fake_query: _FakeQuery) -> None:
    fake_query._count = 42
    assert await repo.count_teams() == 42

    table = next(c for c in fake_query.calls if c[0] == "table")
    assert table[1] == ("teams",)


@pytest.mark.asyncio
async def test_distinct_active_users_dedups_in_python(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"user_id": "u1"},
        {"user_id": "u1"},
        {"user_id": "u2"},
    ]
    since = datetime(2026, 4, 17, tzinfo=timezone.utc)
    count = await repo.distinct_active_users_since(since)
    assert count == 2


@pytest.mark.asyncio
async def test_distinct_active_users_returns_zero_on_error(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    async def _raises():
        raise RuntimeError("table missing")

    fake_query.execute = _raises  # type: ignore[assignment]

    count = await repo.distinct_active_users_since(datetime.now(tz=timezone.utc))
    assert count == 0


@pytest.mark.asyncio
async def test_user_registrations_since_returns_rows(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = [
        {"created_at": "2026-04-01T00:00:00Z"},
        {"created_at": "2026-04-02T00:00:00Z"},
    ]
    rows = await repo.user_registrations_since(
        datetime(2026, 4, 1, tzinfo=timezone.utc)
    )
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_video_status_history_filters_by_since(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    fake_query._data = []
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.video_status_history(since)

    gte = next(c for c in fake_query.calls if c[0] == "gte")
    assert gte[1] == ("created_at", since.isoformat())


@pytest.mark.asyncio
async def test_completed_videos_by_user_is_resource_centric(
    repo: AdminStatsRepository, fake_query: _FakeQuery
) -> None:
    """Resource-centric fix: parsed_media.user_id was dropped in migration 083,
    so this now queries non-trashed ``resources`` by ``creator_id`` and maps each
    row to a ``{"user_id": <str>}`` shape (what the /storage handler groups on)."""
    fake_query._data = [
        {"creator_id": "u1"},
        {"creator_id": "u2"},
        {"creator_id": None},  # defensive: skipped
    ]
    rows = await repo.completed_videos_by_user()

    # Queries the resources table, not parsed_media.
    table = next(c for c in fake_query.calls if c[0] == "table")
    assert table[1] == ("resources",)
    # Filters out trashed resources.
    eq = next(c for c in fake_query.calls if c[0] == "eq")
    assert eq[1] == ("is_trashed", False)
    # Row shape: one {"user_id": <str>} per non-null creator_id.
    assert rows == [{"user_id": "u1"}, {"user_id": "u2"}]
