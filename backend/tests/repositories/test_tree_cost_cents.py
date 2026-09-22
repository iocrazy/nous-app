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
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.agent_runs_repository import AgentRunsRepository

pytestmark = pytest.mark.unit

#: 树键**唯一**允许的拼法，照编译出来的样子写（SQLAlchemy 带 schema 前缀）。
#: 它的住处是 GROUP BY 与输出标签 —— 「同一棵树归成一行」这个语义。
_TREE_KEY = "coalesce(public.agent_runs.root_run_id, public.agent_runs.id)"

#: 收行那一侧的两条臂。与 ``COALESCE(root_run_id, id) IN (…)`` 选出的行完全相同，
#: 但表达式上的 ``COALESCE`` 让 planner 用不上 ``idx_agent_runs_root_tree``，只能
#: 整表扫 —— 所以 WHERE 侧一律写成这两条臂（同 ``run_ids_in_trees``）。
_ROOT_ARM = "public.agent_runs.root_run_id"
_ID_ARM = "public.agent_runs.id"

#: 「窗口 + scope 内的 root」那段 select 的住处。两条臂都引用它，所以它必须是个 CTE
#: 而不是被内联两遍的 ``Select``（见 ``test_efficiency_in_scope_roots_is_a_cte_…``）。
_CTE_HEAD = "WITH in_scope_roots AS ("

#: 两条臂**唯一**该长的样子：引用 CTE，而不是又一份内联 select。
_CTE_REF = "(SELECT in_scope_roots.id FROM in_scope_roots)"


def _membership(inner: str) -> str:
    """「这一行属于这批树」逐字应该长的样子。"""
    return f"{_ROOT_ARM} IN {inner} OR {_ID_ARM} IN {inner}"


def _sql(stmt: Any) -> str:
    return re.sub(r"\s+", " ", str(stmt.compile(dialect=postgresql.dialect())))


def _balanced(s: str, open_at: int) -> int:
    """``s[open_at]`` 那个 ``(`` 对应的 ``)`` 的下标。

    子查询里套子查询，所以不能用 ``str.index(")")`` —— 那会停在内层。
    """
    assert s[open_at] == "(", s[open_at:]
    depth = 0
    for i in range(open_at, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError(f"括号不配对：{s}")


def _tree_cost_region(sql: str) -> str:
    """``LEFT OUTER JOIN ( … )`` 里 ``tree_cost`` 那个派生表的完整文本。

    按区域断言而不是对整条 SQL 做 ``count()``：一个谓词在「子查询自己的 WHERE」里
    和在「子查询内层的 in-scope roots select」里，字符串上长得一模一样，含义却正好
    相反（前者按子行过滤=缺陷，后者按 root 选树=正确）。数次数分不出这两者。
    """
    j = sql.index("LEFT OUTER JOIN (")
    open_at = sql.index("(", j)
    return sql[j : _balanced(sql, open_at) + 1]


def _tree_cost_where(region: str) -> str:
    """``tree_cost`` 子查询**自己那一层**的 WHERE 全文（到它的 ``GROUP BY`` 为止）。

    区域内第一个 ``WHERE`` 就是它自己的（内层 ``IN (SELECT …)`` 的 WHERE 在其后、更深
    一层），最后一个 ``GROUP BY`` 是它自己的（内层那个 select 不分组）。
    """
    start = region.index(" WHERE ") + len(" WHERE ")
    end = region.rindex(" GROUP BY ")
    return region[start:end]


def _cte_body(sql: str) -> str:
    """``WITH in_scope_roots AS ( … )`` 括号里那段 select 的全文（不含外层括号）。

    scope 与 root 谓词从 ``tree_cost`` 的 WHERE 里搬到了这里 —— 语义没变（仍然只作用
    在「选哪些 root」上），住处变了，所以按 root 选树的那组断言改指这个区域。
    """
    k = sql.index(_CTE_HEAD)
    open_at = k + len(_CTE_HEAD) - 1
    return sql[open_at + 1 : _balanced(sql, open_at)]


def _inner_select(region: str) -> str:
    """``tree_cost`` 的 WHERE 里 ``IN ( … )`` 那个「在 scope 内的 root」引用。

    ⚠️ 锚点必须带列名（``root_run_id IN (SELECT``），**不能**是裸 ``"IN ("``：
    ``region`` 以 ``"LEFT OUTER JOIN ("`` 开头，而 ``"JOIN ("`` 里就含 ``"IN ("``
    这个子串（``index`` 命中偏移 13），于是 ``open_at`` 会退化成 JOIN 那个括号、整段
    ``tree_cost`` 被当成「内层 select」返回 —— 下面所有「outside 里不许有 scope 列」
    的负向断言就全部落在空串上，形同虚设。这条注释是修复轮次 2 的成因本身。

    两条臂各带一份**逐字相同**的 CTE 引用，取第一条那份即可（``_membership`` 会拿它
    去比另一条）。
    """
    k = region.index(f"{_ROOT_ARM} IN (SELECT")
    open_at = region.index("(", k + len(_ROOT_ARM))
    return region[open_at : _balanced(region, open_at) + 1]


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
    # 分组按树键。
    assert f"group by {_TREE_KEY}" in low
    # 收行按两条臂，问的仍是这几棵树、不是这几行 —— 但写成 planner 用得上
    # ``idx_agent_runs_root_tree`` 的形状。
    where = low.split(" where ")[1].split(" group by ")[0]
    assert where == (
        f"{_ROOT_ARM} in (__[postcompile_root_run_id_1]) "
        f"or {_ID_ARM} in (__[postcompile_id_1])"
    ), where
    # 树键**不许**出现在 WHERE 里：那正是让索引失效的那个写法。
    assert f"{_TREE_KEY} in (" not in low


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


async def test_efficiency_subquery_is_scoped_by_tree_membership_not_by_child_rows(
    captured_stmt,
):
    """子查询按**「属于某棵在 scope 内的树」**收行，不按每行自己的 scope 列（裁定 7）。

    这是本票最容易写错、且错了完全静默的一处。子 run 的 scope 列是 best-effort：
    ``team_of_run``（``app/services/ai/scope/scope_binding.py:203`）明说「A lookup
    failure degrades to ``None``」，3c A3 之前两个 spawn 站点更是直接写死
    ``team_id=None``；``DispatchScope`` 同理，子的 ``project_id`` 可以是 NULL 而它的
    root 有值。

    所以一旦按子行自己的 ``team_id`` / ``project_id`` / ``user_id`` 过滤：root 在
    scope 里、join 也落得下来，但那条子 run 的 ``own_cost_cents`` 压根没被加进
    ``tree_cost.cents`` —— 树总额**少掉委派那笔**，而这正是本票要捞回来的钱。没有任何
    报错，页面上就是一个小一点的数字。

    改成：先选出「窗口 + scope 内的 root」，再把树键落在这个集合里的**所有**行求和。
    root 才是被授权、被 scope 的那个对象，整棵树归属于它 —— 与 ``tree_cost_cents`` /
    ``tree_charge`` 同一套语义。
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
    region = _tree_cost_region(s)
    where = _tree_cost_where(region)
    inner = _inner_select(region)

    # ① 主断言，也是唯一咬得住「部分回归」的那条：``tree_cost`` 自己的 WHERE
    # **逐字等于**成员判定，前后什么都没有。
    #
    # 用相等而不是 ``in``：把 ``*scope`` 重新加回 ``.where()`` 而**保留**成员判定，
    # 任何 ``fragment in where`` 式的检查都照过（成员判定还在那儿），但那个查询已经
    # 退回按子行过滤了。相等是这里唯一分得清「只有成员判定」和「成员判定 + 别的」的
    # 写法。修复轮次 2 的成因就是这条当初写成了前缀匹配。
    assert where == _membership(inner), f"tree_cost 的 WHERE 不只是成员判定：\n{where}"

    # ② scope 与 root 谓词全在 ``in_scope_roots`` 这个 CTE 里 —— 两条臂只是引用它。
    assert inner == _CTE_REF, inner
    cte = _cte_body(s)
    for fragment in (
        "agent_runs.team_id =",
        "agent_runs.project_id =",
        "agent_runs.created_at >=",
        "agent_runs.created_at <",
        "agent_runs.parent_run_id IS NULL",
    ):
        assert fragment in cte, f"{fragment} 不在「in-scope roots」CTE 里"

    # ③ 把内层切掉之后，``tree_cost`` **自己那层**一个 scope 列都不许剩 —— 剩了就是
    # 按子行过滤，于是 team_id 为 NULL 的子 run 的钱被悄悄丢掉。
    # ①已经覆盖了这一点，③ 留着是因为它在失败时直接点名是哪一列。
    outside = where.replace(inner, "")
    assert outside == f"{_ROOT_ARM} IN  OR {_ID_ARM} IN ", outside
    for col in ("team_id", "project_id", "user_id", "created_at"):
        assert (
            col not in outside
        ), f"tree_cost 子查询在按自己的 {col} 过滤行：\n{region}"


async def test_efficiency_in_scope_roots_is_a_cte_rendered_once(captured_stmt):
    """「窗口 + scope 内的 root」只算一次 —— 它是 CTE，不是被内联两遍的 ``Select``。

    成员判定有两条臂（``root_run_id IN (…) OR id IN (…)``，见 ``_membership`` 上面的
    注释：换成 ``COALESCE(...) IN (…)`` 会让 planner 用不上 ``idx_agent_runs_root_tree``）。
    把同一个 ``Select`` 对象放进两条臂，SQLAlchemy 会把那段 SQL **原样渲染两遍**，PG
    于是把 ``agent_runs`` 按窗口 + scope 扫两次。非递归 CTE 被引用 >1 次时 PG 默认物化
    —— 一次扫描，两条臂共用。

    ⚠️ 这条断言的是**渲染出来的文本**，不是 EXPLAIN。「扫一次」是 PG 对物化 CTE 的行为，
    这里能钉住的是「我们只给了它一份 SQL」这个前提 —— 前提没了，后面那句话就无从谈起。
    """
    to = datetime.now(timezone.utc)
    s = _sql(
        await captured_stmt(
            lambda repo: repo.efficiency_groups(
                group_by="agent",
                frm=to - timedelta(days=30),
                to=to,
                team_id=7,
                project_id=9,
            )
        )
    )

    # ① 一个 CTE 定义，不多不少。
    assert s.count(_CTE_HEAD) == 1, s

    # ② 它的 body 在整条语句里**只出现一次**。这就是本次改动买到的东西；两条臂各内联
    # 一份时这里是 2。
    cte = _cte_body(s)
    assert (
        s.count(cte) == 1
    ), f"in-scope roots 的 select 被渲染了 {s.count(cte)} 遍：\n{cte}"

    # ③ 而且它确实是那段 select —— 窗口 + scope + root 谓词逐字都在，免得 ② 因为
    # 切错区域而在一段无关文本上「通过」。
    assert cte.endswith(
        "FROM public.agent_runs "
        "WHERE public.agent_runs.created_at >= %(created_at_1)s "
        "AND public.agent_runs.created_at < %(created_at_2)s "
        "AND public.agent_runs.team_id = %(team_id_1)s "
        "AND public.agent_runs.project_id = %(project_id_1)s "
        "AND public.agent_runs.parent_run_id IS NULL"
    ), cte

    # ④ 两条臂引用的都是这个 CTE，没有谁偷偷留了一份内联 select。
    assert s.count(f"IN {_CTE_REF}") == 2, s


async def test_efficiency_counts_a_child_whose_team_is_null(captured_stmt):
    """上一条的具体后果，写成它自己的一条用例。

    真实形状：root 属于 team 7，它委派出去的子 run ``team_id IS NULL``（lookup 失败
    降级，或 3c A3 之前留下的历史行）。子 run 的行必须进这棵树的合计。

    SQL 层面的证据就是「子查询不带 ``team_id`` 谓词」—— 带了，``NULL = 7`` 判 false，
    那条子 run 直接出局。行为层面的证明要真库（本文件全是编译断言，Task 7 / 控制器
    的真 PG 跑覆盖它）。
    """
    to = datetime.now(timezone.utc)
    s = _sql(
        await captured_stmt(
            lambda repo: repo.efficiency_groups(
                group_by="model", frm=to - timedelta(days=30), to=to, team_id=7
            )
        )
    )
    region = _tree_cost_region(s)
    where = _tree_cost_where(region)
    # ``tree_cost`` 自己的 WHERE 逐字只有成员判定 —— 没有任何地方能写下
    # ``team_id = 7``，所以 team_id 为 NULL 的那条子 run 不可能被 ``NULL = 7`` 判出局。
    assert where == _membership(_inner_select(region)), where
    assert "team_id" not in where.replace(_inner_select(region), "")
    # 求和的是全部成员行，没有任何行级筛选把它们挡在外面。
    assert "sum(coalesce(public.agent_runs.own_cost_cents" in region


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
