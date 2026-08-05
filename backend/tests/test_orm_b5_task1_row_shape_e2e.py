"""Real-SQLAlchemy-Result regression guard for the row-shape class of bug
(Phase B5 Task 1 code review round 1, 2026-08-05).

Mirrors tests/test_scheduled_master_row_shape_e2e.py: every purely
compile-level assertion in this batch's coverage would pass whether a
statement selects/returns column-level (correct) or entity-level (the B4
Critical bug class — ``select(Entity)``/``insert(Entity).returning(Entity)``
maps each row to ONE key (the entity name) instead of one key per column,
and every consumer's ``row["col"]``/``row.get("col")`` silently returns
None/raises instead). Compile-level assertions cannot tell the two apart
because they render near-identical SQL text; only a REAL materialized
``Result`` exposes the difference.

This file proves, for each of the three highest-risk RETURNING/SELECT sites
the review flagged, with a genuine ``aiosqlite``-backed ``AsyncSession``:

  1. the REAL production statement (imported from the module, never locally
     reconstructed) yields a RowMapping whose keys/values match exactly what
     the production consumer reads, and
  2. (negative control) the entity-level alternative, executed against the
     identical table/row, produces the wrong shape — proving test #1 is
     actually sensitive to the bug class, not just re-confirming whatever
     the code already does.

Covers:
  - app.services.ai_usage._team_budget_select_stmt / _team_budget_upsert_stmt
    (team_ai_budgets)
  - app.services.library.generated_media_service._generated_media_insert_stmt
    (generated_media)
  - app.repositories.episode_repository._progress_stmt (episodes join
    script_projects/script_scenes/script_shots, FILTERed aggregate)

Real model ``__table__`` objects drive both DDL and INSERT (real bind/result
processors); only the DDL is hand-rolled with SQLite-native column types
because the real models declare Postgres-only DDL (JSONB, dialect UUID,
BigInteger identity, ``now()``/``generate_snowflake_id()`` server defaults)
that SQLite's DDL compiler cannot render — ``schema_translate_map`` strips
the compiled statement's ``public.`` prefix so the unqualified SQLite table
resolves.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Episodes,
    GeneratedMedia,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
    TeamAiBudgets,
)
from app.repositories.episode_repository import _progress_stmt
from app.services.ai_usage import _team_budget_select_stmt, _team_budget_upsert_stmt
from app.services.library.generated_media_service import _generated_media_insert_stmt

_TEAM_ID = 900123456789
_UID = uuid.UUID("11111111-1111-1111-1111-111111111111")


# ── app.services.ai_usage — team_ai_budgets SELECT + upsert RETURNING ──────


_TEAM_BUDGETS_DDL = """
CREATE TABLE team_ai_budgets (
    team_id INTEGER PRIMARY KEY, monthly_budget_cents NUMERIC,
    updated_by_user_id TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
)
"""


@asynccontextmanager
async def _real_session_with_one_budget_row():
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_TEAM_BUDGETS_DDL)
        await conn.execute(
            insert(TeamAiBudgets.__table__).values(
                team_id=_TEAM_ID,
                monthly_budget_cents=Decimal("500.00"),
                updated_by_user_id=_UID,
                created_at=now,
                updated_at=now,
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
async def test_team_budget_select_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_team_budget_select_stmt, imported —
    not reconstructed here) round-tripped through a genuine SQLAlchemy
    Result gives a RowMapping whose keys/values match get_team_budget's
    ``dict(row)`` consumption and is_team_over_budget's
    ``budget_row.get("monthly_budget_cents")`` read."""
    async with _real_session_with_one_budget_row() as session:
        rows = (
            (await session.execute(_team_budget_select_stmt(_TEAM_ID))).mappings().all()
        )

    assert len(rows) == 1
    row = rows[0]
    assert row["team_id"] == _TEAM_ID
    assert row.get("monthly_budget_cents") == "500.00" or Decimal(
        str(row.get("monthly_budget_cents"))
    ) == Decimal("500.00")
    assert str(row.get("updated_by_user_id")) == str(_UID)


@pytest.mark.asyncio
async def test_team_budget_select_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: the KNOWN-BAD select(TeamAiBudgets) form, executed
    against the exact same real table/row, must produce the wrong shape —
    proving the test above actually distinguishes correct from broken."""
    async with _real_session_with_one_budget_row() as session:
        bad_stmt = select(TeamAiBudgets).execution_options(
            schema_translate_map={"public": None}
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert list(bad_row.keys()) == ["TeamAiBudgets"]  # entity-keyed, not column-keyed
    assert bad_row.get("monthly_budget_cents") is None  # get_team_budget's read breaks
    with pytest.raises(KeyError):
        bad_row["team_id"]


@pytest.mark.asyncio
async def test_team_budget_upsert_stmt_returning_yields_column_keyed_row():
    """The REAL production upsert statement (_team_budget_upsert_stmt) —
    ON CONFLICT DO UPDATE ... RETURNING — round-tripped through a genuine
    Result gives a column-keyed RowMapping, matching upsert_team_budget's
    ``dict(row)`` consumption."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_TEAM_BUDGETS_DDL)

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        stmt = _team_budget_upsert_stmt(
            _TEAM_ID, monthly_budget_cents=Decimal("1000.00"), updated_by_user_id=_UID
        )
        async with sessionmaker() as session:
            row = (await session.execute(stmt)).mappings().first()
            await session.commit()
    finally:
        await engine.dispose()

    assert row is not None
    assert row["team_id"] == _TEAM_ID
    assert Decimal(str(row["monthly_budget_cents"])) == Decimal("1000.00")
    assert str(row["updated_by_user_id"]) == str(_UID)


@pytest.mark.asyncio
async def test_team_budget_upsert_stmt_entity_level_negative_control():
    """Negative control: pg_insert(TeamAiBudgets).returning(TeamAiBudgets)
    (entity-level RETURNING) produces the wrong shape for the exact same
    insert — proving the column-level RETURNING choice matters here too."""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_TEAM_BUDGETS_DDL)

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = (
            sqlite_insert(TeamAiBudgets)
            .values(
                team_id=_TEAM_ID,
                monthly_budget_cents=Decimal("1000.00"),
                updated_by_user_id=_UID,
            )
            .returning(TeamAiBudgets)
        )
        async with sessionmaker() as session:
            bad_row = (await session.execute(bad_stmt)).mappings().first()
            await session.commit()
    finally:
        await engine.dispose()

    assert bad_row is not None
    assert list(bad_row.keys()) == ["TeamAiBudgets"]
    assert bad_row.get("monthly_budget_cents") is None
    with pytest.raises(KeyError):
        bad_row["team_id"]


# ── generated_media_service — INSERT ... RETURNING (register/upload path) ──


_GENERATED_MEDIA_DDL = """
CREATE TABLE generated_media (
    id INTEGER PRIMARY KEY, scope_id INTEGER, creator_id TEXT,
    media_kind TEXT, mime TEXT, file_path TEXT, file_size_bytes INTEGER,
    content_sha256 TEXT, origin_kind TEXT, origin_run_id TEXT,
    agent_id TEXT, canvas_id INTEGER, node_id TEXT, prompt TEXT,
    model TEXT, provider TEXT, params TEXT, cost_cents REAL,
    parent_resource_id INTEGER, derivation_kind TEXT,
    promoted_resource_id INTEGER, conversation_id INTEGER,
    created_at TIMESTAMP
)
"""


@pytest.mark.asyncio
async def test_generated_media_insert_stmt_returning_yields_column_keyed_row():
    """The REAL production statement (_generated_media_insert_stmt, imported
    — shared by register_generated_media and _insert_uploaded_row) gives a
    column-keyed RowMapping when RETURNING is consumed, matching both
    callers' ``dict(row)``."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_GENERATED_MEDIA_DDL)

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        stmt = _generated_media_insert_stmt(
            scope_id=42,
            creator_id=_UID,
            media_kind="image",
            mime="image/png",
            file_path="teams/42/chat/x/y/shot.png",
            file_size_bytes=3,
            content_sha256=None,
            origin_kind="chat_upload",
            origin_run_id=None,
            agent_id=None,
            canvas_id=None,
            node_id=None,
            prompt=None,
            model=None,
            provider=None,
            params={},
            cost_cents=None,
            parent_resource_id=None,
            derivation_kind=None,
            conversation_id=5,
        )
        async with sessionmaker() as session:
            row = (await session.execute(stmt)).mappings().first()
            await session.commit()
    finally:
        await engine.dispose()

    assert row is not None
    assert row["scope_id"] == 42
    assert row["creator_id"] == _UID
    assert row["media_kind"] == "image"
    assert row["origin_kind"] == "chat_upload"
    assert row["conversation_id"] == 5
    assert row["file_path"] == "teams/42/chat/x/y/shot.png"
    assert row["file_size_bytes"] == 3


@pytest.mark.asyncio
async def test_generated_media_insert_stmt_entity_level_negative_control():
    """Negative control: insert(GeneratedMedia).returning(GeneratedMedia)
    (entity-level RETURNING) produces the wrong shape for the exact same
    insert values."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_GENERATED_MEDIA_DDL)

    sessionmaker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        bad_stmt = (
            insert(GeneratedMedia)
            .values(
                scope_id=42,
                creator_id=_UID,
                media_kind="image",
                mime="image/png",
                file_path="teams/42/chat/x/y/shot.png",
                file_size_bytes=3,
                origin_kind="chat_upload",
                params={},
                conversation_id=5,
            )
            .returning(GeneratedMedia)
        )
        async with sessionmaker() as session:
            bad_row = (await session.execute(bad_stmt)).mappings().first()
            await session.commit()
    finally:
        await engine.dispose()

    assert bad_row is not None
    assert list(bad_row.keys()) == ["GeneratedMedia"]
    assert bad_row.get("scope_id") is None
    with pytest.raises(KeyError):
        bad_row["file_path"]


# ── episode_repository — 4-table JOIN + FILTERed aggregate ─────────────────


_EPISODES_DDL = """
CREATE TABLE episodes (
    id INTEGER PRIMARY KEY, project_id INTEGER, title TEXT,
    sort_order INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP,
    current_node_id INTEGER
)
"""
_SCRIPT_PROJECTS_DDL = """
CREATE TABLE script_projects (
    id INTEGER PRIMARY KEY, project_id INTEGER, team_id INTEGER,
    name TEXT, created_by TEXT, display_code TEXT, description TEXT,
    settings_json TEXT, viewport_json TEXT, status TEXT,
    created_at TIMESTAMP, updated_at TIMESTAMP, episode_id INTEGER,
    numbering_locked_at TIMESTAMP
)
"""
_SCRIPT_SCENES_DDL = """
CREATE TABLE script_scenes (
    id INTEGER PRIMARY KEY, script_id INTEGER, chapter_id INTEGER,
    heading_int_ext TEXT, location_text TEXT, location_id INTEGER,
    time_of_day TEXT, content_json TEXT, content TEXT,
    content_version INTEGER, position_x REAL, position_y REAL,
    width REAL, height REAL, sort_order INTEGER,
    created_at TIMESTAMP, updated_at TIMESTAMP, scene_number TEXT,
    omitted_at TIMESTAMP
)
"""
_SCRIPT_SHOTS_DDL = """
CREATE TABLE script_shots (
    id INTEGER PRIMARY KEY, scene_id INTEGER, shot_number INTEGER,
    shot_type TEXT, camera_angle TEXT, camera_movement TEXT,
    focal_length TEXT, lighting TEXT, description TEXT,
    image_url TEXT, thumbnail_url TEXT, video_url TEXT,
    status TEXT, sort_order INTEGER, created_at TIMESTAMP,
    updated_at TIMESTAMP
)
"""


@asynccontextmanager
async def _real_session_with_one_episode_pipeline():
    """One episode -> one non-deleted script -> one scene -> one shot with
    BOTH image_url and video_url set (the image-then-video generation flow
    the OR-FILTER renders_count fix specifically guards against
    double-counting)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    async with engine.begin() as conn:
        for ddl in (
            _EPISODES_DDL,
            _SCRIPT_PROJECTS_DDL,
            _SCRIPT_SCENES_DDL,
            _SCRIPT_SHOTS_DDL,
        ):
            await conn.exec_driver_sql(ddl)
        await conn.execute(
            insert(Episodes.__table__).values(
                id=1, project_id=99, title="Ep 1", sort_order=0
            )
        )
        await conn.execute(
            insert(ScriptProjects.__table__).values(
                id=10,
                project_id=99,
                team_id=1,
                name="Script A",
                created_by=_UID,
                status="active",
                episode_id=1,
            )
        )
        await conn.execute(
            insert(ScriptScenes.__table__).values(
                id=100,
                script_id=10,
                content_json="[]",
                content="",
                content_version=0,
                sort_order=0,
            )
        )
        await conn.execute(
            insert(ScriptShots.__table__).values(
                id=1000,
                scene_id=100,
                status="done",
                image_url="sb://chat-media/a.png",
                video_url="sb://chat-media/a.mp4",
                sort_order=0,
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
async def test_progress_stmt_yields_column_keyed_row_and_counts_render_once():
    """The REAL production statement (_progress_stmt, imported — not
    reconstructed here) round-tripped through a genuine Result gives a
    column-keyed RowMapping matching _progress_row's field access, AND
    proves the OR-FILTER renders_count doesn't double-count a shot with
    both image_url and video_url set."""
    async with _real_session_with_one_episode_pipeline() as session:
        rows = (await session.execute(_progress_stmt(99))).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    assert row["episode_id"] == 1
    assert row["title"] == "Ep 1"
    assert row["sort_order"] == 0
    assert row["script_count"] == 1
    assert row["scene_count"] == 1
    assert row["shots_total"] == 1
    assert row["shots_done"] == 1
    # The single shot has BOTH image_url and video_url — must count once,
    # not twice (the double-FILTER regression this OR-FILTER guards against).
    assert row["renders_count"] == 1


@pytest.mark.asyncio
async def test_progress_stmt_entity_level_negative_control_proves_sensitivity():
    """Negative control: selecting the Episodes ENTITY alongside the
    aggregate columns (rather than Episodes.id/.title/.sort_order
    individually) produces an entity-keyed mapping for the episode fields —
    proving the column-level select choice in _progress_stmt is load-
    bearing, not incidental."""
    from sqlalchemy import and_, func
    from sqlalchemy import select as sa_select

    async with _real_session_with_one_episode_pipeline() as session:
        bad_stmt = (
            sa_select(
                Episodes,
                func.count(func.distinct(ScriptProjects.id)).label("script_count"),
            )
            .select_from(Episodes)
            .outerjoin(
                ScriptProjects,
                and_(
                    ScriptProjects.episode_id == Episodes.id,
                    ScriptProjects.status != "deleted",
                ),
            )
            .where(Episodes.project_id == 99)
            .group_by(Episodes.id)
            .execution_options(schema_translate_map={"public": None})
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    # entity-keyed under "Episodes", NOT "episode_id"/"title"/"sort_order"
    assert "Episodes" in bad_row.keys()
    assert bad_row.get("episode_id") is None
    assert bad_row.get("title") is None
    with pytest.raises(KeyError):
        bad_row["episode_id"]
