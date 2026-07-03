"""Unit tests for AdminTranscodeRepository (ORM 2.0, model-backed).

Post-rollout the repository is the SQLAlchemy 2.0 implementation — reads go through
``read_scope()`` with ``select`` statements (building SELECT-subset-shaped dicts by
hand) and writes through ``write_scope()`` (``update`` / ``pg_insert`` ON CONFLICT).
These tests mock those scopes with a fake session that captures every emitted
``(compiled_sql, binds)`` pair and returns in-memory rows, so the routing/filter
logic — status_filter "null"→IS NULL vs named→eq, min_size_mb MB→bytes, sort
fallback + pagination, the bounded/ordered batch working set, mark_pending UPDATE,
and settings load/upsert shape — is asserted WITHOUT a live database (the DSN-gated
integration suite in ``tests/integration/test_admin_transcode_repository_orm.py``
exercises the real round-trip). This keeps fast, always-run coverage of the
collapsed ORM bodies.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.repositories.admin.transcode_repository as mod
from app.repositories.admin.transcode_repository import (
    BATCH_VERSIONS_LIMIT,
    AdminTranscodeRepository,
)


class _Obj:
    """A stand-in for an ORM entity (attribute access) — used by scalars().all()."""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _Row(tuple):
    """A stand-in for a SQLAlchemy Row: supports both tuple-unpack and attr access."""

    def __new__(cls, **fields: Any) -> "_Row":
        self = super().__new__(cls, tuple(fields.values()))
        self._fields = fields
        return self

    def __getattr__(self, name: str) -> Any:
        try:
            return self._fields[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _FakeResult:
    """Supports ``.scalars().all()``, ``.all()`` and ``.first()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_FakeResult":
        return self


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    try:
        compiled = stmt.compile(dialect=postgresql.dialect())
        return str(compiled), dict(compiled.params)
    except Exception:
        return str(stmt), {}


class _FakeSession:
    """Captures execute/scalar (compiled sql, binds); returns configured rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rows: list[Any] = []
        self.scalar_value: int = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self.rows)

    async def scalar(self, stmt: Any) -> Any:
        self.calls.append(_compile(stmt))
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
def repo() -> AdminTranscodeRepository:
    return AdminTranscodeRepository()


def _all_sql(session: _FakeSession) -> str:
    return "\n".join(sql for sql, _ in session.calls)


def _all_binds(session: _FakeSession) -> list[Any]:
    values: list[Any] = []
    for _sql, params in session.calls:
        values.extend(params.values())
    return values


# ─── Stats ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_total_video_versions_filters_video_mime(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 100
    count = await repo.count_total_video_versions()
    assert count == 100 and type(count) is int
    assert "mime_type LIKE" in _all_sql(fake_session)
    assert "video/%" in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_count_by_status_filters_both_mime_and_status(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 5
    count = await repo.count_by_status("completed")
    assert count == 5
    sql = _all_sql(fake_session)
    assert "mime_type LIKE" in sql and "transcode_status =" in sql
    assert "completed" in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_status_counts_returns_mapping(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.scalar_value = 3  # every count returns 3 in the fake
    result = await repo.status_counts(["completed", "failed"])
    assert result == {"completed": 3, "failed": 3}


# ─── List ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_null_status_uses_is_null(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_video_versions(page=1, page_size=20, status_filter="null")
    assert "transcode_status IS NULL" in _all_sql(fake_session)


@pytest.mark.asyncio
async def test_list_named_status_uses_eq(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_video_versions(page=1, page_size=20, status_filter="completed")
    assert "transcode_status =" in _all_sql(fake_session)
    assert "completed" in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_list_min_size_converts_mb_to_bytes(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_video_versions(page=1, page_size=20, min_size_mb=100)
    assert 100 * 1024 * 1024 in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_list_invalid_sort_field_falls_back_to_created_at(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_video_versions(page=1, page_size=20, sort_by="DROP TABLE")
    sql = _all_sql(fake_session)
    assert "ORDER BY" in sql and "created_at" in sql
    assert "DROP TABLE" not in sql  # invalid sort never reaches SQL


@pytest.mark.asyncio
async def test_list_pagination_math(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_video_versions(page=4, page_size=25)
    # page=4, page_size=25 → LIMIT 25 OFFSET 75
    binds = _all_binds(fake_session)
    assert 25 in binds and 75 in binds


@pytest.mark.asyncio
async def test_list_projects_list_columns_and_parity(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    from datetime import datetime, timezone

    ts = datetime(2026, 4, 16, 12, tzinfo=timezone.utc)
    fake_session.scalar_value = 1
    fake_session.rows = [
        _Obj(
            id=123,
            resource_id=456,
            version_number=1,
            filename="v.mp4",
            file_size_bytes=50 * 1024 * 1024,
            mime_type="video/mp4",
            transcode_status="failed",
            hls_path=None,
            transcode_at=None,
            created_at=ts,
        )
    ]
    rows, total = await repo.list_video_versions(page=1, page_size=20)
    assert total == 1 and type(total) is int
    r = rows[0]
    assert set(r.keys()) == {
        "id",
        "resource_id",
        "version_number",
        "filename",
        "file_size_bytes",
        "mime_type",
        "transcode_status",
        "hls_path",
        "transcode_at",
        "created_at",
    }
    assert type(r["id"]) is int and type(r["file_size_bytes"]) is int
    assert r["created_at"] == ts.isoformat() and type(r["created_at"]) is str


# ─── Bulk maps ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resources_to_media_str_keyed(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [_Row(id=456, media_id=789)]
    m = await repo.resources_to_media(["456"])
    assert m == {"456": "789"}


@pytest.mark.asyncio
async def test_resources_to_media_empty_short_circuits(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    assert await repo.resources_to_media([]) == {}
    assert fake_session.calls == []  # no query issued


@pytest.mark.asyncio
async def test_media_info_bulk_shape(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        _Row(
            id=789,
            title="clip",
            cover_urls=["c.jpg"],
            cover_download_path="/c",
            source_platform="douyin",
            author="me",
        )
    ]
    info = await repo.media_info_bulk(["789"])
    assert set(info) == {"789"}
    assert info["789"]["title"] == "clip" and type(info["789"]["id"]) is int


# ─── Batch + mutations ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_version_shape(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [_Row(id=1, resource_id=2, mime_type="video/mp4")]
    v = await repo.get_version("1")
    assert v == {"id": 1, "resource_id": 2, "mime_type": "video/mp4"}


@pytest.mark.asyncio
async def test_get_version_absent_none(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    assert await repo.get_version("999") is None


@pytest.mark.asyncio
async def test_list_versions_for_batch_retry_failed(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [_Row(id=1, resource_id=2, mime_type="video/mp4")]
    rows = await repo.list_versions_for_batch("retry_failed")
    assert rows == [{"id": 1, "resource_id": 2, "mime_type": "video/mp4"}]
    sql = _all_sql(fake_session)
    assert "transcode_status =" in sql
    assert "failed" in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_list_versions_for_batch_transcode_new(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_versions_for_batch("transcode_new")
    assert "transcode_status IS NULL" in _all_sql(fake_session)


@pytest.mark.asyncio
async def test_list_versions_for_batch_is_bounded_and_ordered(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    # Scale guard: the working set must be deterministically ordered and capped
    # so it can never silently truncate at PostgREST's 1000 ceiling.
    fake_session.rows = []
    await repo.list_versions_for_batch("transcode_new")
    sql = _all_sql(fake_session)
    assert "ORDER BY" in sql and "id ASC" in sql
    assert BATCH_VERSIONS_LIMIT in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_list_versions_for_batch_respects_custom_limit(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = []
    await repo.list_versions_for_batch("retry_failed", limit=50)
    assert 50 in _all_binds(fake_session)


@pytest.mark.asyncio
async def test_mark_pending_updates_status(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    await repo.mark_pending("1")
    sql = _all_sql(fake_session)
    assert "resource_versions SET transcode_status" in sql
    assert "pending" in _all_binds(fake_session)


# ─── Settings ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_settings_returns_key_value_map(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    fake_session.rows = [
        _Row(key="transcode_enabled", value=True),
        _Row(key="transcode_tiers", value="720p"),
    ]
    settings = await repo.load_settings()
    assert settings["transcode_enabled"] is True
    assert settings["transcode_tiers"] == "720p"
    assert "key LIKE" in _all_sql(fake_session)


@pytest.mark.asyncio
async def test_upsert_setting_on_conflict_payload(
    repo: AdminTranscodeRepository, fake_session: _FakeSession
) -> None:
    await repo.upsert_setting("transcode_enabled", False, "admin-1")
    sql = _all_sql(fake_session)
    assert "system_settings" in sql and "ON CONFLICT" in sql
    binds = _all_binds(fake_session)
    assert "transcode_enabled" in binds and "admin-1" in binds and False in binds
