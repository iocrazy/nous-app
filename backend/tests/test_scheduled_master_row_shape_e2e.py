"""Real-SQLAlchemy-Result regression guard for the row-shape class of bug
(Final Review F1/F2, 2026-08-05).

``fire_due_schedules_step`` originally shipped with ``select(UserSchedules)``
(entity-level) instead of ``select(*UserSchedules.__table__.c)``
(column-level). Both compile to visually-identical SQL text
(``SELECT public.user_schedules.* FROM ...``), so every purely
compile-level assertion in this batch's coverage passed either way — the
bug only shows up once a REAL ``Result`` is materialized:
``select(Entity)`` maps each row to ONE key (the entity name, e.g.
``{'UserSchedules': <instance>}``), while ``select(*cols)`` maps each row to
one key PER COLUMN. Every downstream consumer in scheduled_master.py
(``_dispatch_one``, ``_record_dispatch_failure``, ``_reset_consecutive_fails``)
does ``row.get('task_type')`` / ``row['id']`` — against an entity-keyed
mapping those silently return None / raise KeyError, which single-handedly
stopped the entire user_schedules scheduler (every due row errored before
reaching a workflow dispatch) without the DBOS step ever raising.

This file proves TWO things with a genuine ``aiosqlite``-backed
``AsyncSession`` (no fake/dict-based test double):

  1. ``_due_schedules_stmt`` — imported from the real production module, not
     reconstructed here — yields a real ``RowMapping`` whose keys and values
     match exactly what the row consumers read.
  2. (negative control) the KNOWN-BAD ``select(UserSchedules)`` alternative,
     executed against the identical table/row, produces the wrong shape —
     proving test #1 is actually sensitive to the bug class, not just
     re-confirming whatever the code already does.

Uses ``UserSchedules.__table__`` directly for both DDL and the INSERT (real
column types, real bind/result processors — e.g. Uuid demands real
``uuid.UUID`` values, not strings) so this exercises the SAME processors
production traffic would; only the DDL is hand-rolled with SQLite-native
column types because the real model declares Postgres-only DDL (JSONB,
dialect UUID, ``now()``/``gen_random_uuid()`` server defaults) that SQLite's
DDL compiler cannot render — ``schema_translate_map`` strips the compiled
statement's ``public.`` prefix so the unqualified SQLite table resolves.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import UserSchedules
from app.workflows.scheduled_master import _due_schedules_stmt

_SCHED_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

_CREATE_SQL = """
CREATE TABLE user_schedules (
    id TEXT PRIMARY KEY, name TEXT, cron_expr TEXT, task_type TEXT,
    payload TEXT, lane TEXT, enabled INTEGER, next_fire_at TIMESTAMP,
    fire_count INTEGER, fail_count INTEGER, created_at TIMESTAMP,
    updated_at TIMESTAMP, user_id TEXT, last_fired_at TIMESTAMP,
    last_error TEXT, timezone TEXT, consecutive_fails INTEGER,
    paused_at TIMESTAMP, pause_reason TEXT, skipped_count INTEGER,
    stale_after_minutes INTEGER
)
"""


@asynccontextmanager
async def _real_session_with_one_due_row(now: datetime):
    """Genuine aiosqlite engine + AsyncSession with one due user_schedules
    row, inserted through the real ORM Table (real bind processors)."""
    engine = create_async_engine("sqlite+aiosqlite://")
    engine = engine.execution_options(schema_translate_map={"public": None})
    due_at = now - timedelta(minutes=1)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_CREATE_SQL)
        await conn.execute(
            insert(UserSchedules.__table__).values(
                id=_SCHED_ID,
                name="Daily digest",
                cron_expr="0 9 * * *",
                task_type="agent_routine",
                payload={},
                lane="scheduled",
                enabled=True,
                next_fire_at=due_at,
                fire_count=3,
                fail_count=0,
                created_at=now,
                updated_at=now,
                user_id=_USER_ID,
                timezone="UTC",
                consecutive_fails=2,
                skipped_count=0,
                stale_after_minutes=60,
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
async def test_due_schedules_stmt_yields_column_keyed_row_against_real_sqlite():
    """The REAL production statement (_due_schedules_stmt, imported — not
    rebuilt here) round-tripped through a genuine SQLAlchemy Result gives a
    RowMapping whose keys/values match every consumer's field access."""
    now = datetime.now(timezone.utc)
    async with _real_session_with_one_due_row(now) as session:
        rows = (await session.execute(_due_schedules_stmt(now))).mappings().all()

    assert len(rows) == 1
    row = rows[0]
    # Exactly the field accesses _dispatch_one / _record_dispatch_failure /
    # _reset_consecutive_fails perform in production.
    assert row["id"] == _SCHED_ID
    assert row.get("task_type") == "agent_routine"
    assert row.get("user_id") == _USER_ID
    assert row.get("timezone") == "UTC"
    assert row.get("cron_expr") == "0 9 * * *"
    assert row.get("consecutive_fails") == 2
    assert row.get("fire_count") == 3
    assert row.get("next_fire_at") is not None


@pytest.mark.asyncio
async def test_entity_level_select_negative_control_proves_sensitivity():
    """Negative control: the KNOWN-BAD select(UserSchedules) form (what this
    file's row-shape bug originally shipped as), executed against the exact
    same real table/row, must produce the wrong shape — proving the test
    above actually distinguishes correct from broken, not just echoing
    whatever the production code currently does."""
    now = datetime.now(timezone.utc)
    async with _real_session_with_one_due_row(now) as session:
        bad_stmt = select(UserSchedules).execution_options(
            schema_translate_map={"public": None}
        )
        bad_rows = (await session.execute(bad_stmt)).mappings().all()

    assert len(bad_rows) == 1
    bad_row = bad_rows[0]
    assert list(bad_row.keys()) == ["UserSchedules"]  # entity-keyed, not column-keyed
    assert bad_row.get("task_type") is None  # every consumer field silently None
    with pytest.raises(KeyError):
        bad_row["id"]  # sched_id = row["id"] would raise in _dispatch_one
