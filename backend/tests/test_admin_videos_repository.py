"""Unit tests for AdminVideosRepository (ORM 2.0, model-backed).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — the stats
COUNT/SUM reads go through ``read_scope()`` with ``select(func.count()/func.sum())``,
the list/get reads select ``ParsedMedia`` and convert row objects to SELECT
*-shaped dicts via ``_row``, and delete / reset_for_retry WRITE through
``write_scope()``. These tests mock ``read_scope`` / ``write_scope`` with a fake
session that captures every emitted ``(sql, binds)`` pair and returns in-memory
``ParsedMedia`` instances / configured scalars, so the compiled SQL shape + bind
params AND the ``_row`` value-type sweep (Enum(DownloadStatus) → bare .value STR;
id / datasize_bytes → native int; created_at → ISO STR) are asserted WITHOUT a
live database (the DSN-gated integration suite in
``tests/integration/test_admin_videos_repository_orm.py`` exercises the real
round-trip). This keeps fast, always-run coverage of the collapsed ORM bodies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

import app.repositories.admin.videos_repository as mod
from app.models import ParsedMedia
from app.models._enums import DownloadStatus
from app.repositories.admin.videos_repository import AdminVideosRepository


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeResult:
    """Supports ``.scalars().all()`` / ``.scalars().first()`` (model reads) and
    ``.first()`` (the RETURNING-id row of delete / reset_for_retry)."""

    def __init__(self, scalar_rows: list[Any], first_row: Any) -> None:
        self._scalar_rows = scalar_rows
        self._first_row = first_row

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_rows)

    def first(self) -> Any:
        return self._first_row


class _FakeSession:
    """Captures execute/scalar (sql, binds); returns configured rows / scalar."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scalar_rows: list[Any] = []
        self.first_row: Any = None
        self.scalar_value: int | None = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append((str(stmt), stmt.compile().params))
        return _FakeResult(self.scalar_rows, self.first_row)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append((str(stmt), stmt.compile().params))
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
    monkeypatch.setattr(mod, "write_scope", lambda: _ScopeCM(session))
    return session


@pytest.fixture
def repo() -> AdminVideosRepository:
    return AdminVideosRepository()


def _media(**overrides: Any) -> ParsedMedia:
    fields: dict[str, Any] = {
        "id": 12345,
        "platform_id": "abc123",
        "source_platform": "douyin",
        "video_download_status": DownloadStatus.COMPLETED,
        "datasize_bytes": 2048,
        "title": "Hello",
        "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return ParsedMedia(**fields)


def _bind_values(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


# ─── Stats ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_total_uses_count(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 250
    total = await repo.count_total()
    assert type(total) is int and total == 250
    assert "count(" in fake_session.calls[0][0].lower()


@pytest.mark.asyncio
async def test_count_total_returns_zero_when_none(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await repo.count_total() == 0


@pytest.mark.asyncio
async def test_count_by_status_filters_and_binds(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 12
    total = await repo.count_by_status("completed")
    assert type(total) is int and total == 12
    sql, binds = fake_session.calls[0]
    assert "video_download_status" in sql
    assert "completed" in binds.values()


@pytest.mark.asyncio
async def test_sum_storage_bytes_pushes_sum_and_filters_positive(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 300
    total = await repo.sum_storage_bytes()
    assert type(total) is int and total == 300
    sql = fake_session.calls[0][0].lower()
    assert "sum(" in sql
    assert "datasize_bytes" in sql


@pytest.mark.asyncio
async def test_sum_storage_bytes_returns_zero_when_none(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = None
    assert await repo.sum_storage_bytes() == 0


@pytest.mark.asyncio
async def test_counts_by_statuses_returns_mapping(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 5
    result = await repo.counts_by_statuses(["completed", "pending", "failed"])
    assert result == {"completed": 5, "pending": 5, "failed": 5}
    assert all(type(v) is int for v in result.values())


# ─── List ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_with_filters_applies_search_ilike(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_with_filters(page=1, page_size=20, search="cat")

    sql, binds = fake_session.calls[-1]  # the paginated SELECT
    # ilike renders as lower(col) LIKE lower(:pat) under the default dialect
    assert "like" in sql.lower()
    assert "%cat%" in binds.values()


@pytest.mark.asyncio
async def test_list_with_filters_invalid_sort_falls_back(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_with_filters(page=1, page_size=20, sort_by="DROP TABLE; --")

    sql, _ = fake_session.calls[-1]
    assert "ORDER BY" in sql
    assert "created_at DESC" in sql


@pytest.mark.asyncio
async def test_list_with_filters_pagination_math(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_with_filters(page=5, page_size=10)

    binds = fake_session.calls[-1][1]
    # page=5, page_size=10 → OFFSET 40, LIMIT 10
    assert 40 in binds.values()
    assert 10 in binds.values()


@pytest.mark.asyncio
async def test_list_with_filters_threads_status_and_platform(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    fake_session.scalar_value = 0
    await repo.list_with_filters(
        page=1,
        page_size=20,
        video_download_status="failed",
        source_platform="douyin",
    )

    sql, binds = fake_session.calls[-1]
    assert "video_download_status" in sql and "source_platform" in sql
    values = set(binds.values())
    assert "failed" in values and "douyin" in values


@pytest.mark.asyncio
async def test_list_with_filters_row_parity(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [_media()]
    fake_session.scalar_value = 1
    rows, total = await repo.list_with_filters(page=1, page_size=20)
    assert total == 1
    r = rows[0]
    # Enum → bare .value STR (NOT "DownloadStatus.COMPLETED")
    assert r["video_download_status"] == "completed"
    assert type(r["video_download_status"]) is str
    assert type(r["id"]) is int and r["id"] == 12345
    assert type(r["datasize_bytes"]) is int and r["datasize_bytes"] == 2048
    assert r["created_at"] == "2026-04-01T00:00:00+00:00"
    assert type(r["created_at"]) is str


# ─── Get / mutations ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_id_returns_row(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = [_media(id=1)]
    row = await repo.get_by_id(1)
    assert row is not None and row["id"] == 1
    sql, binds = fake_session.calls[-1]
    assert "parsed_media" in sql
    assert 1 in binds.values()


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_absent(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_rows = []
    assert await repo.get_by_id(999) is None


@pytest.mark.asyncio
async def test_delete_returns_true_when_row_deleted(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_row = (1,)  # RETURNING id
    assert await repo.delete(1) is True
    sql = fake_session.calls[-1][0].lower()
    assert sql.startswith("delete from") and "parsed_media" in sql


@pytest.mark.asyncio
async def test_delete_returns_false_when_nothing_deleted(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_row = None
    assert await repo.delete(1) is False


@pytest.mark.asyncio
async def test_reset_for_retry_sends_correct_payload(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_row = (1,)  # RETURNING id
    assert await repo.reset_for_retry(1) is True

    sql, binds = fake_session.calls[-1]
    assert sql.lower().startswith("update") and "parsed_media" in sql.lower()
    values = set(binds.values())
    assert "pending" in values
    assert None in values  # error_message = NULL


@pytest.mark.asyncio
async def test_reset_for_retry_returns_false_when_nothing_updated(
    repo: AdminVideosRepository, fake_session: _FakeSession
) -> None:
    fake_session.first_row = None
    assert await repo.reset_for_retry(999) is False
