"""Unit tests for AdminStatsRepository (ORM 2.0, model-backed reads).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go
through ``read_scope()`` with ``select(...)``/``select(func.count())``
statements. These tests mock ``read_scope`` with a fake session that captures
every emitted ``(sql, binds)`` pair and returns configured scalar values /
row tuples, so the compiled SQL shape + bind params AND the value-type sweep
(created_at → ISO str, video_download_status Enum → bare .value via _plain,
creator_id → str) are asserted WITHOUT a live database (the DSN-gated
integration suite in ``tests/integration/test_stats_repository_orm.py``
exercises the real round-trip). This keeps fast, always-run coverage of the
collapsed ORM bodies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.stats_repository as mod
from app.repositories.admin.stats_repository import AdminStatsRepository


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeSession:
    """Captures execute/scalar (sql, binds); returns configured rows/scalar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_value: Any = 0
        self.rows: list[tuple[Any, ...]] = []
        self.raise_on_scalar: Exception | None = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append((str(stmt), stmt.compile().params))
        if self.raise_on_scalar is not None:
            raise self.raise_on_scalar
        return self.scalar_value


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(mod, "read_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AdminStatsRepository:
    return AdminStatsRepository()


@pytest.mark.asyncio
async def test_count_user_profiles_no_filter(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 500
    count = await repo.count_user_profiles()
    assert count == 500

    sql, _ = fake_session.calls[-1]
    assert "user_profiles" in sql
    assert "created_at >=" not in sql  # no since filter → no gte clause


@pytest.mark.asyncio
async def test_count_user_profiles_with_since_filter(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 12
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    count = await repo.count_user_profiles(since=since)
    assert count == 12

    sql, binds = fake_session.calls[-1]
    assert "created_at >=" in sql
    assert since in binds.values()


@pytest.mark.asyncio
async def test_count_parsed_media_status_filter(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 100
    count = await repo.count_parsed_media(video_download_status="completed")
    assert count == 100

    sql, binds = fake_session.calls[-1]
    assert "video_download_status =" in sql
    assert "completed" in binds.values()


@pytest.mark.asyncio
async def test_count_parsed_media_combines_status_and_since(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 7
    since = datetime(2026, 4, 1, tzinfo=timezone.utc)
    await repo.count_parsed_media(video_download_status="completed", since=since)

    sql, binds = fake_session.calls[-1]
    assert "video_download_status =" in sql
    assert "created_at >=" in sql
    assert "completed" in binds.values()
    assert since in binds.values()


@pytest.mark.asyncio
async def test_count_teams(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 42
    assert await repo.count_teams() == 42

    sql, _ = fake_session.calls[-1]
    assert "teams" in sql


@pytest.mark.asyncio
async def test_distinct_active_users_pushes_distinct_to_pg(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    """DISTINCT is pushed to PG (COUNT(DISTINCT user_id)) rather than deduped in
    Python — the scalar already carries the deduped count."""
    fake_session.scalar_value = 2
    since = datetime(2026, 4, 17, tzinfo=timezone.utc)
    count = await repo.distinct_active_users_since(since)
    assert count == 2

    sql, binds = fake_session.calls[-1]
    assert "distinct" in sql.lower()
    assert "user_logs" in sql
    assert since in binds.values()


@pytest.mark.asyncio
async def test_distinct_active_users_returns_zero_on_error(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.raise_on_scalar = RuntimeError("table missing")

    count = await repo.distinct_active_users_since(datetime.now(tz=timezone.utc))
    assert count == 0


@pytest.mark.asyncio
async def test_user_registrations_since_returns_rows(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        (datetime(2026, 4, 1, tzinfo=timezone.utc),),
        (datetime(2026, 4, 2, tzinfo=timezone.utc),),
    ]
    rows = await repo.user_registrations_since(
        datetime(2026, 4, 1, tzinfo=timezone.utc)
    )
    assert len(rows) == 2
    # created_at → ISO str (CONSUMED — the growth endpoint slices [:10]).
    assert rows[0]["created_at"] == "2026-04-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_video_status_history_filters_by_since(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.video_status_history(since)

    sql, binds = fake_session.calls[-1]
    assert "created_at >=" in sql
    assert since in binds.values()


@pytest.mark.asyncio
async def test_video_status_history_unwraps_enum_via_plain(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    """video_download_status Enum member → bare .value (CONSUMED — the router
    does status == 'completed')."""
    from app.models._enums import DownloadStatus

    fake_session.rows = [
        (datetime(2026, 4, 1, tzinfo=timezone.utc), DownloadStatus.COMPLETED),
    ]
    rows = await repo.video_status_history(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert rows[0]["video_download_status"] == "completed"
    assert "DownloadStatus." not in rows[0]["video_download_status"]


@pytest.mark.asyncio
async def test_completed_videos_by_user_is_resource_centric(
    repo: AdminStatsRepository, fake_session: _FakeSession
) -> None:
    """Resource-centric fix: parsed_media.user_id was dropped in migration 083,
    so this now queries non-trashed ``resources`` by ``creator_id`` and maps each
    row to a ``{"user_id": <str>}`` shape (what the /storage handler groups on)."""
    fake_session.rows = [
        ("11111111-1111-1111-1111-111111111111",),
        ("22222222-2222-2222-2222-222222222222",),
        (None,),  # defensive: skipped
    ]
    rows = await repo.completed_videos_by_user()

    sql, _ = fake_session.calls[-1]
    assert "resources" in sql
    assert "is_trashed IS false" in sql
    assert rows == [
        {"user_id": "11111111-1111-1111-1111-111111111111"},
        {"user_id": "22222222-2222-2222-2222-222222222222"},
    ]
