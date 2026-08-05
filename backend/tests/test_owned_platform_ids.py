"""Unit tests for ``get_owned_platform_ids`` on the ORM-backed repo.

The method answers: of the given vids (parsed_media.platform_id), which
ones has this user already downloaded (a resources row with file_path
set)? Used by the Soda playlist endpoint to mark already-owned tracks so
the user only re-downloads new ones.

Phase C task 2: the method was migrated OFF the ``scoped_sql`` raw-``text()``
backstop onto a real SQLAlchemy ORM JOIN (``select(ParsedMedia.platform_id)
.distinct().select_from(Resources).join(ParsedMedia, ...)``), run through
``read_scope()``. The must-haves here are: the empty-input short-circuit (no
DB hit), and — since a mock ``session.execute`` can no longer distinguish a
correct JOIN/WHERE from a broken one the way the old string-SQL mock did —
a REAL ``aiosqlite``-backed session exercising the actual WHERE filters
(creator_id ownership AND file_path IS NOT NULL), mirroring the pattern in
``tests/test_orm_c_task1_row_shape_e2e.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ParsedMedia, Resources

_UID_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
_UID_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_empty_input_no_db_hit():
    """Empty platform_ids → set() WITHOUT opening a session."""
    from app.repositories import resources_repository as repo_mod
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("read_scope must not be opened for empty input")

    # Patch the module-level read_scope the repo uses; the short-circuit
    # must return before any session is opened.
    original = repo_mod.read_scope
    repo_mod.read_scope = _boom  # type: ignore[assignment]
    try:
        result = asyncio.run(repo.get_owned_platform_ids([], "user-1"))
    finally:
        repo_mod.read_scope = original  # type: ignore[assignment]
    assert result == set()


def _sqlite_type_for(col) -> str:
    """Crude storage-only type mapping for a real model column's SQLAlchemy
    type — good enough for SQLite (dynamically typed). Falls back to TEXT."""
    tname = type(col.type).__name__
    if tname in ("Integer", "BigInteger", "SmallInteger", "Boolean"):
        return "INTEGER"
    if tname in ("Numeric", "Double", "Float"):
        return "REAL"
    if tname in ("DateTime", "Date"):
        return "TIMESTAMP"
    return "TEXT"


def _sqlite_ddl_for_table(sa_table) -> str:
    cols = ", ".join(f'"{c.name}" {_sqlite_type_for(c)}' for c in sa_table.columns)
    return f"CREATE TABLE {sa_table.name} ({cols})"


_PARSED_MEDIA_DDL = _sqlite_ddl_for_table(ParsedMedia.__table__)
_RESOURCES_DDL = _sqlite_ddl_for_table(Resources.__table__)


@asynccontextmanager
async def _real_session_with_seeded_rows():
    """Three parsed_media/resources pairs:

      * vid_a — owned by user A, file_path SET → must be returned for A.
      * vid_b — owned by user A, file_path NULL (not yet downloaded) →
        must NOT be returned even though A owns the resources row.
      * vid_c — owned by user B (a different creator) with file_path SET →
        must NOT be returned for A's query (creator_id filter).

    ``vid_d`` is queried but never inserted, proving a miss doesn't blow up
    the batched IN(...) query.
    """
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_PARSED_MEDIA_DDL)
        await conn.exec_driver_sql(_RESOURCES_DDL)
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=1, platform_id="vid_a", original_url="http://x/a"
            )
        )
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=2, platform_id="vid_b", original_url="http://x/b"
            )
        )
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=3, platform_id="vid_c", original_url="http://x/c"
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=100,
                media_id=1,
                creator_id=_UID_A,
                file_path="sb://library/a.mp4",
                mime_type="video/mp4",
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=101,
                media_id=2,
                creator_id=_UID_A,
                file_path=None,
                mime_type="video/mp4",
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=102,
                media_id=3,
                creator_id=_UID_B,
                file_path="sb://library/c.mp4",
                mime_type="video/mp4",
            )
        )
    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            yield session
    finally:
        await engine.dispose()


def test_returns_subset_from_rows():
    """Real aiosqlite JOIN: returns only the platform_ids A both owns (via
    ``resources.creator_id``) AND has downloaded (``file_path IS NOT
    NULL``), across a batched IN(...) that includes a never-inserted id."""
    from app.repositories import resources_repository as repo_mod
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    async def _run():
        async with _real_session_with_seeded_rows() as session:

            @asynccontextmanager
            async def _fake_read_scope():
                yield session

            original = repo_mod.read_scope
            repo_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
            try:
                return await repo.get_owned_platform_ids(
                    ["vid_a", "vid_b", "vid_c", "vid_d"], _UID_A
                )
            finally:
                repo_mod.read_scope = original  # type: ignore[assignment]

    result = asyncio.run(_run())

    assert result == {"vid_a"}, (
        "must include vid_a (owned by A, downloaded); must exclude vid_b "
        "(owned by A but file_path NULL — not downloaded), vid_c (owned by "
        "a different creator), and vid_d (never inserted)"
    )


def test_distinct_dedupes_multiple_resource_rows_for_same_platform_id():
    """DISTINCT must collapse duplicates: two resources rows for the SAME
    parsed_media (e.g. re-downloaded / duplicated) must not produce the
    platform_id twice in the returned set — a real ``set()`` already dedupes
    on the Python side, but this pins that the ORM statement's own
    ``.distinct()`` doesn't somehow multiply rows into something a plain
    ``set()`` couldn't absorb (guards against a future JOIN fan-out change,
    e.g. adding a one-to-many join that isn't distinct-safe)."""
    from app.repositories import resources_repository as repo_mod
    from app.repositories.resources_repository import ResourcesRepository

    repo = ResourcesRepository()

    async def _run():
        engine = create_async_engine("sqlite+aiosqlite://")
        engine = engine.execution_options(schema_translate_map={"public": None})
        async with engine.begin() as conn:
            await conn.exec_driver_sql(_PARSED_MEDIA_DDL)
            await conn.exec_driver_sql(_RESOURCES_DDL)
            await conn.execute(
                insert(ParsedMedia.__table__).values(
                    id=1, platform_id="vid_a", original_url="http://x/a"
                )
            )
            # Two resources rows point at the SAME parsed_media, both owned
            # by A with a file_path set.
            await conn.execute(
                insert(Resources.__table__).values(
                    id=100,
                    media_id=1,
                    creator_id=_UID_A,
                    file_path="sb://library/a-v1.mp4",
                    mime_type="video/mp4",
                )
            )
            await conn.execute(
                insert(Resources.__table__).values(
                    id=101,
                    media_id=1,
                    creator_id=_UID_A,
                    file_path="sb://library/a-v2.mp4",
                    mime_type="video/mp4",
                )
            )
        sessionmaker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with sessionmaker() as session:

                @asynccontextmanager
                async def _fake_read_scope():
                    yield session

                original = repo_mod.read_scope
                repo_mod.read_scope = _fake_read_scope  # type: ignore[assignment]
                try:
                    return await repo.get_owned_platform_ids(["vid_a"], _UID_A)
                finally:
                    repo_mod.read_scope = original  # type: ignore[assignment]
        finally:
            await engine.dispose()

    result = asyncio.run(_run())
    assert result == {"vid_a"}
