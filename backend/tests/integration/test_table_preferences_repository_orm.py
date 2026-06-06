"""Integration tests for AdminTablePreferencesRepositoryOrm (admin wave) vs PG.

Proves the REST → ORM swap is invisible for admin_table_preferences. The
projection (table_key, filters, sorts, visible_columns, column_order) has NO uuid
/ NO timestamptz, so the parity concern is shape (jsonb → dict/list, text[] →
list[str]) + write COMMIT semantics (upsert / delete via write_scope()).

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_table_preferences_repository_orm.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_TABLE_KEY = "__test_orm_prefs"


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


@pytest.fixture
async def cleanup_test_rows(integration_db_url):
    yield
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "DELETE FROM admin_table_preferences WHERE table_key = $1", _TABLE_KEY
        )
    finally:
        await conn.close()


async def _real_user_id(conn):
    uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    if not uid:
        pytest.skip("No auth.users rows to satisfy user_id")
    return uid


def _repo():
    from app.repositories.admin.table_preferences_repository_orm import (
        AdminTablePreferencesRepositoryOrm,
    )

    return AdminTablePreferencesRepositoryOrm()


async def test_get_missing_returns_none(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()
    assert await _repo().get(str(user_id), _TABLE_KEY) is None


async def test_upsert_then_get_shape_and_commit(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    filters = [{"field": "name", "op": "eq", "value": "x"}]
    sorts = [{"field": "created_at", "dir": "desc"}]
    out = await _repo().upsert(
        user_id=str(user_id),
        table_key=_TABLE_KEY,
        filters=filters,
        sorts=sorts,
        visible_columns=["a", "b"],
        column_order=["b", "a"],
    )
    assert out is not None
    # 5-column projection only (no id / user_id / timestamps leaked).
    assert set(out.keys()) == {
        "table_key",
        "filters",
        "sorts",
        "visible_columns",
        "column_order",
    }
    assert out["filters"] == filters  # jsonb → native list/dict
    assert out["sorts"] == sorts
    assert out["visible_columns"] == ["a", "b"]  # text[] → native list[str]
    assert out["column_order"] == ["b", "a"]

    # get reflects the committed row.
    got = await _repo().get(str(user_id), _TABLE_KEY)
    assert got is not None
    assert got["filters"] == filters
    assert got["visible_columns"] == ["a", "b"]


async def test_upsert_conflict_updates(
    integration_db_url, patched_engine, cleanup_test_rows
):
    """Second upsert on the same (user_id, table_key) UPDATES, not duplicates."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    await _repo().upsert(
        user_id=str(user_id),
        table_key=_TABLE_KEY,
        filters=[],
        sorts=[],
        visible_columns=["a"],
        column_order=None,
    )
    out2 = await _repo().upsert(
        user_id=str(user_id),
        table_key=_TABLE_KEY,
        filters=[],
        sorts=[],
        visible_columns=["a", "b", "c"],
        column_order=None,
    )
    assert out2["visible_columns"] == ["a", "b", "c"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM admin_table_preferences WHERE user_id = $1 "
            "AND table_key = $2",
            user_id,
            _TABLE_KEY,
        )
    finally:
        await conn.close()
    assert count == 1  # ON CONFLICT updated, not inserted twice


async def test_delete_commits(integration_db_url, patched_engine, cleanup_test_rows):
    conn = await asyncpg.connect(integration_db_url)
    try:
        user_id = await _real_user_id(conn)
    finally:
        await conn.close()

    await _repo().upsert(
        user_id=str(user_id),
        table_key=_TABLE_KEY,
        filters=[],
        sorts=[],
        visible_columns=None,
        column_order=None,
    )
    await _repo().delete(str(user_id), _TABLE_KEY)

    conn = await asyncpg.connect(integration_db_url)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM admin_table_preferences WHERE user_id = $1 "
            "AND table_key = $2",
            user_id,
            _TABLE_KEY,
        )
    finally:
        await conn.close()
    assert count == 0  # delete committed


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import table_preferences_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TABLE_PREFERENCES", False)
    assert (
        type(mod.get_admin_table_preferences_repository())
        is mod.AdminTablePreferencesRepository
    )


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import table_preferences_repository as mod
    from app.repositories.admin.table_preferences_repository_orm import (
        AdminTablePreferencesRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_TABLE_PREFERENCES", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(
        mod.get_admin_table_preferences_repository(),
        AdminTablePreferencesRepositoryOrm,
    )
