"""系统文件夹的 ensure/adopt 逻辑 —— 对真 Postgres（migration 441）。

这些是数据库性质，替身证明不了：
- 用户已有的顶层「封面」文件夹被**认领**（打上 system_key），不是旁边再建一个。
- 没有可认领的就建默认文件夹；再调一次拿到同一个，不重复建。
- 嵌套在别处的同名文件夹不算模板库（只认顶层）。
- 部分唯一索引 ux_folders_scope_system_key 拦住第二个活着的同 key 文件夹。
- 回收站里的那个不占坑：认领/新建看的是活着的。

运行：INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55491/drift
      uv run pytest tests/integration/test_cover_template_folder_db.py -v
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DSN = os.environ.get("INTEGRATION_DATABASE_URL")
if not DSN:
    pytest.skip("INTEGRATION_DATABASE_URL not set", allow_module_level=True)

from app.repositories.cover_templates_repository import (  # noqa: E402
    COVER_TEMPLATE_SYSTEM_KEY,
    DEFAULT_FOLDER_NAME,
    CoverTemplatesRepository,
)

USER = uuid.UUID("b2180063-6860-4f97-9785-ad4eede16064")


async def _conn():
    return await asyncpg.connect(DSN)


@pytest.fixture
async def scope_id():
    """folders.scope_id 是到 teams 的真 FK，所以先造一个 team 当 scope。"""
    c = await _conn()
    try:
        await c.execute(
            "INSERT INTO auth.users (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            USER,
            "cover-folder-test@nous.test",
        )
        sid = await c.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) "
            "VALUES ('cover-folder-test', $1, $2) RETURNING id",
            USER,
            uuid.uuid4().hex[:12],
        )
        yield int(sid)
        await c.execute("DELETE FROM folders WHERE scope_id=$1", sid)
        await c.execute("DELETE FROM teams WHERE id=$1", sid)
    finally:
        await c.close()


@pytest.fixture(autouse=True)
async def _engine():
    """把 SQLAlchemy 引擎指到一次性库（同 test_style_template_repository_orm 的
    patched_engine）。"""
    from unittest.mock import patch

    from app.db import engine as db_engine
    from app.db import session as db_session

    db_engine._engine = None
    db_session.dispose_sessionmaker()
    with patch.object(db_engine.settings, "SUPAVISOR_DATABASE_URL", DSN):
        yield
    await db_engine.dispose_engine()
    db_engine._engine = None
    db_session.dispose_sessionmaker()


async def test_adopts_the_users_existing_top_level_cover_folder(scope_id):
    c = await _conn()
    try:
        existing = await c.fetchval(
            "INSERT INTO folders (name, scope_id, created_by) VALUES ('封面', $1, $2) RETURNING id",
            scope_id,
            USER,
        )
    finally:
        await c.close()

    got = await CoverTemplatesRepository().ensure_folder(scope_id, str(USER))

    assert got["adopted"] is True
    assert got["id"] == str(existing), "该认领已有的，不是旁边新建"
    c = await _conn()
    try:
        row = await c.fetchrow(
            "SELECT is_system, system_key FROM folders WHERE id=$1", existing
        )
        assert (
            row["is_system"] is True and row["system_key"] == COVER_TEMPLATE_SYSTEM_KEY
        )
        n = await c.fetchval(
            "SELECT count(*) FROM folders WHERE scope_id=$1 AND is_trashed=false",
            scope_id,
        )
        assert n == 1, "不该多出第二个文件夹"
    finally:
        await c.close()


async def test_creates_a_default_folder_when_nothing_is_adoptable_and_is_idempotent(
    scope_id,
):
    repo = CoverTemplatesRepository()
    first = await repo.ensure_folder(scope_id, str(USER))
    second = await repo.ensure_folder(scope_id, str(USER))

    assert first["adopted"] is False
    assert first["name"] == DEFAULT_FOLDER_NAME
    assert second["id"] == first["id"], "第二次必须拿到同一个"


async def test_a_nested_folder_named_cover_is_not_adopted(scope_id):
    """只认领顶层：某个项目下嵌套的「封面」不是模板库。"""
    c = await _conn()
    try:
        parent = await c.fetchval(
            "INSERT INTO folders (name, scope_id, created_by) VALUES ('project', $1, $2) RETURNING id",
            scope_id,
            USER,
        )
        await c.execute(
            "INSERT INTO folders (name, scope_id, created_by, parent_id) VALUES ('封面', $1, $2, $3)",
            scope_id,
            USER,
            parent,
        )
    finally:
        await c.close()

    got = await CoverTemplatesRepository().ensure_folder(scope_id, str(USER))

    assert got["adopted"] is False and got["name"] == DEFAULT_FOLDER_NAME


async def test_the_unique_index_refuses_a_second_live_system_folder(scope_id):
    await CoverTemplatesRepository().ensure_folder(scope_id, str(USER))
    c = await _conn()
    try:
        with pytest.raises(asyncpg.UniqueViolationError):
            await c.execute(
                "INSERT INTO folders (name, scope_id, created_by, is_system, system_key) "
                "VALUES ('dup', $1, $2, true, $3)",
                scope_id,
                USER,
                COVER_TEMPLATE_SYSTEM_KEY,
            )
    finally:
        await c.close()


async def test_a_trashed_system_folder_does_not_block_a_fresh_one(scope_id):
    repo = CoverTemplatesRepository()
    first = await repo.ensure_folder(scope_id, str(USER))
    c = await _conn()
    try:
        await c.execute(
            "UPDATE folders SET is_trashed=true WHERE id=$1", int(first["id"])
        )
    finally:
        await c.close()

    again = await repo.ensure_folder(scope_id, str(USER))

    assert again["id"] != first["id"], "回收站里的那个不该再被当成模板库"
