"""Scale guards for the batch sweepers in ResourcesRepository.

``get_expired_trashed_resources`` and ``get_untranscoded_video_versions`` used to
issue unbounded SELECTs that PostgREST silently truncated at 1000 rows (and whose
ORM twins fetched the whole set into RAM). They must now apply a deterministic
ORDER BY + an explicit LIMIT so a backlog drains over successive sweeper runs
instead of being clipped.

Post-collapse these run on the ORM: a scope-mock fake session captures the
built SQLAlchemy statement and we compile it (literal binds) to assert the
ORDER BY column/direction and the LIMIT value.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List

import pytest

from app.repositories import resources_repository as repo_mod
from app.repositories.resources_repository import (
    EXPIRED_TRASH_BATCH,
    UNTRANSCODED_BATCH,
    ResourcesRepository,
)


class _Result:
    def mappings(self):
        return self

    def all(self) -> List[dict]:
        return []


class _CapSession:
    def __init__(self) -> None:
        self.stmt: Any = None

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.stmt = stmt
        return _Result()


def _repo_with(session: _CapSession) -> ResourcesRepository:
    @asynccontextmanager
    async def _fake_read_scope():
        yield session

    repo_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
    return ResourcesRepository()


def _compiled(session: _CapSession) -> str:
    assert session.stmt is not None, "expected a statement to be executed"
    return str(session.stmt.compile(compile_kwargs={"literal_binds": True})).lower()


@pytest.fixture(autouse=True)
def _restore_read_scope():
    original = repo_mod.read_scope
    yield
    repo_mod.read_scope = original  # type: ignore[assignment]


# ─── get_expired_trashed_resources ──────────────────────────────────


@pytest.mark.asyncio
async def test_expired_trash_default_is_bounded_and_ordered() -> None:
    session = _CapSession()
    repo = _repo_with(session)

    await repo.get_expired_trashed_resources()

    sql = _compiled(session)
    assert "order by public.resources.trashed_at asc" in sql  # oldest-trashed first
    assert f"limit {EXPIRED_TRASH_BATCH}" in sql


@pytest.mark.asyncio
async def test_expired_trash_respects_custom_limit() -> None:
    session = _CapSession()
    repo = _repo_with(session)

    await repo.get_expired_trashed_resources(older_than_days=15, limit=100)

    assert "limit 100" in _compiled(session)


@pytest.mark.asyncio
async def test_expired_trash_projection_includes_media_id_and_thumbnail() -> None:
    """C2 regression: ``scheduled_cleanup.py`` -> ``resources_service.
    cleanup_expired_trash`` is the ONLY unattended cleanup path, and that
    service reads ``resource.get("media_id")`` to decide whether the shared
    parsed_media row + physical file can be GC'd, and
    ``resource.get("thumbnail_path")`` for the thumbnail cleanup branch.
    Before the fix this projection selected only id/file_path/
    cover_image_path — media_id was silently None for every row on this
    path, so the scheduled sweeper NEVER ran the shared-object GC branch
    (every one of the ~909 single-object-shared-key rows measured
    2026-08-03 stayed kept_referenced forever) and thumbnail_path was dead
    code specifically here.

    Asserted against the REAL compiled SELECT (not a hand-built dict) so a
    future column drop is caught the same way the original bug would have
    been — a service-level test that stubs the repo's return value as a
    complete dict can never catch a missing SELECT column."""
    session = _CapSession()
    repo = _repo_with(session)

    await repo.get_expired_trashed_resources()

    sql = _compiled(session)
    assert "public.resources.media_id" in sql
    assert "public.resources.thumbnail_path" in sql


class _RowResult:
    def __init__(self, rows: List[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def all(self) -> List[dict]:
        return self._rows


class _DataSession:
    def __init__(self, rows: List[dict]) -> None:
        self._rows = rows

    async def execute(self, stmt: Any, params: Any = None) -> _RowResult:
        return _RowResult(self._rows)


@pytest.mark.asyncio
async def test_expired_trash_row_carries_media_id_and_thumbnail_through() -> None:
    """C2 regression (data-flow half): simulates what the fixed SELECT now
    returns and proves media_id / thumbnail_path survive ``_rest_parity``
    untouched and land in the dict ``cleanup_expired_trash`` reads —
    exercising the real repository method's row-shaping logic, not a stub
    that fabricates the post-fix shape directly."""
    rows = [
        {
            "id": 1,
            "file_path": "sb://library/t5/aa/bb/x.mp4",
            "cover_image_path": None,
            "media_id": 42,
            "thumbnail_path": "sb://library/derived/1/thumb.jpg",
        }
    ]

    @asynccontextmanager
    async def _fake_read_scope():
        yield _DataSession(rows)

    repo_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
    repo = ResourcesRepository()

    result = await repo.get_expired_trashed_resources()

    assert result[0]["media_id"] == 42
    assert result[0]["thumbnail_path"] == "sb://library/derived/1/thumb.jpg"


# ─── get_untranscoded_video_versions ────────────────────────────────


@pytest.mark.asyncio
async def test_untranscoded_default_is_bounded_and_ordered() -> None:
    session = _CapSession()
    repo = _repo_with(session)

    await repo.get_untranscoded_video_versions()

    sql = _compiled(session)
    assert "order by public.resource_versions.id asc" in sql
    assert f"limit {UNTRANSCODED_BATCH}" in sql


@pytest.mark.asyncio
async def test_untranscoded_respects_custom_limit() -> None:
    session = _CapSession()
    repo = _repo_with(session)

    await repo.get_untranscoded_video_versions(limit=25)

    assert "limit 25" in _compiled(session)
