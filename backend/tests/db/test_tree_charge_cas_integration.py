"""``settle_tree_if_closed`` 的收口 CAS 在真 Postgres 上跑一遍。

WHY THIS FILE EXISTS
────────────────────
同名单测里的 session 全是桩：它们证明「我们写了什么」，证明不了「服务器接受了
什么」（CLAUDE.md「读正常 ≠ 服务正常」）。这条链上有**三处**只有真库能判：

  * **CAS 的 where** —— ``metadata_json['cost']['charged_at'].astext.is_(None)``
    编译成 ``metadata_json -> 'cost' ->> 'charged_at' IS NULL``。一棵还没收过口
    的树，``cost`` 这一层可能存在也可能不存在；两种形状都必须匹配上，否则
    **第一次收口就抢不到**，整棵树一分不扣而日志只会说 ``already``。
  * **CAS 的 values** —— 两层 ``jsonb ||`` 合并（顶层塞回 ``cost``，``cost`` 里塞
    ``charged_at``）。写错的表现不是报错，是**把 cost 视图整个覆盖掉**：
    ``own_cents`` / BYOK 四道一起没了，而扣费已经发生、没有任何探针会说。
  * **树的取行** —— ``id = root OR root_run_id = root``。``root_run_id`` 在 root
    行上是 NULL、在子孙行上指向**真** root，所以这一条 WHERE 要同时捞到三代。
    漏掉孙子那一行的表现是少收钱，同样静默。

幂等（第二次调用必须 ``already``）也只有真库说得出来：它靠的是第一次那条 UPDATE
真的把戳落了盘，而不是 Python 里记了个标志。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_tree_charge_cas_integration.py -v

``INTEGRATION_DATABASE_URL`` 没设就整体 skip —— 这个文件里没有一条在无库时仍然
「通过」的用例，那种用例比没有更糟。
"""

from __future__ import annotations

import json
import math
import os
import uuid
from typing import Any, Dict
from unittest.mock import AsyncMock

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason=(
        "INTEGRATION_DATABASE_URL not set — tree_charge CAS integration tests "
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


# 三代各自的 cost 视图。**每一行的两道各带一条 BYOK 道**，所以平台桶 ≠ 真实花费
# —— 一个把 BYOK 减法写丢的实现会扣 22 而不是 9，真库上立刻看得出来。
_ROOT_COST = {
    "own_cents": 10.0,
    "own_byok_cents": 4.0,
    "media_cents": 5.0,
    "media_byok_cents": 5.0,
    # ⚠️ 故意放一个巨大的 by_child：收口**按行聚合**，绝不读它。读了就会扣
    # 1009 而不是 9。workforce 链上 root 的这一格恒为空，依赖它的实现在真栈上
    # 反而会少收 —— 两个方向都错，所以这里钉死「不读」。
    "by_child": {"bogus": 1000.0},
    "spent_cents": 22.0,
}
_CHILD_COST = {"own_cents": 3.0}
_GRANDCHILD_COST = {"own_cents": 4.0, "own_byok_cents": 4.0}

#: root 6（10−4 + 5−5）+ 子 3 + 孙 0（4−4）。
_EXPECTED_PLATFORM = 9.0


async def _make_run(pg, *, agent_id, user_id, cost, status, parent=None, root=None):
    return await pg.fetchval(
        "INSERT INTO public.agent_runs"
        " (agent_id, user_id, status, trigger, team_id, metadata_json,"
        "  parent_run_id, root_run_id)"
        " VALUES ($1, $2, $3, 'chat', $4, $5::jsonb, $6, $7) RETURNING id",
        agent_id,
        user_id,
        status,
        4242,
        json.dumps({"cost": cost}),
        parent,
        root,
    )


@pytest.fixture
async def tree(pg) -> Dict[str, Any]:
    """root → 子 → **孙** 三行一棵树。

    孙子是必须的：``root_run_id`` 在它身上指向**真** root（不是它的直接父），
    而收口的那条 WHERE 正是靠这一点一次捞全三代。只建两行的夹具证不出来。
    """
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"tc-agent-{uuid.uuid4().hex[:8]}",
    )
    user_id = uuid.uuid4()
    made: list[int] = []
    try:
        root = await _make_run(
            pg,
            agent_id=agent_id,
            user_id=user_id,
            cost=_ROOT_COST,
            status="completed",
        )
        made.append(root)
        child = await _make_run(
            pg,
            agent_id=agent_id,
            user_id=user_id,
            cost=_CHILD_COST,
            status="completed",
            parent=root,
            root=root,
        )
        made.append(child)
        grand = await _make_run(
            pg,
            agent_id=agent_id,
            user_id=user_id,
            cost=_GRANDCHILD_COST,
            status="completed",
            parent=child,
            root=root,
        )
        made.append(grand)
        yield {"agent_id": agent_id, "root": root, "child": child, "grand": grand}
    finally:
        if made:
            await pg.execute(
                "DELETE FROM public.agent_runs WHERE id = ANY($1::bigint[])", made
            )
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)


async def _charged_at(pg, run_id):
    return await pg.fetchval(
        "SELECT metadata_json -> 'cost' ->> 'charged_at'"
        " FROM public.agent_runs WHERE id = $1",
        run_id,
    )


def _stub_reconcile(monkeypatch):
    """只拦扣费本身 —— 读、CAS、聚合三件事都跑真的。"""
    from app.services.ai.billing.token_billing import ReconcileResult

    mock = AsyncMock(
        return_value=ReconcileResult(
            charged=True, charged_points=9.0, byo_key=False, usage_logged=False
        )
    )
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", mock)
    return mock


@_skip
async def test_the_first_settle_wins_the_cas_and_the_second_finds_it_taken(
    orm_dsn, tree, pg, monkeypatch
):
    """第一次收口：抢到戳、按三行自身平台桶扣一次、``reference_id`` 是 root。
    第二次：戳已在，``already``，一分不扣。

    幂等靠的是那条 UPDATE 真的落了盘 —— 这正是桩 session 证不了的部分。"""
    from app.services.ai.billing import tree_charge

    mock = _stub_reconcile(monkeypatch)
    assert await _charged_at(pg, tree["root"]) is None, "夹具就该是没收过口的"

    first = await tree_charge.settle_tree_if_closed(run_id=str(tree["root"]))
    assert (first.settled, first.reason) == (True, "charged")

    stamp = await _charged_at(pg, tree["root"])
    assert stamp, "CAS 没把 charged_at 落库 —— 幂等就只是个说法"

    kwargs = mock.await_args.kwargs
    assert kwargs["cost_points"] == _EXPECTED_PLATFORM
    assert math.ceil(kwargs["cost_points"]) == 9  # ceil 在 reconcile_run 里
    # 一树一行流水，reference_id 是 root（读方按 root_run_id 合计）。
    assert kwargs["run_id"] == str(tree["root"])
    assert kwargs["log_usage"] is False

    second = await tree_charge.settle_tree_if_closed(run_id=str(tree["child"]))
    assert (second.settled, second.reason) == (False, "already")
    assert mock.await_count == 1, "同一棵树收了两次口"
    assert await _charged_at(pg, tree["root"]) == stamp, "戳被改写了"


@_skip
async def test_the_cas_merge_keeps_every_cost_lane_it_found(orm_dsn, tree, pg):
    """两层 ``jsonb ||`` 写错的表现不是报错，是**把 cost 视图整个覆盖掉** ——
    四道花费一起没了，而钱已经扣了。四道逐个回读。"""
    from app.services.ai.billing import tree_charge

    await tree_charge.settle_tree_if_closed(run_id=str(tree["root"]))

    stored = json.loads(
        await pg.fetchval(
            "SELECT (metadata_json -> 'cost')::text FROM public.agent_runs"
            " WHERE id = $1",
            tree["root"],
        )
    )
    for key in ("own_cents", "own_byok_cents", "media_cents", "media_byok_cents"):
        assert stored[key] == _ROOT_COST[key], f"{key} 被 CAS 合并冲掉了"
    assert stored["charged_at"], "戳没进去"


@_skip
async def test_a_tree_with_one_run_still_running_is_deferred_and_unstamped(
    orm_dsn, tree, pg, monkeypatch
):
    """只要有一行还在跑就不收口 —— 而且**戳不许盖**。盖了就等于把这棵树永久
    免单：后面真正收口的那一次会拿到 ``already``。"""
    from app.services.ai.billing import tree_charge

    mock = _stub_reconcile(monkeypatch)
    await pg.execute(
        "UPDATE public.agent_runs SET status = 'running' WHERE id = $1",
        tree["grand"],
    )

    out = await tree_charge.settle_tree_if_closed(run_id=str(tree["root"]))
    assert (out.settled, out.reason) == (False, "deferred")
    mock.assert_not_awaited()
    assert await _charged_at(pg, tree["root"]) is None

    # 那一行结束之后，收口才成立 —— 证明上面不是「永远收不了口」。
    await pg.execute(
        "UPDATE public.agent_runs SET status = 'completed' WHERE id = $1",
        tree["grand"],
    )
    later = await tree_charge.settle_tree_if_closed(run_id=str(tree["grand"]))
    assert later.reason == "charged"
    assert mock.await_args.kwargs["cost_points"] == _EXPECTED_PLATFORM


@_skip
async def test_a_tree_whose_cost_key_is_absent_can_still_be_settled(
    orm_dsn, pg, monkeypatch
):
    """``metadata_json`` 里**没有** ``cost`` 这一层时，CAS 的 where
    （``-> 'cost' ->> 'charged_at' IS NULL``）必须照样匹配，``jsonb_set`` 式的写法
    在这里会静默什么都不改 —— 那样第一次收口就抢不到，整棵树一分不扣。"""
    from app.services.ai.billing import tree_charge

    mock = _stub_reconcile(monkeypatch)
    agent_id = await pg.fetchval(
        "INSERT INTO public.ai_agents (name) VALUES ($1) RETURNING id",
        f"tc-bare-{uuid.uuid4().hex[:8]}",
    )
    run_id = await pg.fetchval(
        "INSERT INTO public.agent_runs"
        " (agent_id, user_id, status, trigger, metadata_json)"
        " VALUES ($1, $2, 'completed', 'chat', '{}'::jsonb) RETURNING id",
        agent_id,
        uuid.uuid4(),
    )
    try:
        out = await tree_charge.settle_tree_if_closed(run_id=str(run_id))
        assert (out.settled, out.reason) == (True, "charged")
        assert await _charged_at(pg, run_id), "没有 cost 层时 CAS 没写进去"
        # 一分没花的树不是 BYOK 树 —— 两者在 note 里必须分得开。
        assert mock.await_args.kwargs["cost_points"] == 0.0
        assert mock.await_args.kwargs["byo_key"] is False
    finally:
        await pg.execute("DELETE FROM public.agent_runs WHERE id = $1", run_id)
        await pg.execute("DELETE FROM public.ai_agents WHERE id = $1", agent_id)
