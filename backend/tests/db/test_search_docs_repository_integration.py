"""``SearchDocsRepository`` 的语句在真 Postgres 上跑一遍（3c Task 13 评审 Important 3）。

WHY THIS FILE EXISTS
────────────────────
同文件的单测全是 ``stmt.compile()`` 字符串断言 —— 它们证明「我们写了什么」，
证明不了「服务器接受了什么」（CLAUDE.md「读正常 ≠ 服务正常」）。这个仓库里
每一条被编译过却没被执行过的语句都是同一类缺口。具体到这四条，SQLAlchemy 编
译得过而 Postgres 仍可能拒绝或**静默做错事**：

  * ``func.similarity(...)`` 要 ``pg_trgm`` 真的装着。没装时函数不存在，而
    编译期断言 ``"greatest(similarity" in sql`` 照样绿。
  * ``on_conflict_do_update(index_elements=[entity_kind, entity_id])`` 要
    ``search_docs_entity_key`` 这个唯一索引真的存在且列对得上 —— 投影「可重
    建、绝不长出第二行」的全部保证就挂在它身上。
  * ``owner_user_id`` / ``team_members.user_id`` 两列在 ORM 上是 ``Uuid``，一次
    查询里同一个绑定值要同时喂给这两列。类型绑错的表现是
    ``operator does not exist: uuid = character varying``，只有服务器说得出来。
  * 用户搜一个字面 ``%`` 时它必须匹配它自己，而不是匹配全表。**哪一半在干活
    只有真库说得清**：本文件的突变实测（见 task-13 报告）——拿掉 Python 侧的
    ``escape_like`` 立刻转红，拿掉 SQL 侧的 ``ESCAPE '\\'`` **不会**，因为
    Postgres 的 LIKE 本来就默认以反斜杠为转义符（``SELECT 'abc' LIKE '%\\%%'``
    在 pg17 上是 false）。ESCAPE 子句留着是把这个默认钉死成显式声明，不是机制
    本身。⚠️ CLAUDE.md 那句「Postgres 的 LIKE 没有默认转义符，光在 Python 侧
    转义是无效的」与实测不符 —— 结论（两侧都写）没错，理由错了。

Transport 与 ``tests/db/test_assets_repository_integration.py`` 同：asyncpg 建
拆夹具，repository 自己走 ``app.db.session`` 的 SQLAlchemy 引擎（``orm_dsn``
把它重指到同一个 DSN）。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_search_docs_repository_integration.py -v

``INTEGRATION_DATABASE_URL`` 没设就整体 skip —— 这个文件里没有一条在无库时
仍然「通过」的用例，那种用例比没有更糟。
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
    reason="INTEGRATION_DATABASE_URL not set — search_docs integration tests need a DB.",
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
    """一个 team + 三个人 + 一个 project。

    - ``member``：team 成员，**不是**任何一条投影的 owner —— 他能看见什么，全部
      由 team 那条臂决定。
    - ``owner``：某条投影的 owner_user_id，但不在 team 里 —— owner 那条臂。
    - ``stranger``：既不在 team 里也不 own 任何东西 —— 授权谓词的反证。

    ``project_id`` 只用来把查询圈死在本用例造的行上（``search_docs`` 的坐标列
    没有外键，它是投影不是真相），这样断言不受库里其他行影响。
    """
    member, owner, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for user_id in (member, owner, stranger):
        await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)
    team_id = await pg.fetchval(
        "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3)"
        " RETURNING id",
        "Search Docs Test Team",
        member,
        uuid.uuid4().hex[:16],
    )
    # 建 team 的 trigger 已经把 owner 写进 team_members 了；显式写一遍是为了让
    # 「member 在这个 team 里」是这个夹具自己说出来的事实，而不是一个不在眼前的
    # trigger 的副作用 —— trigger 哪天改了，这里仍然成立。
    await pg.execute(
        "INSERT INTO team_members (team_id, user_id) VALUES ($1, $2)"
        " ON CONFLICT DO NOTHING",
        team_id,
        member,
    )
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            team_id,
            member,
        )
        == 1
    )
    project_id = await pg.fetchval(
        "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, $3)"
        " RETURNING id",
        "Search Docs Test Project",
        member,
        team_id,
    )
    tag = uuid.uuid4().hex[:12]
    try:
        yield {
            "member": member,
            "owner": owner,
            "stranger": stranger,
            "team_id": team_id,
            "project_id": project_id,
            "tag": tag,
        }
    finally:
        await pg.execute(
            "DELETE FROM public.search_docs WHERE project_id = $1", project_id
        )
        await pg.execute("DELETE FROM projects WHERE id = $1", project_id)
        await pg.execute("DELETE FROM team_members WHERE team_id = $1", team_id)
        await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        for user_id in (member, owner, stranger):
            await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


def _repo():
    from app.repositories.search_docs_repository import SearchDocsRepository

    return SearchDocsRepository()


def _doc(fx, **over):
    from app.services.search.types import SearchDoc

    values = {
        "entity_kind": "run",
        "entity_id": f"it-{fx['tag']}-1",
        "title": "MH-96 · 雨夜的玻璃",
        "body": "镜头缓慢推近，雨水顺着玻璃滑落",
        "team_id": fx["team_id"],
        "project_id": fx["project_id"],
        "owner_user_id": str(fx["member"]),
    }
    values.update(over)
    return SearchDoc(**values)


async def _search(fx, *, user_id, q="雨水", kinds=("run", "output"), limit=50):
    return await _repo().search(
        q=q,
        kinds=set(kinds),
        team_ids=[],
        user_id=str(user_id),
        project_id=fx["project_id"],
        issue_id=None,
        limit=limit,
    )


@_skip
async def test_upserting_the_same_entity_twice_leaves_one_row_and_moves_updated_at(
    orm_dsn, pg, fx
):
    """投影可重建是设计的一部分：同一个 ``entity_id`` 再投一次是**覆盖**，
    绝不是第二行。这条保证整个挂在 ``search_docs_entity_key`` 上，而那个索引
    存不存在、列对不对，只有服务器知道。"""
    await _repo().upsert(_doc(fx))
    first = await pg.fetchrow(
        "SELECT id, title, updated_at FROM public.search_docs WHERE entity_id = $1",
        f"it-{fx['tag']}-1",
    )
    await _repo().upsert(_doc(fx, title="MH-96 · 雨夜的玻璃（重剪）"))

    rows = await pg.fetch(
        "SELECT id, title, updated_at FROM public.search_docs WHERE entity_id = $1",
        f"it-{fx['tag']}-1",
    )
    assert len(rows) == 1, "同一个 entity_id 长出了第二行"
    assert rows[0]["id"] == first["id"], "覆盖写成了删旧建新"
    assert rows[0]["title"] == "MH-96 · 雨夜的玻璃（重剪）"
    assert rows[0]["updated_at"] > first["updated_at"]


@_skip
async def test_a_multibyte_body_survives_the_round_trip_and_is_searchable(
    orm_dsn, pg, fx
):
    """正文里是中文（本仓的剧本行就是），所以 ``similarity()`` / ``ILIKE`` /
    ``GREATEST`` 必须在真库上对多字节文本成立一次 —— 编译期断言对此一无所知。"""
    await _repo().upsert(_doc(fx))
    hits = await _search(fx, user_id=fx["member"], q="雨水")
    assert [h["entity_id"] for h in hits] == [f"it-{fx['tag']}-1"]
    hit = hits[0]
    assert hit["body"] == "镜头缓慢推近，雨水顺着玻璃滑落"
    assert isinstance(hit["score"], float)
    # Snowflake 纪律：BIGINT / uuid 列一律以字符串出口。
    assert hit["id"] == str(hit["id"]) and hit["team_id"] == str(fx["team_id"])
    assert hit["owner_user_id"] == str(fx["member"])


@_skip
async def test_a_literal_percent_matches_itself_instead_of_everything(orm_dsn, pg, fx):
    """CLAUDE.md「ILIKE 模式的转义责任要跟着模式走」：拼模式的人负责转义。

    少了 ``escape_like``，用户在搜索框里打一个 ``%`` 就是一次全表匹配 —— 而
    编译期断言（``"ESCAPE" in sql``）对此一无所知，它只看得见子句在不在。
    突变实测：拿掉 ``escape_like`` 这条转红；拿掉 ``ESCAPE '\\'`` 不会（见
    模块 docstring —— 反斜杠本来就是 Postgres LIKE 的默认转义符）。
    """
    await _repo().upsert(_doc(fx))  # 标题/正文里没有字面 %
    await _repo().upsert(
        _doc(
            fx,
            entity_id=f"it-{fx['tag']}-2",
            entity_kind="output",
            title="渲染进度 100% 完成",
            body=None,
        )
    )

    hits = await _search(fx, user_id=fx["member"], q="%")
    ids = {h["entity_id"] for h in hits}
    assert f"it-{fx['tag']}-2" in ids, "字面 % 应该匹配到它自己"
    assert f"it-{fx['tag']}-1" not in ids, "一个 % 匹配了所有东西 —— 转义没生效"


@_skip
async def test_the_authorisation_predicate_holds_on_the_server(orm_dsn, pg, fx):
    """授权谓词恒在：team 一臂、owner 一臂，两臂都不沾的人零命中。

    这条同时是 ``Uuid`` 双绑定的真栈证据 —— 同一个绑定值要在一条查询里同时喂给
    ``team_members.user_id`` 和 ``search_docs.owner_user_id``。绑错的表现是
    ``operator does not exist: uuid = character varying``，编译期看不见。
    """
    # 这一条的 owner 是 team 外的人：member 能看见它，只能是靠 team 那条臂。
    await _repo().upsert(_doc(fx, owner_user_id=str(fx["owner"])))

    by_team = await _search(fx, user_id=fx["member"])
    assert [h["entity_id"] for h in by_team] == [f"it-{fx['tag']}-1"]

    # owner 不在 team 里，他看得见是靠 owner 那条臂。
    by_owner = await _search(fx, user_id=fx["owner"])
    assert [h["entity_id"] for h in by_owner] == [f"it-{fx['tag']}-1"]

    # 两臂都不沾 —— 零命中。少了授权谓词这张表就是一张对所有人开放的全量索引。
    assert await _search(fx, user_id=fx["stranger"]) == []


@_skip
async def test_another_teams_id_narrows_instead_of_widening(orm_dsn, pg, fx):
    """``team_ids`` 是**过滤不是授权**。传别人的 team id 只会让结果变少 ——
    因为授权谓词是另一个 ``.where()``，不是被它替换掉的那个
    （``rpc_user_media_text_search`` 那个洞正是合成一个谓词的形状）。"""
    await _repo().upsert(_doc(fx))
    assert len(await _search(fx, user_id=fx["member"])) == 1

    narrowed = await _repo().search(
        q="雨水",
        kinds={"run", "output"},
        team_ids=[fx["team_id"] + 999_999],
        user_id=str(fx["member"]),
        project_id=fx["project_id"],
        issue_id=None,
        limit=50,
    )
    assert narrowed == []
