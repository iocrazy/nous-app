"""``AgentRunsRepository.tree_cost_cents`` —— 一个 root run 的整棵树真花了多少钱。

3d 第 0 票：``agent_runs.cost_cents`` 是「自身 + **已报到的**后代」，由
``run_recorder._finish`` 在父 run 收口时折叠出来。三个按树呈现的消费方（气泡的消耗
行、``done`` 状态帧、效率页）此前都直接读 root 行那一列，于是继承了它的两个缺口：
子 run 还没报回父行时低报，而任何不按 root 过滤的求和又把子 run 数两遍。

``own_cost_cents``（mig 479）每行只记自身，所以「这棵树花了多少」= 按
``COALESCE(root_run_id, id)`` 分组求和 —— 这个键是全仓唯一允许的树键拼法，与
``run_ids_in_trees`` 的 ``root_run_id IN (:roots) OR id IN (:roots)`` 答的是同一棵树。

**断言的是编译出来的 SQL**：注释写「按树分组」不会在漂移时报错，SQL 会。分组本身是
Postgres 干的活，真库执行覆盖在 Task 7。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.agent_runs_repository import AgentRunsRepository

pytestmark = pytest.mark.unit

#: 树键**唯一**允许的拼法，照编译出来的样子写（SQLAlchemy 带 schema 前缀）。
_TREE_KEY = "coalesce(public.agent_runs.root_run_id, public.agent_runs.id)"


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Session:
    """记下每一条语句；``.all()`` 交出预置的行（本文件的方法只用这一种取法）。"""

    def __init__(self, rows: Any = ()) -> None:
        self.stmts: list[Any] = []
        self._rows = rows

    async def execute(self, stmt: Any) -> Any:
        self.stmts.append(stmt)
        rows = self._rows

        class _R:
            def mappings(self) -> "_R":
                return self

            def all(self) -> Any:
                return rows

        return _R()


class _Raising:
    async def execute(self, stmt: Any) -> Any:
        raise RuntimeError("db down")


def _scope(sess: Any):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


@pytest.fixture
def captured_stmt(monkeypatch: pytest.MonkeyPatch):
    """跑一次真方法，交出它实际执行的第一条语句。

    ``agent_runs_repository`` 在 import 时就把 ``read_scope`` 绑成了模块属性，只
    patch ``app.db.session`` 碰不到它 —— 那样它会去连真库。
    """

    async def _run(call, rows: Any = ()) -> Any:
        from app.repositories import agent_runs_repository as mod

        sess = _Session(rows)
        monkeypatch.setattr(mod, "read_scope", _scope(sess))
        await call(mod.AgentRunsRepository())
        assert sess.stmts, "方法一条语句都没执行 —— 大概率被 except 吞了"
        return sess.stmts[0]

    return _run


@pytest.fixture
def repo_with_rows(monkeypatch: pytest.MonkeyPatch):
    """一棵树：root 1 自身 1.0 + 两条子 run 0.5 / 0.25；root 2 一行都没有。

    喂进来的是 **Postgres 分组之后**的形状（``GROUP BY`` 是数据库干的活，在桩里再
    实现一遍只是在测桩）。1.75 这个数从哪来写在用例里。
    """

    def _make(rows: Any):
        from app.repositories import agent_runs_repository as mod

        sess = _Session(rows)
        monkeypatch.setattr(mod, "read_scope", _scope(sess))
        return mod.AgentRunsRepository()

    return _make


async def test_tree_cost_cents_groups_by_coalesce_root_id(captured_stmt):
    """树键只有这一种拼法，钱只从自身列来。

    ``root_run_id`` 单独一列不够：root 行自己的那一列是 NULL，漏掉它等于把根的花费
    整个丢掉。而读 ``cost_cents`` 则会让 root 行那笔折叠额与子 run 自己的行相加，
    一棵树的钱被数两遍。
    """
    s = _sql(await captured_stmt(lambda repo: repo.tree_cost_cents([1, 2])))
    low = s.lower()

    assert _TREE_KEY in low
    assert "own_cost_cents" in s
    assert "GROUP BY" in s
    # 旧列一次都不许出现：这里没有「输出别名」这种豁免，投影就是 (root, cents)。
    assert "own_cost_cents" in s and s.count("cost_cents") == s.count("own_cost_cents")
    # 分组键同时是筛选键 —— 问的是这几棵树，不是这几行。
    assert f"{_TREE_KEY} in (" in low


async def test_tree_cost_cents_every_root_present(repo_with_rows):
    """每个问到的 root 至少映射到它自己。

    读空**不等于**免费。root 2 的行这会儿读不回来（还没落库 / 被过滤掉），回 0.0 是
    「这棵树目前记着 0」，而键整个缺席会让调用方的 ``.get`` 拿到 None 再 ``?? 0``，
    两条路径的区别在界面上看不见、在账上看得见。
    """
    repo = repo_with_rows([(1, 1.0 + 0.5 + 0.25)])
    assert await repo.tree_cost_cents([1, 2]) == {"1": 1.75, "2": 0.0}


async def test_tree_cost_cents_asks_nothing_for_an_empty_batch(repo_with_rows):
    repo = repo_with_rows([])
    assert await repo.tree_cost_cents([]) == {}
    assert await repo.tree_cost_cents([None]) == {}  # type: ignore[list-item]


async def test_tree_cost_cents_rounds_like_the_other_money_reads(repo_with_rows):
    """4 位小数，与 ``_own_cost_sum_stmt`` 那一族一致 —— 同一笔钱在两个面上必须
    逐字节相同，否则「气泡说 0.1235、议题说 0.12346」会被当成两笔账。"""
    repo = repo_with_rows([(1, 0.123456789)])
    assert await repo.tree_cost_cents([1]) == {"1": 0.1235}


async def test_tree_cost_cents_a_null_sum_is_zero_not_none(repo_with_rows):
    """整棵树都是历史行（``own_cost_cents`` 从没算过）时 ``SUM`` 给 NULL。
    透传 None 会让消费方的 ``float()`` 抛在渲染路径上。"""
    repo = repo_with_rows([(1, None)])
    assert await repo.tree_cost_cents([1]) == {"1": 0.0}


async def test_tree_cost_cents_only_answers_for_roots(repo_with_rows):
    """已知边界，钉成决定而不是意外：**只许拿 root 来问。**

    一条子 run 的树键是它的根，不是它自己，所以它的行进不了以它自己为键的那一组 ——
    问它会拿到 0.0。那句话的意思是「这个 id 不是任何一棵树的根」，**不是**「它没花
    钱」。两个宿主（``/runs/costs`` 与 ``done`` 帧）问的都是 root，所以生产上碰不到；
    这条用例存在是为了让第三个消费方接上来时看见这个前提，而不是读出一个 0。

    同族：``run_ids_in_trees`` 那里问中间节点只拿回它自己。
    """
    # root 1 那棵树的合计回来了；子 run 11 自己不是树键，所以一行都没有。
    repo = repo_with_rows([(1, 1.75)])
    assert await repo.tree_cost_cents([1, 11]) == {"1": 1.75, "11": 0.0}


async def test_tree_cost_cents_raises_instead_of_reporting_a_free_tree():
    """读失败一律 raise，同 ``run_ids_in_trees``。

    降级成 ``{root: 0.0}`` 会把一次读故障说成「这次回合免费」—— 那是一个断言，不是
    一个缺口，而 ``/runs/costs`` 正靠这个异常答 503。
    """
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(_Raising())):
        with pytest.raises(RuntimeError):
            await AgentRunsRepository().tree_cost_cents([1])


async def test_efficiency_groups_cost_is_tree_sum_joined_to_root_group(captured_stmt):
    """效率页的花费：按树预聚合，再 join 回 root 行所在的分组。

    直接对分组里的行求 ``own_cost_cents`` 会改掉归属口径 —— 父用 A 模型、子用 B
    模型时，钱会跟着各自的行进各自的组，而这张表的既有语义是「整棵树的钱进 root
    那一组」（路由层的单价 null 逻辑正是为这个语义写的）。所以：子查询按树求和，
    外层仍按 root 行的 key 分组。
    """
    to = datetime.now(timezone.utc)
    s = _sql(
        await captured_stmt(
            lambda repo: repo.efficiency_groups(
                group_by="agent", frm=to - timedelta(days=30), to=to, team_id=7
            )
        )
    )
    low = s.lower()

    assert "own_cost_cents" in s
    # 旧列彻底不出现（这条语句里 cost_cents 只作为输出别名 AS cost_cents 存在）。
    assert "sum(public.agent_runs.cost_cents)" not in s
    assert "cost_cents).filter" not in s.replace("own_cost_cents", "")
    # 树键出现在子查询里。
    assert _TREE_KEY in low
    assert "LEFT OUTER JOIN" in s and "tree_cost" in s


async def test_efficiency_subquery_carries_the_same_scope_as_the_outer_query(
    captured_stmt,
):
    """子查询与主查询**同窗同 scope**，这是这次改动最容易出错的一处。

    子查询宽了：窗口外 / 别的团队的子 run 的钱会顺着 join 漏进这一屏的合计（跨租户
    金额泄漏，不只是数字不准）。子查询窄了：窗口内的委派花费又整个不见，正是本票要
    修的那个缺口。所以两边共用同一个 ``scope`` 列表，而不是各写一份 where。
    """
    to = datetime.now(timezone.utc)
    s = _sql(
        await captured_stmt(
            lambda repo: repo.efficiency_groups(
                group_by="model",
                frm=to - timedelta(days=30),
                to=to,
                team_id=7,
                project_id=9,
            )
        )
    )

    # 每个 scope 谓词都出现两次：一次在子查询，一次在主查询。
    for fragment in (
        "agent_runs.team_id =",
        "agent_runs.project_id =",
        "agent_runs.created_at >=",
        "agent_runs.created_at <",
    ):
        assert s.count(fragment) == 2, f"{fragment} 没有在两侧各出现一次：\n{s}"


async def test_efficiency_join_cannot_fan_out_the_counters(captured_stmt):
    """join 回来的是**按 root 分好组**的子查询，每个 root 至多一行 —— 所以
    ``run_count`` / ``tool_calls`` 这些回合粒度的计数不会被 join 放大。

    钉住这一点的是子查询自己带 ``GROUP BY``：少了它，join 就变成一对多，这张表上
    每个计数都会悄悄翻倍而没有任何报错。
    """
    to = datetime.now(timezone.utc)
    s = _sql(
        await captured_stmt(
            lambda repo: repo.efficiency_groups(
                group_by="model",
                frm=to - timedelta(days=1),
                to=to,
                user_id=None,
                team_id=7,
            )
        )
    )
    # 两个 GROUP BY：子查询按树键，主查询按分组键。
    assert s.count("GROUP BY") == 2
    assert "GROUP BY coalesce(public.agent_runs.root_run_id, public.agent_runs.id)" in s
