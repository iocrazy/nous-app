"""Integration tests for the UserSettingsRepository ORM path (Task 5.4).

When ``USE_ORM_USER_SETTINGS`` is on (and the SQLAlchemy engine is
configured), ``UserSettingsRepository`` routes its reads through
``read_scope()`` + ``select(UserSettings)`` and the canonical
``settings_json`` merge through ``write_scope()`` (the COMMITTING session)
running the SAME ``COALESCE(existing,'{}'::jsonb) || CAST(:patch AS jsonb)``
ON CONFLICT statement that the ``db_engine.execute_returning_one`` Core path
ran. These tests run real SQL against a real Postgres to prove:

  - **THE #1 INVARIANT — MERGE-NOT-REPLACE** (the #485 / ``user_settings_json_clobber``
    P0): saving a General key must NOT wipe ``ai_settings`` / other top-level
    keys sitting in the same shared ``settings_json`` blob. A replace would
    drop them — the ``test_*merge*`` tests catch it.
  - **COMMIT BOUNDARY**: a ``patch_settings_json`` write is visible to a
    FRESH asyncpg connection (the write_scope() session actually COMMITS —
    the old ``execute_returning_one`` already committed; the ORM swap must
    keep committing).
  - **PLAIN-vs-JSON ISOLATION**: a ``download_path`` upsert must NOT disturb
    ``settings_json``; a ``settings_json`` patch must NOT wipe ``download_path``.
  - **jsonb → dict**: the ORM read + merge return a Python ``dict`` for
    ``settings_json`` (not a JSON str), matching the PostgREST shape.
  - **VALUE-TYPE PARITY**: ``user_id`` / ``id`` come back as ``str`` and
    ``created_at`` / ``updated_at`` as ISO ``str`` — the REST baseline the
    type-sensitive consumer (``user_settings_router`` → Pydantic ``str``
    fields) was built against. Native uuid/datetime would 500 the response.

Setup: requires INTEGRATION_DATABASE_URL set to a PG with the mediahub schema.
Skips otherwise. Use the dev stack:

    source /tmp/orm2_integration.env  # sets INTEGRATION_DATABASE_URL
    uv run pytest tests/integration/test_user_settings_repository_orm.py -v
"""

from __future__ import annotations

import os

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()


# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    if not _TEST_DSN:
        pytest.skip("INTEGRATION_DATABASE_URL not set — skipping integration tests")
    return _TEST_DSN


@pytest.fixture
async def patched_engine(integration_db_url):
    """Point the SQLAlchemy engine + sessionmaker at the test DSN so
    read_scope()/write_scope() hit the test DB, and turn the flag ON.
    Resets both singletons + the cache."""
    from unittest.mock import patch

    from app.core.cache import user_settings_cache
    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with (
        patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url),
        patch.object(db_engine.settings, "USE_ORM_USER_SETTINGS", True),
    ):
        # The repo reads settings via app.core.config.settings; patch there too.
        from app.core import config as app_config

        with patch.object(app_config.settings, "USE_ORM_USER_SETTINGS", True):
            user_settings_cache.clear()
            yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


@pytest.fixture
async def test_user_id(integration_db_url):
    """Grab a real auth user (FK target) and ensure NO user_settings row
    exists for it before/after the test (UNIQUE on user_id)."""
    conn = await asyncpg.connect(integration_db_url)
    try:
        uid = await conn.fetchval(
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE conname='user_settings_user_id_fkey'"
        )
        user_id = await conn.fetchval(f"SELECT id FROM {uid} LIMIT 1")
        if not user_id:
            pytest.skip("No users rows to satisfy the user_settings FK")
        await conn.execute("DELETE FROM user_settings WHERE user_id = $1", user_id)
    finally:
        await conn.close()
    yield user_id
    conn = await asyncpg.connect(integration_db_url)
    try:
        await conn.execute("DELETE FROM user_settings WHERE user_id = $1", user_id)
    finally:
        await conn.close()


def _repo():
    from app.repositories.user_settings_repository import UserSettingsRepository

    return UserSettingsRepository()


async def _read_settings_json_fresh(dsn, user_id) -> dict | None:
    """Read settings_json from a SEPARATE asyncpg connection (commit proof)."""
    conn = await asyncpg.connect(dsn)
    try:
        import json

        raw = await conn.fetchval(
            "SELECT settings_json FROM user_settings WHERE user_id = $1", user_id
        )
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    finally:
        await conn.close()


# ─── #1 INVARIANT: MERGE-NOT-REPLACE (the #485 clobber regression) ──────


async def test_patch_preserves_other_top_level_keys(
    integration_db_url, patched_engine, test_user_id
):
    """Seed {ai_settings, parse_mode}; patch {general}; ALL survive.

    A replace (not a merge) would drop ai_settings + parse_mode — the
    2026-06-02 data-loss P0. The ``||`` merge under ON CONFLICT must keep them."""
    repo = _repo()
    await repo.patch_settings_json(
        test_user_id,
        {"ai_settings": {"provider": "x", "key": "secret"}, "parse_mode": "a"},
    )

    result = await repo.patch_settings_json(
        test_user_id, {"general": {"theme": "dark"}}
    )

    sj = result["settings_json"]
    assert sj["ai_settings"]["key"] == "secret", "ai_settings clobbered (REPLACE bug!)"
    assert sj["parse_mode"] == "a", "parse_mode clobbered"
    assert sj["general"]["theme"] == "dark", "new general key not merged in"


async def test_patch_ai_settings_does_not_wipe_parse_mode(
    integration_db_url, patched_engine, test_user_id
):
    """The reverse: patching ai_settings must not wipe a sibling parse_mode."""
    repo = _repo()
    await repo.patch_settings_json(test_user_id, {"parse_mode": "a"})

    result = await repo.patch_settings_json(
        test_user_id, {"ai_settings": {"provider": "y"}}
    )

    sj = result["settings_json"]
    assert sj["parse_mode"] == "a", "parse_mode wiped by ai_settings patch"
    assert sj["ai_settings"]["provider"] == "y"


async def test_sequential_patches_both_survive(
    integration_db_url, patched_engine, test_user_id
):
    """Last-write-MERGE, not last-write-replace: both patches accumulate."""
    repo = _repo()
    await repo.patch_settings_json(test_user_id, {"k1": "v1"})
    await repo.patch_settings_json(test_user_id, {"k2": "v2"})

    settings = await repo.get_by_user_id(test_user_id)
    sj = settings["settings_json"]
    assert sj["k1"] == "v1"
    assert sj["k2"] == "v2"


# ─── COMMIT BOUNDARY ────────────────────────────────────────────────────


async def test_patch_commits_visible_to_fresh_connection(
    integration_db_url, patched_engine, test_user_id
):
    """patch_settings_json → a SEPARATE asyncpg connection sees the merged
    value. Proves write_scope() actually COMMITs (the P0 the asyncpg path
    silently lacked)."""
    repo = _repo()
    await repo.patch_settings_json(test_user_id, {"committed": True})

    fresh = await _read_settings_json_fresh(integration_db_url, test_user_id)
    assert fresh is not None, "row not committed — fresh connection sees nothing"
    assert fresh["committed"] is True


# ─── PLAIN-vs-JSON ISOLATION ────────────────────────────────────────────


async def test_upsert_download_path_does_not_change_settings_json(
    integration_db_url, patched_engine, test_user_id
):
    """A download_path write must NOT disturb settings_json."""
    repo = _repo()
    await repo.patch_settings_json(test_user_id, {"ai_settings": {"key": "keep"}})

    await repo.upsert(test_user_id, {"download_path": "/x"})

    settings = await repo.get_by_user_id(test_user_id)
    assert settings["download_path"] == "/x"
    assert settings["settings_json"]["ai_settings"]["key"] == "keep"


async def test_patch_settings_json_does_not_change_download_path(
    integration_db_url, patched_engine, test_user_id
):
    """A settings_json patch must NOT wipe download_path."""
    repo = _repo()
    await repo.upsert(test_user_id, {"download_path": "/keepme"})

    await repo.patch_settings_json(test_user_id, {"parse_mode": "drissionpage"})

    settings = await repo.get_by_user_id(test_user_id)
    assert settings["download_path"] == "/keepme"
    assert settings["settings_json"]["parse_mode"] == "drissionpage"


# ─── jsonb → dict + VALUE-TYPE PARITY ───────────────────────────────────


async def test_settings_json_is_dict_not_str(
    integration_db_url, patched_engine, test_user_id
):
    """ORM read + merge return a Python dict for settings_json, not a JSON str."""
    repo = _repo()
    merged = await repo.patch_settings_json(test_user_id, {"a": 1})
    assert type(merged["settings_json"]) is dict

    read = await repo.get_by_user_id(test_user_id)
    assert type(read["settings_json"]) is dict


async def test_value_type_parity_matches_rest(
    integration_db_url, patched_engine, test_user_id
):
    """user_id / id come back as str; timestamps as ISO str — the REST shape
    the Pydantic-str consumer (user_settings_router) needs. Native uuid /
    datetime would 500 the GET/PUT /settings response."""
    repo = _repo()
    await repo.patch_settings_json(test_user_id, {"a": 1})

    settings = await repo.get_by_user_id(test_user_id)
    assert type(settings["user_id"]) is str
    assert settings["user_id"] == str(test_user_id)
    assert type(settings["id"]) is str
    assert type(settings["created_at"]) is str
    assert type(settings["updated_at"]) is str
    # The merge-path return dict must share the same parity.
    merged = await repo.patch_settings_json(test_user_id, {"b": 2})
    assert type(merged["user_id"]) is str
    assert type(merged["created_at"]) is str
