"""``find_by_promoted_resource`` 打在真 Postgres 上（mig 307 + 456）。

单测里 session 是桩的，编译得过不代表服务器接受：``promoted_resource_id``
的 IS NULL 语义（NULL 不匹配任何等值）、``_normalize`` 把 BIGINT id 变成
string、以及 456 的 partial unique 到底存不存在，只有这里会说话。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_generated_media_promoted_lookup_integration.py -v

``promoted_resource_id`` **没有外键**（mig 307:30），所以本文件不需要
``resources`` 行 —— fixture 只建 ``generated_media``，NOT NULL 的列也只有
``scope_id`` / ``creator_id`` / ``media_kind`` / ``file_path`` / ``origin_kind``。
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — promoted-resource lookup needs a DB.",
)


@pytest.fixture
async def orm_dsn():
    """Repoint the ORM engine (app.db.session read/write scope) at the test DSN
    for the duration of a test, then restore + dispose so no other test inherits
    a stray engine."""
    from app.core.config import settings
    from app.db import engine as engine_mod
    from app.db import session as session_mod

    old = settings.SUPAVISOR_DATABASE_URL
    settings.SUPAVISOR_DATABASE_URL = _TEST_DSN
    await engine_mod.dispose_engine()
    session_mod.dispose_sessionmaker()
    try:
        yield _TEST_DSN
    finally:
        await engine_mod.dispose_engine()
        session_mod.dispose_sessionmaker()
        settings.SUPAVISOR_DATABASE_URL = old


@pytest.fixture
async def pg():
    """A raw asyncpg connection for setup/assertions (plain libpq DSN)."""
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def gm(pg):
    """两行 generated_media：一行已 promote 到 resource_a，一行没 promote。"""
    scope_id = 331438215859255
    creator = uuid.uuid4()
    resource_a = 900_000_000_000_000 + int(uuid.uuid4().int % 1_000_000)
    made = []

    async def _mk(promoted):
        gen_id = await pg.fetchval(
            """
            INSERT INTO public.generated_media
                (scope_id, creator_id, media_kind, file_path, origin_kind,
                 promoted_resource_id, model, provider)
            VALUES ($1,$2,'image',$3,'agent_run',$4,'gpt-6-astra','openai-images')
            RETURNING id
            """,
            scope_id,
            creator,
            f"gm/{uuid.uuid4().hex}.png",
            promoted,
        )
        made.append(gen_id)
        return gen_id

    promoted_id = await _mk(resource_a)
    loose_id = await _mk(None)
    try:
        yield {"promoted": promoted_id, "loose": loose_id, "resource_a": resource_a}
    finally:
        await pg.execute(
            "DELETE FROM public.generated_media WHERE id = ANY($1::bigint[])", made
        )


def _repo():
    from app.repositories.generated_media_repository import GeneratedMediaRepository

    return GeneratedMediaRepository()


@_skip
async def test_finds_the_inbox_row_of_a_promoted_resource(orm_dsn, gm):
    row = await _repo().find_by_promoted_resource(gm["resource_a"])
    assert row is not None
    assert row["id"] == str(gm["promoted"]), "Snowflake id 必须是 string"
    assert row["model"] == "gpt-6-astra"


@_skip
async def test_a_resource_nobody_promoted_into_is_none(orm_dsn, gm):
    assert await _repo().find_by_promoted_resource(gm["resource_a"] + 7) is None


@_skip
async def test_a_null_promoted_pointer_never_matches(orm_dsn, gm, pg):
    """NULL 不等于任何值——没有这条，一个写错成 IS NOT DISTINCT FROM 的
    谓词会把每个未 promote 的生成都认领成某个资源的来源。"""
    row = await _repo().find_by_promoted_resource(gm["resource_a"])
    assert row["id"] != str(gm["loose"])


@_skip
async def test_the_partial_unique_index_is_what_makes_one_row_the_answer(
    orm_dsn, gm, pg
):
    """mig 456 的 partial unique 存在 → 一个资源至多一行，``ORDER BY id ASC``
    只为 456 之前可能残留的一对行兜底（所以本测试断言索引，而不是造重复行）。"""
    assert (
        await pg.fetchval(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='uq_genmedia_promoted_resource'"
        )
        == 1
    )
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await pg.execute(
            "INSERT INTO public.generated_media "
            "(scope_id, creator_id, media_kind, file_path, origin_kind, promoted_resource_id) "
            "VALUES (331438215859255, gen_random_uuid(), 'image', 'x.png', 'agent_run', $1)",
            gm["resource_a"],
        )
