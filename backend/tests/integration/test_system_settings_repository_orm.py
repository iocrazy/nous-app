"""Integration tests for SystemSettingsRepositoryOrm (admin wave) vs real PG.

Proves the REST → ORM swap is invisible AND strategy-C parity for system_settings:

  - updated_by (uuid) → STR (SystemSettingResponse.updated_by:Optional[str])
  - updated_at (tstz) → ISO STR
  - value / options (jsonb) → native dict

update() WRITES value + updated_by and COMMITS via write_scope() — a fresh
asyncpg read proves no silent rollback. The transcode_* exclusion policy is
verified. exists() existence-probe parity.

    source /tmp/orm2_integration.env
    uv run pytest tests/integration/test_system_settings_repository_orm.py -v
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
_PREFIX = "__test_orm_setting_"


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
            "DELETE FROM system_settings WHERE key LIKE $1", _PREFIX + "%"
        )
    finally:
        await conn.close()


async def _seed_setting(conn, key, value, **overrides):
    payload = {"key": key, "value": json.dumps(value)}
    payload.update(
        {k: json.dumps(v) if k in ("options",) else v for k, v in overrides.items()}
    )
    cols = list(payload.keys())
    ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
    col_list = ", ".join(f'"{c}"' for c in cols)
    await conn.execute(
        f"INSERT INTO system_settings ({col_list}) VALUES ({ph})", *payload.values()
    )


def _repo():
    from app.repositories.admin.system_settings_repository_orm import (
        SystemSettingsRepositoryOrm,
    )

    return SystemSettingsRepositoryOrm()


async def test_list_non_transcode_excludes_and_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    visible_key = f"{_PREFIX}visible_{uuid.uuid4().hex[:6]}"
    transcode_key = f"transcode_{_PREFIX}{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        await _seed_setting(conn, visible_key, {"a": 1}, updated_by=admin)
        await _seed_setting(conn, transcode_key, {"b": 2})
    finally:
        await conn.close()

    rows = await _repo().list_non_transcode()
    keys = {r["key"] for r in rows}
    assert visible_key in keys
    assert transcode_key not in keys  # transcode_* excluded

    row = next(r for r in rows if r["key"] == visible_key)
    assert row["value"] == {"a": 1}  # jsonb → native dict
    assert type(row["updated_at"]) is str  # timestamptz → ISO str
    assert datetime.fromisoformat(row["updated_at"])
    if admin is not None:
        assert type(row["updated_by"]) is str  # uuid → str
        assert row["updated_by"] == str(admin)


async def test_exists(integration_db_url, patched_engine, cleanup_test_rows):
    key = f"{_PREFIX}exists_{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        await _seed_setting(conn, key, {"x": 1})
    finally:
        await conn.close()

    assert await _repo().exists(key) is True
    assert await _repo().exists(f"{_PREFIX}nope_{uuid.uuid4().hex}") is False


async def test_update_commits_and_returns_parity(
    integration_db_url, patched_engine, cleanup_test_rows
):
    key = f"{_PREFIX}upd_{uuid.uuid4().hex[:6]}"
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
        await _seed_setting(conn, key, {"old": True})
    finally:
        await conn.close()

    out = await _repo().update(key, {"new": 99}, str(admin))
    assert out is not None
    assert out["value"] == {"new": 99}
    assert out["key"] == key
    assert type(out["updated_at"]) is str
    assert type(out["updated_by"]) is str and out["updated_by"] == str(admin)

    # Fresh asyncpg read proves the write COMMITTED (no silent rollback).
    conn = await asyncpg.connect(integration_db_url)
    try:
        persisted = await conn.fetchval(
            "SELECT value FROM system_settings WHERE key = $1", key
        )
        persisted_by = await conn.fetchval(
            "SELECT updated_by FROM system_settings WHERE key = $1", key
        )
    finally:
        await conn.close()
    assert json.loads(persisted) == {"new": 99}
    assert str(persisted_by) == str(admin)


async def test_update_missing_key_returns_none(
    integration_db_url, patched_engine, cleanup_test_rows
):
    conn = await asyncpg.connect(integration_db_url)
    try:
        admin = await conn.fetchval("SELECT id FROM auth.users LIMIT 1")
    finally:
        await conn.close()
    out = await _repo().update(
        f"{_PREFIX}ghost_{uuid.uuid4().hex}", {"x": 1}, str(admin)
    )
    assert out is None  # REST-contract parity (no row matched)


# ─── factory parity ─────────────────────────────────────────────────────


async def test_factory_off_returns_rest(monkeypatch):
    from app.core.config import settings
    from app.repositories.admin import system_settings_repository as mod

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_SYSTEM_SETTINGS", False)
    assert type(mod.get_system_settings_repository()) is mod.SystemSettingsRepository


async def test_factory_on_returns_orm(monkeypatch, integration_db_url):
    from app.core.config import settings
    from app.db import engine as db_engine
    from app.repositories.admin import system_settings_repository as mod
    from app.repositories.admin.system_settings_repository_orm import (
        SystemSettingsRepositoryOrm,
    )

    monkeypatch.setattr(settings, "USE_ORM_ADMIN_SYSTEM_SETTINGS", True)
    monkeypatch.setattr(
        db_engine.settings, "SUPAVISOR_DATABASE_URL", integration_db_url
    )
    assert isinstance(mod.get_system_settings_repository(), SystemSettingsRepositoryOrm)
