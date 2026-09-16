"""``OutputCitationsRepository`` 的三条语句在真 Postgres 上跑一遍（3c Task 16
评审补覆盖）。

WHY THIS FILE EXISTS
────────────────────
同名单测全是 ``stmt.compile()`` 字符串断言——它们证明「我们写了什么」，证明不了
「服务器接受了什么」（CLAUDE.md「读正常 ≠ 服务正常」）。具体到这三条，SQLAlchemy
编译得过而 Postgres 仍可能拒绝或**静默做错事**：

  * ``insert_many`` 一次喂多行 + ``ON CONFLICT DO NOTHING``，靠的是
    ``output_citations_message_ref_key`` 这个唯一索引真的存在且列对得上——镜像
    「重投不翻倍」的全部保证就挂在它身上。它还要 ``message_id`` 指向一条**真实
    存在**的消息（外键 CASCADE），所以夹具必须建 auth.users → teams →
    conversations → messages 的真链。
  * ``list_for_ref`` 的 ``ORDER BY created_at DESC, id DESC``：同一事务里两条
    引用的 ``now()`` **完全相等**，决胜键有没有生效只有真库说得出来。
  * ``counts_for_chain`` 的 ``GROUP BY version``：分错组的表现是血缘面板上每一版
    都显示整条链的引用数，而编译期断言 ``"GROUP BY" in sql`` 照样绿。

Transport 与 ``tests/db/test_search_docs_repository_integration.py`` 同：asyncpg
建拆夹具，repository 自己走 ``app.db.session`` 的 SQLAlchemy 引擎（``orm_dsn``
把它重指到同一个 DSN）。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_output_citations_repository_integration.py -v

``INTEGRATION_DATABASE_URL`` 没设就整体 skip——这个文件里没有一条在无库时仍然
「通过」的用例，那种用例比没有更糟。
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — output_citations integration tests "
        "need a DB."
    ),
)


@pytest.fixture
async def orm_dsn():
    """把 ORM 引擎重指到测试 DSN，用完 dispose，别让其他测试继承一个野引擎。"""
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
    conn = await asyncpg.connect(_TEST_DSN)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def fx(pg) -> Dict[str, Any]:
    """auth.users → team → conversation → **两条消息**，外加一件议题。

    两条消息是必须的：``list_for_ref`` 的顺序和 UNIQUE 的作用域都是**按消息**
    的，一条消息证不出来。``ref`` 每次随机，所以断言不受库里其他行影响。
    """
    user = await pg.fetchval("INSERT INTO auth.users DEFAULT VALUES RETURNING id")
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code, kind)"
        " VALUES ($1, $2, $3, 'personal') RETURNING id",
        f"oc-team-{uuid.uuid4().hex[:8]}",
        user,
        f"OC{uuid.uuid4().hex[:8].upper()}",
    )
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"oc-agent-{uuid.uuid4().hex[:8]}",
    )
    try:
        conv_id = await pg.fetchval(
            "INSERT INTO public.conversations (type, scope_id, created_by)"
            " VALUES ('direct_agent', $1, $2) RETURNING id",
            team_id,
            user,
        )
        msgs = [
            await pg.fetchval(
                "INSERT INTO public.messages (conversation_id, seq, sender_id)"
                " VALUES ($1, $2, $3) RETURNING id",
                conv_id,
                seq,
                user,
            )
            for seq in (1, 2)
        ]
        issue_id = await pg.fetchval(
            "INSERT INTO public.issues"
            " (issue_number, identifier, title, created_by_agent_id)"
            " VALUES ($1, $2, $3, $4) RETURNING id",
            960_001,
            f"OC-{uuid.uuid4().hex[:8].upper()}",
            "output_citations integration fixture",
            agent_id,
        )
        ref = f"oc-{uuid.uuid4().hex[:12]}"
        try:
            yield {
                "user": user,
                "conv_id": conv_id,
                "msgs": msgs,
                "issue_id": issue_id,
                "ref": ref,
            }
        finally:
            await pg.execute(
                "DELETE FROM public.output_citations WHERE ref_id = $1", ref
            )
            await pg.execute("DELETE FROM public.issues WHERE id = $1", issue_id)
            await pg.execute("DELETE FROM public.conversations WHERE id = $1", conv_id)
    finally:
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)
        await pg.execute("DELETE FROM public.teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", user)


def _repo():
    from app.repositories.output_citations_repository import (
        OutputCitationsRepository,
    )

    return OutputCitationsRepository()


def _row(fx, *, message_id, version, issue_id=None) -> Dict[str, Any]:
    return {
        "kind": "script_shot",
        "ref_id": fx["ref"],
        "version": version,
        "issue_id": issue_id,
        "conversation_id": fx["conv_id"],
        "message_id": message_id,
        "cited_by_user_id": str(fx["user"]),
    }


@_skip
async def test_a_batch_lands_and_a_repost_of_it_does_not_double_count(orm_dsn, fx, pg):
    """一条消息里两条引用 = 一次多行 INSERT；整条重投一次 = 零新行。

    重投（编辑、重发、重放）**不是错误**，所以它不该抛 —— 靠
    ``ON CONFLICT DO NOTHING`` 吃掉。而「吃掉」这件事只有唯一索引真的存在时才
    成立：索引丢了，第二次会安静地插进两条重复行，计数翻倍，没有任何探针会说。
    """
    repo, msg = _repo(), fx["msgs"][0]
    batch = [
        _row(fx, message_id=msg, version=4, issue_id=fx["issue_id"]),
        _row(fx, message_id=msg, version=1),
    ]
    await repo.insert_many(batch)
    await repo.insert_many(batch)  # 重投：不许抛，也不许长出新行
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM public.output_citations WHERE ref_id = $1", fx["ref"]
        )
        == 2
    )


@_skip
async def test_two_messages_citing_one_version_come_back_newest_first(orm_dsn, fx, pg):
    """``created_at`` 是**事务开始时刻**，所以同一事务里落的两条时间戳完全相等
    —— 决胜键 ``id DESC`` 不生效的话顺序由服务器随便定。这里刻意把两条塞进
    **同一批**，正是那个最坏情况。
    """
    repo = _repo()
    await repo.insert_many(
        [
            _row(fx, message_id=fx["msgs"][0], version=4, issue_id=fx["issue_id"]),
            _row(fx, message_id=fx["msgs"][1], version=4),
        ]
    )
    rows = await repo.list_for_ref("script_shot", fx["ref"], 4)
    assert [r["message_id"] for r in rows] == [
        str(fx["msgs"][1]),
        str(fx["msgs"][0]),
    ]
    # id 一律字符串出口（Snowflake 过 2^53 在浏览器里掉精度）。
    assert rows[0]["issue_id"] is None
    assert rows[1]["issue_id"] == str(fx["issue_id"])
    assert all(isinstance(r["message_id"], str) for r in rows)
    assert rows[0]["user_id"] == str(fx["user"]) and rows[0]["at"] is not None


@_skip
async def test_the_chain_is_counted_per_version_in_one_query(orm_dsn, fx, pg):
    """分错组的表现是血缘面板上**每一版**都显示整条链的引用数。没被引过的版本
    压根不在 dict 里 —— 调用方用 ``.get(v, 0)`` 读，缺席就是零。
    """
    repo = _repo()
    await repo.insert_many(
        [
            _row(fx, message_id=fx["msgs"][0], version=4),
            _row(fx, message_id=fx["msgs"][1], version=4),
            _row(fx, message_id=fx["msgs"][0], version=1),
        ]
    )
    assert await repo.counts_for_chain("script_shot", fx["ref"]) == {4: 2, 1: 1}
    assert await repo.counts_for_chain("script_shot", f"{fx['ref']}-nope") == {}
