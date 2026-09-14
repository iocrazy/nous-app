"""466 在真 Postgres 上的样子。文本测试读的是我们写了什么，这里读的是服务器接受了
什么——不同的问题（CLAUDE.md「读正常 ≠ 服务正常」）。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_migration_466_revert_cost_integration.py -v

script_shot_ops 的正例要 script_projects→scenes→shots 整条 FK 链，代价远大于它能
回答的问题，所以那张表只做负例：CHECK 在 ExecConstraints 阶段求值、FK 是语句末尾的
AFTER 触发器，所以违反 CHECK 的插入先拿 CheckViolation；CHECK 若不存在，同一句拿
ForeignKeyViolation —— 两种结果都不是 pass，测试仍可证伪。"""

from __future__ import annotations

import decimal
import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()
# 没有 importorskip：asyncpg 是硬依赖，上面的顶层 import 已经决定了一切——
# 缺它就是 collection error，而那正是想要的（悄悄跳过等于假绿）。
_skip = pytest.mark.skipif(
    not _TEST_DSN, reason="INTEGRATION_DATABASE_URL not set — mig 466 needs a DB."
)


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@_skip
async def test_a_human_registration_needs_no_run(pg):
    """回退行：run_id NULL + actor_user_id 有值。整条回退路径靠它，且不需要 FK。"""
    ref = f"mig466-{uuid.uuid4().hex[:12]}"
    row_id = await pg.fetchval(
        "INSERT INTO public.run_deliverables (run_id, kind, ref_id, version,"
        " actor_user_id, reverted_from_version, ledger_ref)"
        " VALUES (NULL, 'script_shot', $1, 1, $2, 3, '9001') RETURNING id",
        ref,
        uuid.uuid4(),
    )
    try:
        got = await pg.fetchrow(
            "SELECT run_id, reverted_from_version, ledger_ref"
            " FROM public.run_deliverables WHERE id = $1",
            row_id,
        )
        assert got["run_id"] is None
        assert got["reverted_from_version"] == 3 and got["ledger_ref"] == "9001"
    finally:
        await pg.execute("DELETE FROM public.run_deliverables WHERE id = $1", row_id)


@_skip
async def test_neither_a_run_nor_an_actor_is_refused(pg):
    """「既无 run 也无人」的行不许存在——否则版本号可被任何路径白占。"""
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as err:
        await pg.execute(
            "INSERT INTO public.run_deliverables (run_id, kind, ref_id, version)"
            " VALUES (NULL, 'script_shot', $1, 1)",
            f"bad-{uuid.uuid4().hex[:8]}",
        )
    assert "run_deliverables_run_or_actor" in str(err.value)


@_skip
async def test_shot_ops_refuses_a_row_with_neither_run_nor_actor(pg):
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as err:
        await pg.execute(
            "INSERT INTO public.script_shot_ops (run_id, shot_id, scene_id, action,"
            " after_json, actor) VALUES (NULL, 1, 1, 'update', '{}'::jsonb, NULL)"
        )
    assert "script_shot_ops_run_or_actor" in str(err.value)


@_skip
async def test_per_call_cents_keeps_four_decimals(pg):
    """按次价常是 12.0000，但 0.0125 这种不能被截成 0.01。"""
    model = f"mig466-{uuid.uuid4().hex[:8]}"
    await pg.execute(
        "INSERT INTO public.ai_model_prices (model, provider, prompt_cents_per_1k,"
        " completion_cents_per_1k, per_call_cents) VALUES ($1,'mig466',0,0,0.0125)",
        model,
    )
    try:
        got = await pg.fetchval(
            "SELECT per_call_cents FROM public.ai_model_prices WHERE model=$1", model
        )
        assert got == decimal.Decimal("0.0125")
    finally:
        await pg.execute("DELETE FROM public.ai_model_prices WHERE model=$1", model)


@_skip
async def test_the_promoted_resource_reverse_lookup_is_already_indexed(pg):
    """3b 的资源反查（spec §2.4）不建新索引，因为已经有一个在。删掉它反查就退化成
    全表扫，而没有别的测试会说出来。

    ⚠️ 计划书写的是「307 与 456 各建一个索引、两个都在」——真跑一遍才知道那是错的：
    456 第 13 行 `DROP INDEX IF EXISTS public.idx_genmedia_promoted`，它的 partial
    UNIQUE 是**替换**而不是并存（该迁移注释原话 "is subsumed and dropped"，ORM
    generated_media.py:61-68 也是这么写的）。照计划书原样断言两个都在，这一步在 CI
    里会永远红——一个不可能为真的探针。所以正例只钉活着的那个，并把已被删掉的那个
    钉成**不在**：这样将来谁把它加回来（等于恢复一份冗余写放大）也会被说出来。"""
    names = {
        r["indexname"]
        for r in await pg.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
            " AND tablename='generated_media'"
        )
    }
    assert "uq_genmedia_promoted_resource" in names
    assert "idx_genmedia_promoted" not in names, "456 dropped it; do not resurrect"
