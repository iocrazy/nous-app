"""``unified_search`` 的**两道门**在真 Postgres 上跑一遍（3c Task 14 评审 Important 2）。

WHY THIS FILE EXISTS
────────────────────
`tests/services/search/test_unified_search.py` 里那一整套的 `visible_issue_ids`
是桩的 —— 它们证明「服务层在调用那道门」，证明不了「那道门真的拦得住人」。而
这个端点是**跨团队**的：它唯一的安全断言是「别团队成员搜同一个词，三组全空」，
那句话里的每一个环节都只有服务器说得出来：

  * SQL 侧的授权谓词 `team_id ∈ 我的 team OR owner_user_id = 我` —— 那个
    `IN (SELECT team_id FROM team_members WHERE user_id = :me)` 子查询要真的
    把 B 用户排除掉。单测里它是一段编译过的字符串。
  * Python 侧的 `visible_issue_ids` 走 `issues` 真表 + `is_team_member` 真查询。
    两个 uuid 列（`created_by_user_id` / `assignee_user_id`）在 ORM 上是
    `Uuid`，比较时一旦把 `UUID` 和 `str` 比错就**恒为 False 且不报错** —— 那会
    404 掉每一个合法 owner（`issue_repository` 顶部那段 ★ 审计的原话）。方向反
    过来同样致命：拿 `UUID(...) == "uuid-string"` 判「可见」，写错成恒 True
    就是一次跨团队泄漏。
  * **无议题的 run 必须活下来。** 它由谓词的 owner 那条臂收下，不进第二道；
    这条只有在真库里跑一次「A 用户能搜到自己那条无议题 run」才算证明过。

一句话：本文件是 `search_docs` 那张表**不会变成一张对所有人开放的全量索引**的
唯一真凭据。它把别人的 run 标题、别人的剧本行都摊平在一列 text 里，一次 ILIKE
就全读走了。

Transport 与 `tests/db/test_search_docs_repository_integration.py` 同：asyncpg
建拆夹具，被测代码自己走 `app.db.session` 的 SQLAlchemy 引擎（`orm_dsn` 把它
重指到同一个 DSN）。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \
    uv run pytest tests/db/test_unified_search_visibility_integration.py -v

`INTEGRATION_DATABASE_URL` 没设就整体 skip —— 这个文件里没有一条在无库时仍然
「通过」的用例，那种用例比没有更糟。
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import Any, Dict

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — unified_search visibility tests need a DB."
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
    """两个 team、两个人、一件 A team 的议题、两条 A team 的投影行。

    - ``alice``：A team 成员，议题的创建人，两条投影的 owner。
    - ``bob``：**B team** 成员。他与 A team 的一切没有任何关系 —— 既不是成员、
      不是创建人、不是指派人、也不 own 任何一条投影。他是这个文件的全部意义。

    ``tag`` 是一个本次独有的词，同时出现在议题标题、产出标题和 run 正文里，所以
    一次搜索能同时打到三组；``project_id`` 把查询圈死在本用例造的行上。
    """
    alice, bob = uuid.uuid4(), uuid.uuid4()
    for user_id in (alice, bob):
        await pg.execute("INSERT INTO auth.users (id) VALUES ($1)", user_id)

    async def _team(name: str, owner: uuid.UUID) -> int:
        team_id = await pg.fetchval(
            "INSERT INTO teams (name, owner_id, invite_code) VALUES ($1, $2, $3)"
            " RETURNING id",
            name,
            owner,
            uuid.uuid4().hex[:16],
        )
        # 建 team 的 trigger 已经把 owner 写进 team_members 了；显式再写一遍，
        # 让「这个人在这个 team 里」是夹具自己说出来的事实而不是一个不在眼前
        # 的 trigger 的副作用。
        await pg.execute(
            "INSERT INTO team_members (team_id, user_id) VALUES ($1, $2)"
            " ON CONFLICT DO NOTHING",
            team_id,
            owner,
        )
        return team_id

    team_a = await _team("Unified Search Team A", alice)
    team_b = await _team("Unified Search Team B", bob)
    # 反向断言：bob 绝不能在 A team 里。整个文件的前提就是这一行。
    assert (
        await pg.fetchval(
            "SELECT count(*) FROM team_members WHERE team_id = $1 AND user_id = $2",
            team_a,
            bob,
        )
        == 0
    )

    project_id = await pg.fetchval(
        "INSERT INTO projects (name, owner_id, team_id) VALUES ($1, $2, $3)"
        " RETURNING id",
        "Unified Search Test Project",
        alice,
        team_a,
    )
    tag = f"rainprobe{uuid.uuid4().hex[:10]}"
    issue_id = await pg.fetchval(
        """
        INSERT INTO public.issues
            (issue_number, identifier, title, description,
             team_id, project_id, created_by_user_id, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'in_progress')
        RETURNING id
        """,
        90001,
        f"UST-{tag[:8]}",
        f"Storm study {tag}",
        f"the {tag} board wants a look at the rain",
        team_a,
        project_id,
        alice,
    )
    try:
        yield {
            "alice": alice,
            "bob": bob,
            "team_a": team_a,
            "team_b": team_b,
            "project_id": project_id,
            "issue_id": issue_id,
            "tag": tag,
        }
    finally:
        await pg.execute(
            "DELETE FROM public.search_docs WHERE project_id = $1", project_id
        )
        await pg.execute("DELETE FROM public.issues WHERE id = $1", issue_id)
        await pg.execute("DELETE FROM projects WHERE id = $1", project_id)
        for team_id in (team_a, team_b):
            await pg.execute("DELETE FROM team_members WHERE team_id = $1", team_id)
            await pg.execute("DELETE FROM teams WHERE id = $1", team_id)
        for user_id in (alice, bob):
            await pg.execute("DELETE FROM auth.users WHERE id = $1", user_id)


async def _seed_docs(fx) -> None:
    """一条挂在 A team 议题下的产出行 + 一条 A team 的**无议题** run 行。

    第二条是本文件的另一半：它只能靠谓词的 owner 臂活下来，而 Python 那道门
    必须完全不碰它。
    """
    from app.repositories.search_docs_repository import get_search_docs_repository
    from app.services.search.types import SearchDoc

    repo = get_search_docs_repository()
    await repo.upsert(
        SearchDoc(
            entity_kind="output",
            entity_id=f"script_shot:{fx['tag']}:1",
            title=f"S1 · Shot 1 · {fx['tag']}",
            body=f"description: {fx['tag']} on the glass",
            kind="script_shot",
            ref_id=fx["tag"],
            version=1,
            team_id=fx["team_a"],
            project_id=fx["project_id"],
            issue_id=fx["issue_id"],
            owner_user_id=str(fx["alice"]),
        )
    )
    await repo.upsert(
        SearchDoc(
            entity_kind="run",
            entity_id=f"run-{fx['tag']}",
            title="canvas run",
            body=f"generated 6 frames for {fx['tag']}",
            team_id=fx["team_a"],
            project_id=fx["project_id"],
            # 无议题：画布道 / 聊天道的 run 就是这个形状。
            issue_id=None,
            owner_user_id=str(fx["alice"]),
            status="completed",
        )
    )


async def _search(fx, *, user_id):
    from app.services.search.service import unified_search

    return await unified_search(
        auth=SimpleNamespace(user_id=str(user_id)),
        q=fx["tag"],
        kinds={"issue", "run", "output"},
        team_id=None,
        project_id=fx["project_id"],
        issue_id=None,
        limit_per_group=10,
    )


@_skip
async def test_the_owner_finds_all_three_groups(orm_dsn, pg, fx):
    """正向对照。没有它，下面那条负例可能只是因为**什么都没种进去**而通过
    —— 一个永远返回空的端点会让安全断言看起来完美。"""
    await _seed_docs(fx)
    res = await _search(fx, user_id=fx["alice"])

    assert [h.issue_key for h in res.groups.issues] == [f"UST-{fx['tag'][:8]}"]
    assert res.totals.issues == 1
    assert [h.id for h in res.groups.outputs] == [f"script_shot:{fx['tag']}:1"]
    # 无议题的 run 活了下来，而且没有可跳转的页面（deep_link 是空串）。
    assert [h.id for h in res.groups.runs] == [f"run-{fx['tag']}"]
    assert res.groups.runs[0].deep_link == ""
    # 产出那条的深链要真的被 issues 表上的 (identifier, team_id) 填上。
    assert res.groups.outputs[0].deep_link == (
        f"/team/{fx['team_a']}/todolist/UST-{fx['tag'][:8]}"
    )


@_skip
async def test_a_member_of_another_team_gets_three_empty_groups_not_a_refusal(
    orm_dsn, pg, fx
):
    """**本文件存在的理由。** 同一个词、同一批行，换一个别 team 的人来搜。

    三组必须全空，而且是「没有匹配」这个答案 —— 不是 404、不是异常。空组和
    拒绝在跨团队端点上必须长得一样，否则一次搜索就能探出别的 team 有没有某个词。
    """
    await _seed_docs(fx)
    res = await _search(fx, user_id=fx["bob"])

    assert res.groups.issues == []
    assert res.groups.runs == []
    assert res.groups.outputs == []
    assert (res.totals.issues, res.totals.runs, res.totals.outputs) == (0, 0, 0)


@_skip
async def test_a_row_sql_admits_is_still_dropped_by_the_issue_gate(orm_dsn, pg, fx):
    """**第二道门单独的证据。**

    上面那条负例其实是 **SQL 那道门**干的：bob 既不在 A team 也不 own 任何行，
    谓词直接把他挡在外面，Python 那道门根本没机会开口。所以那条用例证明不了
    `visible_issue_ids` 在做事 —— 把它整个删掉，那条用例照样绿。

    这里造的行正是两道门职责分界处的形状：``team_id`` 是 **B team**（所以
    SQL 的 team 臂**放行** bob），而 ``issue_id`` 指着 **A team** 的那件议题
    （bob 看不见）。投影的 team 与它所属议题的 team 不一致不是假设 —— 投影
    是投影不是真相，它的坐标列没有外键，一次跨 team 派发、一次议题改属、
    一次漏投都会造出这个形状。

    断言分两半，缺一不可：先证明 **SQL 确实放行了**（直接问 repository，那一行
    在），再证明 **服务层仍然不给**。少了前半句，这条用例会在「SQL 其实也挡住
    了」时假绿，就和上面那条一样。
    """
    from app.repositories.search_docs_repository import get_search_docs_repository
    from app.services.search.types import SearchDoc

    await get_search_docs_repository().upsert(
        SearchDoc(
            entity_kind="output",
            entity_id=f"script_shot:{fx['tag']}:cross",
            title=f"S9 · leaked · {fx['tag']}",
            body=f"{fx['tag']} body that B team must not read",
            kind="script_shot",
            ref_id=f"{fx['tag']}x",
            version=1,
            # SQL 放行：bob 是 B team 成员。
            team_id=fx["team_b"],
            project_id=fx["project_id"],
            # Python 必须拦下：这件议题在 A team，bob 不是创建人 / 指派人 / 成员。
            issue_id=fx["issue_id"],
            owner_user_id=str(fx["alice"]),
        )
    )

    admitted = await get_search_docs_repository().search(
        q=fx["tag"],
        kinds={"output"},
        team_ids=[],
        user_id=str(fx["bob"]),
        project_id=fx["project_id"],
        issue_id=None,
        limit=50,
    )
    # 前半句：SQL 这一层**真的**把它交出来了。
    assert [d["entity_id"] for d in admitted] == [f"script_shot:{fx['tag']}:cross"]

    # 后半句：整条服务链上，它仍然到不了 bob。
    res = await _search(fx, user_id=fx["bob"])
    assert res.groups.outputs == []
    assert res.totals.outputs == 0


@_skip
async def test_the_row_is_really_there_when_the_gate_is_taken_away(orm_dsn, pg, fx):
    """负例的**可证伪性**：上一条的空组必须来自那道门，而不是来自「这批行根本
    不存在」或「那个词拼错了」。

    这里绕过两道门直接问服务器：同样的词、同样的 project，`search_docs` 上就是
    有两行，而 `issues` 上就是有那一件。所以 bob 拿到的空，是被拦下来的空。
    """
    await _seed_docs(fx)
    docs = await pg.fetchval(
        "SELECT count(*) FROM public.search_docs"
        " WHERE project_id = $1 AND (title ILIKE $2 OR body ILIKE $2)",
        fx["project_id"],
        f"%{fx['tag']}%",
    )
    issues = await pg.fetchval(
        "SELECT count(*) FROM public.issues WHERE id = $1 AND title ILIKE $2",
        fx["issue_id"],
        f"%{fx['tag']}%",
    )
    assert (docs, issues) == (2, 1)
