"""472 在真 Postgres 上的样子。上一个文件读的是我们写了什么；这里读的是服务器
接受了什么（CLAUDE.md「读正常 ≠ 服务正常」）。

    INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
      uv run pytest tests/db/test_mig_472_integration.py -v
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
    not _TEST_DSN, reason="INTEGRATION_DATABASE_URL not set — mig 472 needs a DB."
)


@pytest.fixture
async def pg():
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


async def _insert_run_doc(pg, entity_id: str) -> int:
    return await pg.fetchval(
        "INSERT INTO public.search_docs (entity_kind, entity_id, title, body)"
        " VALUES ('run', $1, 'MH-1 · demo', 'a shot about lanterns') RETURNING id",
        entity_id,
    )


@_skip
async def test_the_trigram_indexes_exist_with_their_predicates(pg):
    """pg_trgm 没装时这两个索引压根建不出来 —— 而文本测试会照样绿。"""
    defs = {
        r["indexname"]: r["indexdef"]
        for r in await pg.fetch(
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE schemaname='public' AND tablename='search_docs'"
        )
    }
    assert "gin_trgm_ops" in defs["idx_search_docs_title_trgm"]
    assert "gin_trgm_ops" in defs["idx_search_docs_body_trgm"]
    assert "WHERE (body IS NOT NULL)" in defs["idx_search_docs_body_trgm"]
    assert "WHERE (project_id IS NOT NULL)" in defs["idx_search_docs_project"]


@_skip
async def test_one_entity_gets_exactly_one_row_and_a_third_kind_is_refused(pg):
    """投影表的全部幂等性靠 UNIQUE —— upsert 与回填拼同一个 entity_id 时第二次
    必须撞上它。而 'issue' 不进投影表（issues 走自己的三个 trgm 索引），CHECK 是
    那条设计决定在库里的唯一表达。"""
    ent = f"mig472-{uuid.uuid4().hex[:12]}"
    row_id = await _insert_run_doc(pg, ent)
    try:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError) as err:
            await _insert_run_doc(pg, ent)
        assert "search_docs_entity_key" in str(err.value)
        with pytest.raises(asyncpg.exceptions.CheckViolationError) as err2:
            await pg.execute(
                "INSERT INTO public.search_docs (entity_kind, entity_id, title)"
                " VALUES ('issue', $1, 'x')",
                f"bad-{uuid.uuid4().hex[:8]}",
            )
        assert "search_docs_entity_kind_check" in str(err2.value)
    finally:
        await pg.execute("DELETE FROM public.search_docs WHERE id=$1", row_id)


@_skip
async def test_deleting_the_run_takes_its_projection_with_it(pg):
    """投影是镜像，不是独立事实。run 删了而 search_docs 行还在 = 搜索结果里一个
    指向不存在行的幽灵 —— 点进去 404，而没有任何探针会说出来。这条只有真库能
    答：外键与 ON DELETE 是服务器的行为，文本测试读的是我们写了什么。

    顺带也是「回填 INSERT 不会违约」的正向对照：run_id 来自 agent_runs 本身。
    """
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"mig472-agent-{uuid.uuid4().hex[:8]}",
    )
    run_id = await pg.fetchval(
        "INSERT INTO public.agent_runs (agent_id, user_id, status, trigger)"
        " VALUES ($1, $2, 'completed', 'test') RETURNING id",
        agent_id,
        uuid.uuid4(),
    )
    try:
        doc_id = await pg.fetchval(
            "INSERT INTO public.search_docs (entity_kind, entity_id, title, run_id)"
            " VALUES ('run', $1, 'MH-1 · demo', $2) RETURNING id",
            str(run_id),
            run_id,
        )
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM public.search_docs WHERE id=$1", doc_id
            )
            == 1
        )
        await pg.execute("DELETE FROM public.agent_runs WHERE id=$1", run_id)
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM public.search_docs WHERE id=$1", doc_id
            )
            == 0
        ), "run 没了，讲它的那条投影还在 —— 外键或 ON DELETE CASCADE 丢了"
    finally:
        await pg.execute("DELETE FROM public.agent_runs WHERE id=$1", run_id)
        await pg.execute("DELETE FROM public.ai_agents WHERE id=$1", agent_id)


@_skip
async def test_the_same_version_cannot_be_cited_twice_by_one_message(pg):
    """顺带证明 message_id 的外键是真的：这条引用必须挂在一条**存在的**消息上，
    而那条消息删掉时引用要跟着走（镜像不留幽灵）。"""
    # auth.users → team → conversation → message：每一层的外键都是真的，同
    # test_revert_output_integration.py 的做法。
    user = await pg.fetchval("INSERT INTO auth.users DEFAULT VALUES RETURNING id")
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code, kind)"
        " VALUES ($1, $2, $3, 'personal') RETURNING id",
        f"mig472-team-{uuid.uuid4().hex[:8]}",
        user,
        f"M472{uuid.uuid4().hex[:8].upper()}",
    )
    try:
        conv_id = await pg.fetchval(
            "INSERT INTO public.conversations (type, scope_id, created_by)"
            " VALUES ('direct_agent', $1, $2) RETURNING id",
            team_id,
            user,
        )
        msg = await pg.fetchval(
            "INSERT INTO public.messages (conversation_id, seq, sender_id)"
            " VALUES ($1, 1, $2) RETURNING id",
            conv_id,
            user,
        )
        ref = f"mig472-{uuid.uuid4().hex[:12]}"
        ins = (
            "INSERT INTO public.output_citations (kind, ref_id, version, message_id,"
            " cited_by_user_id) VALUES ('script_shot', $1, 2, $2, $3)"
        )
        cite_id = await pg.fetchval(ins + " RETURNING id", ref, msg, user)

        with pytest.raises(asyncpg.exceptions.UniqueViolationError) as err:
            await pg.execute(ins, ref, msg, user)
        assert "output_citations_message_ref_key" in str(err.value)

        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError) as fk:
            await pg.execute(ins, ref, 472_000_000_000_001, user)
        assert "output_citations_message_id_fkey" in str(fk.value)

        await pg.execute("DELETE FROM public.conversations WHERE id=$1", conv_id)
        assert (
            await pg.fetchval(
                "SELECT count(*) FROM public.output_citations WHERE id=$1", cite_id
            )
            == 0
        ), "消息没了，指着它的引用还在 —— CASCADE 丢了"
    finally:
        await pg.execute("DELETE FROM public.teams WHERE id=$1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id=$1", user)


@_skip
async def test_the_counter_columns_have_the_nullability_each_side_needs(pg):
    """小时表五列必须 NOT NULL DEFAULT 0（累加器可空 → 加法把整行算成 NULL）；
    agent_runs 五列必须可空（「没在数」不能被 0 伪装成「没调过」）。"""
    names = ["run_count", "failed_runs", "tool_calls", "tool_errors", "deliverables"]
    hourly = await pg.fetch(
        "SELECT column_name, is_nullable, column_default FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='ai_usage_hourly'"
        "   AND column_name = ANY($1::text[])",
        names,
    )
    assert len(hourly) == 5
    for r in hourly:
        assert r["is_nullable"] == "NO" and "0" in (r["column_default"] or ""), r[
            "column_name"
        ]
    runs = await pg.fetch(
        "SELECT column_name, is_nullable FROM information_schema.columns"
        " WHERE table_schema='public' AND table_name='agent_runs'"
        "   AND column_name = ANY($1::text[])",
        ["steps", "tool_calls", "tool_errors", "deliverables", "turn_end_reason"],
    )
    assert len(runs) == 5 and all(r["is_nullable"] == "YES" for r in runs)


@_skip
async def test_both_new_tables_have_rls_on_and_no_browser_policy(pg):
    """anon key 是烤进浏览器包的公开值。多一条给 anon/authenticated 的策略就是
    一个公开读口（CLAUDE.md 2026-09-11）。"""
    for tbl in ("search_docs", "output_citations"):
        assert (
            await pg.fetchval(
                "SELECT relrowsecurity FROM pg_class WHERE relname=$1", tbl
            )
            is True
        ), tbl
        joined = " ".join(
            r["r"]
            for r in await pg.fetch(
                "SELECT roles::text AS r FROM pg_policies"
                " WHERE schemaname='public' AND tablename=$1",
                tbl,
            )
        )
        assert "anon" not in joined and "authenticated" not in joined, tbl
