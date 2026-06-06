"""Integration tests for SessionMemoryRepositoryOrm (Phase 2 M batch) vs real PG.

Proves the REST → ORM swap is invisible on ai_session_memory:

  - load / upsert / delete round-trip a SessionMemoryRow dataclass identically.
  - session_id (BIGINT) is int-coerced for binds (the asyncpg int8 strictness)
    but str()'d back on the way out by the inherited _row_to_obj.
  - upsert ON CONFLICT (session_id) DO UPDATE bumps version (load-then-+1).
  - last_updated_at survives as a datetime (parsed by _parse_ts).

Writes go through ``write_scope()`` (COMMITS) — a fresh asyncpg read proves no
silent rollback. session_id requires a parent ai_sessions row (FK), seeded +
cleaned up by id.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_session_memory_repository_orm.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy ai_sessions.user_id")
    return uid


@pytest.fixture
async def seeded_session(integration_db_url):
    """Create an ai_sessions row (FK target) and clean it up by id (CASCADE
    drops the ai_session_memory child)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
        session_id = await conn.fetchval(
            "INSERT INTO ai_sessions (user_id) VALUES ($1) RETURNING id", user_id
        )
    finally:
        await conn.close()
    yield session_id
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM ai_sessions WHERE id = $1", session_id)
    finally:
        await conn.close()


def _repo():
    from app.repositories.session_memory_repository_orm import (
        SessionMemoryRepositoryOrm,
    )

    return SessionMemoryRepositoryOrm()


# ─── load / upsert / delete round-trip ──────────────────────────────────


async def test_load_missing_returns_none(
    integration_db_url, patched_engine, seeded_session
):
    """No memory row yet → load returns None."""
    out = await _repo().load(str(seeded_session))
    assert out is None


async def test_upsert_insert_then_load_commit(
    integration_db_url, patched_engine, seeded_session
):
    """First upsert inserts (version 1) and COMMITS; load reads it back."""
    out = await _repo().upsert(
        str(seeded_session),
        body_md="# Notes\nhello",
        sections_json={"title": "Notes"},
        tokens_at_update=1234,
        tool_calls_at_update=3,
        turns_at_update=5,
    )
    assert out is not None
    assert out.version == 1
    # session_id str()'d back out (bigint → str by _row_to_obj).
    assert out.session_id == str(seeded_session)
    assert out.body_md == "# Notes\nhello"
    assert out.sections_json == {"title": "Notes"}
    assert out.tokens_at_last_update == 1234
    assert out.last_updated_at is not None

    # Persisted (no silent rollback) — fresh asyncpg read.
    conn = await asyncpg.connect(integration_db_url)
    try:
        ver = await conn.fetchval(
            "SELECT version FROM ai_session_memory WHERE session_id = $1",
            seeded_session,
        )
    finally:
        await conn.close()
    assert ver == 1

    loaded = await _repo().load(str(seeded_session))
    assert loaded is not None
    assert loaded.version == 1
    assert loaded.body_md == "# Notes\nhello"


async def test_upsert_bumps_version_on_conflict(
    integration_db_url, patched_engine, seeded_session
):
    """Second upsert on the same session_id → ON CONFLICT DO UPDATE, version 2."""
    await _repo().upsert(
        str(seeded_session),
        body_md="v1",
        sections_json={},
        tokens_at_update=10,
        tool_calls_at_update=1,
        turns_at_update=1,
    )
    out2 = await _repo().upsert(
        str(seeded_session),
        body_md="v2",
        sections_json={"k": "v"},
        tokens_at_update=20,
        tool_calls_at_update=2,
        turns_at_update=2,
    )
    assert out2 is not None
    assert out2.version == 2
    assert out2.body_md == "v2"


async def test_upsert_no_bump_keeps_version_one(
    integration_db_url, patched_engine, seeded_session
):
    """bump_version=False keeps version at 1 even on re-upsert."""
    await _repo().upsert(
        str(seeded_session),
        body_md="a",
        sections_json={},
        tokens_at_update=0,
        tool_calls_at_update=0,
        turns_at_update=0,
    )
    out = await _repo().upsert(
        str(seeded_session),
        body_md="b",
        sections_json={},
        tokens_at_update=0,
        tool_calls_at_update=0,
        turns_at_update=0,
        bump_version=False,
    )
    assert out is not None
    assert out.version == 1


async def test_delete_commit(integration_db_url, patched_engine, seeded_session):
    """delete removes the row (returns True) and COMMITS."""
    await _repo().upsert(
        str(seeded_session),
        body_md="x",
        sections_json={},
        tokens_at_update=0,
        tool_calls_at_update=0,
        turns_at_update=0,
    )
    assert await _repo().delete(str(seeded_session)) is True

    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM ai_session_memory WHERE session_id = $1",
            seeded_session,
        )
    finally:
        await conn.close()
    assert cnt == 0

    # Deleting a non-existent row → False.
    assert await _repo().delete(str(seeded_session)) is False


# ─── Factory flag wiring ────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import session_memory_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_SESSION_MEMORY", False)
    repo = mod.get_session_memory_repository()
    assert type(repo) is mod.SessionMemoryRepository
    from app.repositories.session_memory_repository_orm import (
        SessionMemoryRepositoryOrm,
    )

    assert not isinstance(repo, SessionMemoryRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import session_memory_repository as mod
    from app.repositories.session_memory_repository_orm import (
        SessionMemoryRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_SESSION_MEMORY", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_session_memory_repository()
    assert isinstance(repo, SessionMemoryRepositoryOrm)
