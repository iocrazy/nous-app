"""Real-SQLAlchemy-Result regression guard for the row-shape class of bug
(Phase C task 1 code review, 2026-08-05).

Mirrors ``tests/test_orm_b5_task1_row_shape_e2e.py``: every purely
compile-level assertion in this batch's coverage would pass whether a
statement selects/returns column-level (correct) or entity-level (the B4
Critical bug class — ``select(Entity)`` maps each row to ONE key, the entity
name, instead of one key per column, and every consumer's
``row["col"]``/``row.get("col")`` silently returns None/raises instead).
Compile-level assertions cannot tell the two apart because they render
near-identical SQL text; only a REAL materialized ``Result`` exposes the
difference.

This file proves, for the two highest-complexity JOIN-producing SELECT
sites from Phase C task 1's tenant-statement migration, with a genuine
``aiosqlite``-backed ``AsyncSession``:

  1. the REAL production statement (imported from the module, never locally
     reconstructed) yields a RowMapping whose keys/values match exactly what
     the production consumer reads, and
  2. (negative control) the entity-level alternative, executed against the
     identical table/row, produces the wrong shape — proving test #1 is
     actually sensitive to the bug class.

Covers:
  - app.workflows.ai_transcription._transcribe_inputs_select_stmt
    (parsed_media JOIN resources)
  - app.workflows.ai_summary._summary_inputs_select_stmt
    (parsed_media JOIN resources JOIN resource_transcripts)
  - app.workflows.thumbnail._backfill_candidates_stmt (single-table
    resources SELECT — simplest baseline)

The remaining Phase C task 1 SELECT sites (analyze_l1's resource lookup,
dbos_orchestrator's routing cache) are structurally simpler variants of the
two-table-join and single-table shapes already covered here (a 2-table join
with an added ORDER BY, and a single-table SELECT with no WHERE) — see
``tests/db/test_scope_enforcement_regression_c1.py`` for their compile-level
+ scope-choke-point coverage.

Real model ``__table__`` objects drive both DDL and INSERT (real bind/result
processors); only the DDL is hand-rolled with SQLite-native column types
because the real models declare Postgres-only DDL (Enum, BigInteger identity,
``now()``/``generate_snowflake_id()`` server defaults) that SQLite's DDL
compiler cannot render — ``schema_translate_map`` strips the compiled
statement's ``public.`` prefix so the unqualified SQLite table resolves.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ParsedMedia, Resources, ResourceTranscripts
from app.workflows.ai_summary import _summary_inputs_select_stmt
from app.workflows.ai_transcription import _transcribe_inputs_select_stmt
from app.workflows.thumbnail import _backfill_candidates_stmt

_UID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _sqlite_type_for(col) -> str:
    """Crude storage-only type mapping for a real model column's SQLAlchemy
    type — good enough for SQLite (dynamically typed; these are advisory
    affinities, not enforced). Falls back to TEXT for anything unmapped
    (JSONB / Enum / pgvector Vector — all fine to store/read as NULL, which
    is all these row-shape tests need for columns outside their own
    assertions)."""
    tname = type(col.type).__name__
    if tname in ("Integer", "BigInteger", "SmallInteger", "Boolean"):
        return "INTEGER"
    if tname in ("Numeric", "Double", "Float"):
        return "REAL"
    if tname in ("DateTime", "Date"):
        return "TIMESTAMP"
    return "TEXT"


def _sqlite_ddl_for_table(sa_table) -> str:
    """Generate a permissive SQLite CREATE TABLE from a real model's
    ``Table`` object (no constraints/defaults — storage shape only), so
    entity-level negative-control tests (``select(Entity)`` selects EVERY
    mapped column) have every column physically present. The real models
    declare Postgres-only DDL (JSONB, Enum, Uuid, BigInteger identity,
    ``now()``/``generate_snowflake_id()`` server defaults) that SQLite's DDL
    compiler cannot render directly, so this hand-maps each column's SA type
    to a generic SQLite affinity instead of reusing the real DDL."""
    cols = ", ".join(f'"{c.name}" {_sqlite_type_for(c)}' for c in sa_table.columns)
    return f"CREATE TABLE {sa_table.name} ({cols})"


# ── ai_transcription — parsed_media JOIN resources ──────────────────────


_PARSED_MEDIA_DDL = _sqlite_ddl_for_table(ParsedMedia.__table__)
_RESOURCES_DDL = _sqlite_ddl_for_table(Resources.__table__)
_RESOURCE_TRANSCRIPTS_DDL = _sqlite_ddl_for_table(ResourceTranscripts.__table__)


@asynccontextmanager
async def _real_session_with_one_transcribe_row():
    """One parsed_media row + its one resources row (JOIN key: media_id)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_PARSED_MEDIA_DDL)
        await conn.exec_driver_sql(_RESOURCES_DDL)
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=1,
                platform_id="pf-1",
                download_path="sb://library/v1/download.mp4",
                extract_audio_path="sb://library/v1/audio.mp3",
                music_download_path=None,
                title="Test Video",
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=100,
                media_id=1,
                creator_id=_UID,
                file_path="sb://library/v1/download.mp4",
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


@pytest.mark.asyncio
async def test_transcribe_inputs_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_transcribe_inputs_select_stmt,
    imported — not reconstructed here) round-tripped through a genuine
    Result gives a RowMapping whose keys/values match
    load_transcribe_inputs's ``media_row.get(...)``/``media_row["resource_id"]``
    reads."""
    async with _real_session_with_one_transcribe_row() as session:
        rows = (
            (await session.execute(_transcribe_inputs_select_stmt(1))).mappings().all()
        )

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row.get("download_path") == "sb://library/v1/download.mp4"
    assert row.get("extract_audio_path") == "sb://library/v1/audio.mp3"
    assert row.get("music_download_path") is None
    assert row["resource_id"] == 100


@pytest.mark.asyncio
async def test_transcribe_inputs_stmt_entity_level_negative_control():
    """Negative control: selecting ParsedMedia as an ENTITY (rather than its
    individual columns) alongside the resources.id label produces an
    entity-keyed mapping for the parsed_media fields — proving the
    column-level select choice in _transcribe_inputs_select_stmt is
    load-bearing, not incidental."""
    async with _real_session_with_one_transcribe_row() as session:
        bad_stmt = (
            select(ParsedMedia, Resources.id.label("resource_id"))
            .join(Resources, Resources.media_id == ParsedMedia.id)
            .where(ParsedMedia.id == 1)
            .limit(1)
            .execution_options(schema_translate_map={"public": None})
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    # entity-keyed under "ParsedMedia", NOT "download_path"/"extract_audio_path"
    assert "ParsedMedia" in bad_row.keys()
    assert bad_row.get("download_path") is None
    assert bad_row.get("extract_audio_path") is None
    with pytest.raises(KeyError):
        bad_row["download_path"]


# ── ai_summary — parsed_media JOIN resources JOIN resource_transcripts ──


@asynccontextmanager
async def _real_session_with_one_summary_row():
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_PARSED_MEDIA_DDL)
        await conn.exec_driver_sql(_RESOURCES_DDL)
        await conn.exec_driver_sql(_RESOURCE_TRANSCRIPTS_DDL)
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=2, platform_id="pf-2", title="Summary Target"
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=200, media_id=2, creator_id=_UID, file_path="x"
            )
        )
        await conn.execute(
            insert(ResourceTranscripts.__table__).values(
                id=uuid.uuid4(),
                resource_id=200,
                full_text="hello world transcript",
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


@pytest.mark.asyncio
async def test_summary_inputs_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_summary_inputs_select_stmt, imported)
    round-tripped through a genuine Result gives a RowMapping matching
    load_summary_inputs's ``row["transcript"]``/``row.get("title")`` reads."""
    async with _real_session_with_one_summary_row() as session:
        rows = (
            (await session.execute(_summary_inputs_select_stmt(2, _UID)))
            .mappings()
            .all()
        )

    assert len(rows) == 1
    row = rows[0]
    assert row["transcript"] == "hello world transcript"
    assert row["pm_id"] == 2
    assert row.get("title") == "Summary Target"
    assert row["resource_id"] == 200


@pytest.mark.asyncio
async def test_summary_inputs_stmt_entity_level_negative_control():
    """Negative control: selecting ResourceTranscripts as an ENTITY (rather
    than projecting full_text as a column) produces an entity-keyed mapping —
    proving the column-level select choice matters here too."""
    async with _real_session_with_one_summary_row() as session:
        bad_stmt = (
            select(ResourceTranscripts, ParsedMedia.id.label("pm_id"))
            .join(Resources, Resources.media_id == ParsedMedia.id)
            .join(ResourceTranscripts, ResourceTranscripts.resource_id == Resources.id)
            .where(ParsedMedia.id == 2)
            .limit(1)
            .execution_options(schema_translate_map={"public": None})
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert "ResourceTranscripts" in bad_row.keys()
    assert bad_row.get("transcript") is None
    with pytest.raises(KeyError):
        bad_row["transcript"]


# ── ai_summary — Bug B-2: load_summary_inputs error-message split ───────
#
# 'no transcript' and 'owned by someone else' used to share ONE message
# (both hit the same `if not row: raise RuntimeError("no transcript...")`),
# which sent the 2026-08-07 live diagnosis down the wrong path: the
# transcript existed, but load_summary_inputs's own tenant filter
# (Resources.creator_id == user_id) hid the row from a caller who wasn't
# the resource's owner. These two tests exercise load_summary_inputs()
# itself (not just the raw SELECT) end-to-end against a real aiosqlite
# session, monkeypatching get_sessionmaker() the way
# tests/db/test_maybe_unit_of_work.py does — load_summary_inputs opens its
# own session(s) via read_scope(), so there's no other way to point it at
# the throwaway engine.
#
# Real uuid.UUID objects (not str) throughout — see this file's earlier
# note: SQLite's Uuid bind_processor calls value.hex, which only a real
# UUID instance has; production binds a plain str against asyncpg/Postgres
# just fine (bind_processor is None there).

_CALLER_UID = uuid.UUID("44444444-4444-4444-4444-444444444444")
_OTHER_UID = uuid.UUID("55555555-5555-5555-5555-555555555555")


@asynccontextmanager
async def _real_sessionmaker_with_summary_row(*, creator_id, with_transcript: bool):
    """Same schema/DDL as _real_session_with_one_summary_row, but yields the
    SESSIONMAKER itself rather than one session — load_summary_inputs() opens
    its own session(s) internally, so the caller needs to monkeypatch
    get_sessionmaker() to return this."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_PARSED_MEDIA_DDL)
        await conn.exec_driver_sql(_RESOURCES_DDL)
        await conn.exec_driver_sql(_RESOURCE_TRANSCRIPTS_DDL)
        await conn.execute(
            insert(ParsedMedia.__table__).values(
                id=3, platform_id="pf-3", title="Task 3 Target"
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=300, media_id=3, creator_id=creator_id, file_path="x"
            )
        )
        if with_transcript:
            await conn.execute(
                insert(ResourceTranscripts.__table__).values(
                    id=uuid.uuid4(),
                    resource_id=300,
                    full_text="hello world transcript",
                )
            )
    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        yield sessionmaker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_load_summary_inputs_distinguishes_foreign_owner_from_missing_transcript(
    monkeypatch,
):
    """'no transcript' and 'owned by someone else' used to share one message,
    sending the 2026-08-07 diagnosis down the wrong path (transcript
    existed; the tenant filter hid it)."""
    import app.db.session as session_mod
    from app.workflows.ai_summary import load_summary_inputs

    async with _real_sessionmaker_with_summary_row(
        creator_id=_OTHER_UID, with_transcript=True
    ) as sessionmaker:
        monkeypatch.setattr(session_mod, "get_sessionmaker", lambda: sessionmaker)
        with pytest.raises(RuntimeError, match="not owned by"):
            await load_summary_inputs(3, _CALLER_UID)


@pytest.mark.asyncio
async def test_load_summary_inputs_when_transcript_truly_missing(monkeypatch):
    """Same caller owns the resource, but no transcript row exists — must
    still raise the original 'no transcript' message, not the new
    foreign-owner one."""
    import app.db.session as session_mod
    from app.workflows.ai_summary import load_summary_inputs

    async with _real_sessionmaker_with_summary_row(
        creator_id=_CALLER_UID, with_transcript=False
    ) as sessionmaker:
        monkeypatch.setattr(session_mod, "get_sessionmaker", lambda: sessionmaker)
        with pytest.raises(RuntimeError, match="no transcript"):
            await load_summary_inputs(3, _CALLER_UID)


# ── thumbnail — single-table resources SELECT (simplest baseline) ───────


@asynccontextmanager
async def _real_session_with_backfill_candidates():
    """One eligible resource (no thumbnail, no media_id, not trashed, real
    file_path) + one INELIGIBLE resource (already has a thumbnail_path) —
    proves the WHERE filters, not just the column projection, are real."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_RESOURCES_DDL)
        await conn.execute(
            insert(Resources.__table__).values(
                id=300,
                media_id=None,
                creator_id=_UID,
                file_path="teams/1/uploads/300/photo.jpg",
                mime_type="image/jpeg",
                thumbnail_path=None,
                is_trashed=False,
            )
        )
        await conn.execute(
            insert(Resources.__table__).values(
                id=301,
                media_id=None,
                creator_id=_UID,
                file_path="teams/1/uploads/301/photo.jpg",
                mime_type="image/jpeg",
                thumbnail_path="teams/1/uploads/301/thumb.jpg",  # already has one
                is_trashed=False,
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


@pytest.mark.asyncio
async def test_backfill_candidates_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_backfill_candidates_stmt, imported)
    round-tripped through a genuine Result gives a RowMapping matching
    _backfill_scan_step's ``r["id"]``/``r["file_path"]``/``r.get("mime_type")``
    dict-comprehension reads, and only returns the eligible row (WHERE
    thumbnail_path IS NULL excludes the one that already has a thumbnail)."""
    async with _real_session_with_backfill_candidates() as session:
        rows = (await session.execute(_backfill_candidates_stmt(10))).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 300
    assert row["file_path"] == "teams/1/uploads/300/photo.jpg"
    assert row.get("mime_type") == "image/jpeg"


@pytest.mark.asyncio
async def test_backfill_candidates_stmt_entity_level_negative_control():
    """Negative control: selecting Resources as an ENTITY produces an
    entity-keyed mapping — proving the column-level select choice in
    _backfill_candidates_stmt is load-bearing."""
    async with _real_session_with_backfill_candidates() as session:
        bad_stmt = (
            select(Resources)
            .where(Resources.thumbnail_path.is_(None))
            .where(Resources.media_id.is_(None))
            .where(Resources.is_trashed.is_(False))
            .execution_options(schema_translate_map={"public": None})
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert "Resources" in bad_row.keys()
    assert bad_row.get("file_path") is None
    with pytest.raises(KeyError):
        bad_row["file_path"]
