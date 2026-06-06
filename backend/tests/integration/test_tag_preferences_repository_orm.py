"""Integration tests for TagPreferencesRepositoryOrm (Batch L1) against real PG.

The public return shape is a fixed 3-key defaults-merged dict
(starred_tag_ids / picker_settings / panel_size) — NOT a SELECT-* dict. There
is no uuid / datetime / bigint OUTPUT field, so strategy-C is a no-op here; the
tests instead pin:

  - defaults returned when the user has no row;
  - field-level merge of picker_settings / panel_size against DEFAULTS;
  - upsert PERSISTS (write_scope COMMITS) + is idempotent (ON CONFLICT user_id);
  - native JSONB → dict / ARRAY → list at the boundary.

Setup: requires INTEGRATION_DATABASE_URL. Skips cleanly otherwise:

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_tag_preferences_repository_orm.py -v
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


@pytest.fixture
async def test_user(integration_db_url):
    """A real auth.users id (PK FK target) with its prefs cleaned up after."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        if not uid:
            pytest.skip("No auth.users rows to satisfy user_tag_preferences PK")
        # Start clean (no pre-existing prefs row for this user).
        await conn.execute("DELETE FROM user_tag_preferences WHERE user_id = $1", uid)
    finally:
        await conn.close()
    yield uid
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM user_tag_preferences WHERE user_id = $1", uid)
    finally:
        await conn.close()


def _repo():
    from app.repositories.tag_preferences_repository_orm import (
        TagPreferencesRepositoryOrm,
    )

    return TagPreferencesRepositoryOrm()


# ─── Reads ──────────────────────────────────────────────────────────────


async def test_get_preferences_defaults_when_missing(
    integration_db_url, patched_engine, test_user
):
    """No row → fresh DEFAULTS copy with the exact 3-key shape."""
    repo = _repo()
    prefs = await repo.get_preferences(str(test_user))
    assert set(prefs.keys()) == {"starred_tag_ids", "picker_settings", "panel_size"}
    assert prefs["starred_tag_ids"] == []
    assert prefs == repo.DEFAULTS
    # A fresh copy — not the shared DEFAULTS object (no aliasing).
    assert prefs is not repo.DEFAULTS


async def test_get_preferences_merges_partial_row(
    integration_db_url, patched_engine, test_user
):
    """A row with a PARTIAL picker_settings merges over DEFAULTS field-by-field;
    JSONB → dict, ARRAY → list at the boundary."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute(
            "INSERT INTO user_tag_preferences "
            "(user_id, starred_tag_ids, picker_settings, panel_size) "
            "VALUES ($1, $2, $3::jsonb, $4::jsonb)",
            test_user,
            ["t1", "t2"],
            '{"layout": "grid"}',
            '{"width": 999}',
        )
    finally:
        await conn.close()

    prefs = await _repo().get_preferences(str(test_user))
    assert type(prefs["starred_tag_ids"]) is list
    assert prefs["starred_tag_ids"] == ["t1", "t2"]
    # picker_settings merged: overridden layout, defaults retained.
    assert type(prefs["picker_settings"]) is dict
    assert prefs["picker_settings"]["layout"] == "grid"
    assert prefs["picker_settings"]["showCount"] is True  # from DEFAULTS
    # panel_size merged: overridden width, default height.
    assert prefs["panel_size"]["width"] == 999
    assert prefs["panel_size"]["height"] == 400  # from DEFAULTS


# ─── Writes (COMMIT + idempotent upsert) ────────────────────────────────


async def test_upsert_commits_and_merges(integration_db_url, patched_engine, test_user):
    """upsert PERSISTS (write_scope commits) + merges picker_settings."""
    repo = _repo()
    out = await repo.upsert_preferences(
        str(test_user),
        {"starred_tag_ids": ["a"], "picker_settings": {"layout": "grid"}},
    )
    assert out["starred_tag_ids"] == ["a"]
    assert out["picker_settings"]["layout"] == "grid"
    assert out["picker_settings"]["showCount"] is True  # default retained

    # Fresh asyncpg read proves the COMMIT.
    conn = await asyncpg.connect(integration_db_url)
    try:
        starred = await conn.fetchval(
            "SELECT starred_tag_ids FROM user_tag_preferences WHERE user_id = $1",
            test_user,
        )
    finally:
        await conn.close()
    assert starred == ["a"]


async def test_upsert_idempotent_second_write(
    integration_db_url, patched_engine, test_user
):
    """Second upsert ON CONFLICT (user_id) updates the SAME row (no dup PK)."""
    repo = _repo()
    await repo.upsert_preferences(str(test_user), {"starred_tag_ids": ["a"]})
    out2 = await repo.upsert_preferences(
        str(test_user), {"picker_settings": {"layout": "grid"}}
    )
    # picker_settings updated; starred_tag_ids preserved from the first write.
    assert out2["picker_settings"]["layout"] == "grid"
    assert out2["starred_tag_ids"] == ["a"]

    conn = await asyncpg.connect(integration_db_url)
    try:
        cnt = await conn.fetchval(
            "SELECT count(*) FROM user_tag_preferences WHERE user_id = $1", test_user
        )
    finally:
        await conn.close()
    assert cnt == 1  # one row, not two


# ─── Flag-off legacy parity ─────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories import tag_preferences_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_TAG_PREFERENCES", False)
    repo = mod.get_tag_preferences_repository()
    assert type(repo) is mod.TagPreferencesRepository
    from app.repositories.tag_preferences_repository_orm import (
        TagPreferencesRepositoryOrm,
    )

    assert not isinstance(repo, TagPreferencesRepositoryOrm)


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories import tag_preferences_repository as mod
    from app.repositories.tag_preferences_repository_orm import (
        TagPreferencesRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_TAG_PREFERENCES", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    repo = mod.get_tag_preferences_repository()
    assert isinstance(repo, TagPreferencesRepositoryOrm)
