"""退款抵扣走真 Postgres —— ``charged_points_for_references`` 与它上面那层
``charged_points_for_run_trees``（终审 I6）。

WHY THIS FILE EXISTS
────────────────────
同名单测里的 session 是桩的：它们证明「我们拿两批行做了什么减法」，证明不了
**服务器会不会把那两批行交出来**。这条链上有三处只有真库能判：

  * **两条腿各自的谓词** —— 拆成 ``type='consume'`` 与 ``type='refund'`` 两次查询
    （而不是 ``type IN (…)``）是为了保住 mig 474 那个 partial 索引。桩 session
    对「谓词还能不能选出行」一无所知：把 refund 那条腿的 ``reference_type`` 写错，
    单测照样绿（桩按调用顺序发牌，根本不看 WHERE），而真库会交出零行 —— 退款不
    抵扣，回到缺陷本身。
  * **两种流水的符号** —— ``consume`` 为负、``refund`` 为正，这不是代码里的约定，
    是 mig 123 那个 RPC 的行为（``points_balance + p_amount``）。减法的方向只能由
    真的写进这张表的行来证。手写夹具可以把 refund 写成负数，然后「净扣 = 扣 − 退」
    的实现会**加**上去，而单测的夹具正好是自己编的那一份。
  * **``amount`` 是 INTEGER** —— 这张表的金额列不是 numeric。桩里随手写 21.0 能过，
    真库上非整数会被拒/被截，所以这里的夹具必须是整数，这件事只有真库说得出来。
  * **两条 partial 索引到底能不能被 planner 用上**（mig 474 / mig 477）—— 「谓词蕴含」
    是 **planner 的判断**，不是我们读 SQL 文本能断言的事。单测里那条
    ``test_each_leg_keeps_its_equality_on_type_so_the_partial_index_holds`` 只能证明
    我们**发出去**的两条 WHERE 长什么样；索引到底被选中没有，只有 ``EXPLAIN`` 说了算。

``charged_points_for_run_trees`` 一并在真库上跑：它按 ``root_run_id`` 把全树的流水
合起来，而「退款挂在 root 那一行、consume 也挂在 root」这个真实形状（2026-09-17 那
81 棵树就是这样）在桩里是拼不出来的。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_points_refund_offset_integration.py -v

``INTEGRATION_DATABASE_URL`` 没设就整体 skip —— 这个文件里没有一条在无库时仍然
「通过」的用例，那种用例比没有更糟。
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

asyncpg = pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — the refund-offset integration "
        "tests need a DB."
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
async def team(pg):
    owner = await pg.fetchval("INSERT INTO auth.users DEFAULT VALUES RETURNING id")
    team_id = await pg.fetchval(
        "INSERT INTO public.teams (name, owner_id, invite_code, kind)"
        " VALUES ($1, $2, $3, 'personal') RETURNING id",
        f"pr-team-{uuid.uuid4().hex[:8]}",
        owner,
        f"PR{uuid.uuid4().hex[:8].upper()}",
    )
    try:
        yield team_id
    finally:
        await pg.execute(
            "DELETE FROM public.point_transactions WHERE team_id = $1", team_id
        )
        await pg.execute("DELETE FROM public.teams WHERE id = $1", team_id)
        await pg.execute("DELETE FROM auth.users WHERE id = $1", owner)


async def _txn(pg, *, team_id, ref_id, txn_type, amount, ref_type="agent_run"):
    """一行流水。``amount`` 的符号照库里的真实约定：consume 负、refund 正。"""
    await pg.execute(
        "INSERT INTO public.point_transactions"
        " (team_id, type, amount, balance_after, reference_type, reference_id)"
        " VALUES ($1, $2, $3, 0, $4, $5)",
        team_id,
        txn_type,
        amount,
        ref_type,
        str(ref_id),
    )


async def _charged(ref_ids):
    from app.repositories.points_repository import PointsRepository

    return await PointsRepository().charged_points_for_references(
        reference_type="agent_run", reference_ids=[str(r) for r in ref_ids]
    )


@_skip
async def test_a_refund_row_is_deducted_from_what_the_reference_still_owes(
    orm_dsn, pg, team
):
    """终审 I6 的核心。两种流水都真的写进表里，符号由库说了算。

    2026-09-17 之后那 81 棵树就卡在这条路上：钱退了，而三个用户可见的宿主仍然
    显示 ``◇ n``。"""
    ref = uuid.uuid4().int % 10**12
    await _txn(pg, team_id=team, ref_id=ref, txn_type="consume", amount=-21)
    await _txn(pg, team_id=team, ref_id=ref, txn_type="refund", amount=6)

    assert await _charged([ref]) == {str(ref): 15.0}


@_skip
async def test_a_fully_refunded_reference_reports_zero_and_keeps_its_key(
    orm_dsn, pg, team
):
    """全额退款后是 0，不是原来那个数；键仍在场 —— 「扣过、又退了」与「从没扣过」
    是两个答案，消费方靠缺席/在场区分。"""
    ref = uuid.uuid4().int % 10**12
    await _txn(pg, team_id=team, ref_id=ref, txn_type="consume", amount=-7)
    await _txn(pg, team_id=team, ref_id=ref, txn_type="refund", amount=7)

    out = await _charged([ref])
    assert out == {str(ref): 0.0}
    assert str(ref) in out


@_skip
async def test_an_unrefunded_reference_is_untouched(orm_dsn, pg, team):
    """正向对照：没有退款行的引用**一分不少**。少了这条，一个「永远返回 0」的
    实现也能让上面两条转绿。"""
    ref = uuid.uuid4().int % 10**12
    await _txn(pg, team_id=team, ref_id=ref, txn_type="consume", amount=-9)

    assert await _charged([ref]) == {str(ref): 9.0}


@_skip
async def test_other_transaction_types_do_not_leak_into_the_offset(orm_dsn, pg, team):
    """``gift`` / ``purchase`` / ``daily_gift`` 也是正数流水，但它们不是对这次回合的
    退款。两条腿各自钉死 ``type`` 的等值，正是为了不让它们漏进减法 —— 一张送了
    100 分的礼券不该让一次真扣掉的消耗在界面上变成 0。"""
    ref = uuid.uuid4().int % 10**12
    await _txn(pg, team_id=team, ref_id=ref, txn_type="consume", amount=-5)
    await _txn(pg, team_id=team, ref_id=ref, txn_type="gift", amount=100)
    await _txn(pg, team_id=team, ref_id=ref, txn_type="purchase", amount=50)

    assert await _charged([ref]) == {str(ref): 5.0}


@_skip
async def test_a_refund_under_another_reference_type_is_not_borrowed(orm_dsn, pg, team):
    """refund 那条腿的 ``reference_type`` 必须跟着查询走。写死成别的值、或者干脆
    漏掉这个条件，桩 session 一无所知（它按调用顺序发牌），只有真库会让这条红。"""
    ref = uuid.uuid4().int % 10**12
    await _txn(pg, team_id=team, ref_id=ref, txn_type="consume", amount=-8)
    await _txn(
        pg,
        team_id=team,
        ref_id=ref,
        txn_type="refund",
        amount=8,
        ref_type="order_refund",
    )

    assert await _charged([ref]) == {str(ref): 8.0}


@_skip
async def test_the_tree_reader_sees_the_refund_too(orm_dsn, pg, team):
    """``charged_points_for_run_trees`` 建在上面那个方法之上，所以修一处两处都好。
    这条钉住的是「界面读到的那个数」—— 三个宿主（议题线程 / 聊天气泡 / ``done``
    状态帧）共用它。

    形状照 2026-09-17 那 81 棵树：consume 与 refund 都挂在 **root** 的 id 上，而树
    里还有一个子 run（按 ``root_run_id`` 合计，不是「取 root 那一条」）。
    """
    from app.services.billing.run_tree_points import charged_points_for_run_trees

    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"pr-agent-{uuid.uuid4().hex[:8]}",
    )
    user_id = uuid.uuid4()
    made: list[int] = []
    try:
        root = await pg.fetchval(
            "INSERT INTO public.agent_runs"
            " (agent_id, user_id, status, trigger, team_id, metadata_json)"
            " VALUES ($1, $2, 'completed', 'chat', $3, $4::jsonb) RETURNING id",
            agent_id,
            user_id,
            team,
            json.dumps({"cost": {"own_cents": 10.0}}),
        )
        made.append(root)
        child = await pg.fetchval(
            "INSERT INTO public.agent_runs"
            " (agent_id, user_id, status, trigger, team_id, metadata_json,"
            "  parent_run_id, root_run_id)"
            " VALUES ($1, $2, 'completed', 'chat', $3, $4::jsonb, $5, $5)"
            " RETURNING id",
            agent_id,
            user_id,
            team,
            json.dumps({"cost": {"own_cents": 3.0}}),
            root,
        )
        made.append(child)

        await _txn(pg, team_id=team, ref_id=root, txn_type="consume", amount=-4)
        assert await charged_points_for_run_trees([root]) == {str(root): 4.0}

        await _txn(pg, team_id=team, ref_id=root, txn_type="refund", amount=4)
        assert await charged_points_for_run_trees([root]) == {str(root): 0.0}
    finally:
        if made:
            await pg.execute(
                "DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])", made
            )
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


# ── 两条腿的 partial 索引真的被 planner 选中（mig 474 / 477，终审 I-2）──────


#: 两条腿真发出去的查询形状。与 ``_charge_leg_stmt`` 编译结果同构 —— 这里写字面 SQL
#: 是因为 ``EXPLAIN`` 要的是一条能直接喂给服务器的语句，而被测的命题（「planner 用不
#: 用得上那个 partial 索引」）只取决于 WHERE 的**形状**，不取决于它由谁拼出来。
#: 形状与 ORM 的一致性由单测那条编译断言 + mig 477 的文本断言两侧共同钉住。
_LEG_SQL = (
    "SELECT reference_id, sum(amount) FROM point_transactions"
    " WHERE type=$1 AND reference_type='agent_run'"
    " AND reference_id IN ('1','2') GROUP BY reference_id"
)


async def _plan(pg, sql: str) -> str:
    """强制关掉顺扫之后的执行计划。

    ⚠️ 关顺扫是必须的：drift 库里这张表只有个位数行，planner 永远会选顺扫（它更
    便宜），于是「索引能不能用」这个命题在小表上根本观察不到。关掉之后如果计划里
    仍然是 Seq Scan（带天价 cost），就说明那条索引**用不上** —— 这正是要区分的事。
    """
    rows = await pg.fetch(f"EXPLAIN {sql}")
    return "\n".join(r[0] for r in rows)


@_skip
@pytest.mark.parametrize(
    "txn_type,index_name",
    [
        ("consume", "idx_point_transactions_agent_run_consume"),
        ("refund", "idx_point_transactions_agent_run_refund"),
    ],
)
async def test_each_leg_is_served_by_its_own_partial_index(pg, txn_type, index_name):
    """终审 I-2 的决定性验证：refund 腿此前**没有任何索引**可用（mig 123 那个
    refund 唯一索引首列是 ``team_id``，这条查询不带 team_id），于是在同一个被前端
    轮询的端点上又开了一条全扫。

    断言两件事，缺一不可：
      ① 计划里点名了**这一条**索引；
      ② ``Index Cond`` 之外**没有** ``Filter`` —— 没有残留过滤才说明 planner 真的从
         partial 谓词里把 ``type`` / ``reference_type`` 两个等值**证掉了**（那就是
         「蕴含」本身）。只断言 ①、不断言 ②，一个谓词写歪但仍能走索引+过滤的版本
         会照样转绿，而那正是慢下来的那种形状。
    """
    await pg.execute("SET enable_seqscan=off")
    plan = await _plan(pg, _LEG_SQL.replace("$1", f"'{txn_type}'"))
    assert index_name in plan, plan
    assert "Seq Scan" not in plan, plan
    assert "Filter:" not in plan, plan


@_skip
async def test_the_in_form_loses_both_indexes(pg):
    """反向对照，也是「两条腿而不是一条 ``type IN (…)``」这个设计的**证据**。

    没有这一条，上面那条用例在「合成一条 IN 查询」的实现上也可能碰巧转绿（比如
    planner 选了别的索引），于是那个设计决定就变回了一句无法证伪的注释。
    """
    await pg.execute("SET enable_seqscan=off")
    plan = await _plan(
        pg,
        "SELECT reference_id, sum(amount) FROM point_transactions"
        " WHERE type IN ('consume','refund') AND reference_type='agent_run'"
        " AND reference_id IN ('1','2') GROUP BY reference_id",
    )
    assert "idx_point_transactions_agent_run_consume" not in plan, plan
    assert "idx_point_transactions_agent_run_refund" not in plan, plan
    assert "Seq Scan" in plan, plan
