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
