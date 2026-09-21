"""议题一族的三个钱读面改读 ``agent_runs.own_cost_cents``（3d 第 0 票 Task 3）。

一条 run 的 ``cost_cents`` 是「自身 + 已报到的后代」，所以求和时必须按 root 过滤，
否则子 run 被数两遍。代价是：Delegate 出去的子 run **带着 issue_id 落库**，却因为
这条 root 过滤整个进不了预算门禁 —— 一次委派花掉的钱，议题预算完全无感。

``own_cost_cents`` 每行只记自身（own + media，不含后代），所以「这个议题真花了多少」
就是**全部行**的和，不需要也不许再按 root 过滤。三处必须同时改，否则同一笔花费在
Usage 面、驾驶舱 Budget 格、预算门禁上是三个数。

这里断言的是**编译出来的 SQL**（桩 session 捕获语句）：注释说「用了自身列」不会在
漂移时报错，SQL 会。真库执行见 Task 7。
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Callable

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Result:
    def __init__(self, value: Any) -> None:
        self._v = value

    def scalar_one(self) -> Any:
        return self._v

    def mappings(self) -> "_Result":
        return self

    def one(self) -> Any:
        return self._v


class _CapturingSession:
    def __init__(self, result: Any) -> None:
        self.stmts: list[Any] = []
        self._result = result

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> Any:
        self.stmts.append(stmt)
        return self._result


@contextlib.asynccontextmanager
async def _scope_of(session: _CapturingSession):
    yield session


@pytest.fixture
def captured_stmt(monkeypatch: pytest.MonkeyPatch):
    """跑一次真方法，交出它实际执行的那条语句。

    ``agent_runs_repository`` 在 import 时就把 ``read_scope`` 绑成了模块属性，
    只 patch ``app.db.session`` 碰不到它 —— 那样它会去连真库。
    """

    async def _run(call: Callable[[Any], Any]) -> Any:
        from app.repositories import agent_runs_repository as mod

        session = _CapturingSession(_Result(0))
        monkeypatch.setattr(mod, "read_scope", lambda: _scope_of(session))
        await call(mod.AgentRunsRepository())
        return session.stmts[-1]

    return _run


@pytest.fixture
def captured_stmt_usage(monkeypatch: pytest.MonkeyPatch):
    """同上，但 ``usage_repository.issue_totals`` 是在函数体内 import 的，
    所以要 patch ``app.db.session`` 本身。"""

    async def _run(**kw: Any) -> Any:
        import app.db.session as db_session
        from app.repositories import usage_repository

        row = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_cents": 0,
            "run_count": 0,
        }
        session = _CapturingSession(_Result(row))
        monkeypatch.setattr(db_session, "read_scope", lambda: _scope_of(session))
        await usage_repository.issue_totals(1, **kw)
        return session.stmts[-1]

    return _run


async def test_budget_gate_sums_own_cost_over_all_rows(captured_stmt):
    """门禁读自身列，且**不再**按 root 过滤 —— 这正是委派花费此前逃掉预算的那道口。"""
    s = _sql(await captured_stmt(lambda repo: repo.spent_cents_for_issue(issue_id=1)))

    assert "own_cost_cents" in s
    # 旧列一点都不许剩（去掉 own_ 前缀后整串里不该再有 cost_cents）。
    assert "cost_cents" not in s.replace("own_cost_cents", "")
    assert "parent_run_id IS NULL" not in s
    # NULL（从未算过）在求和里必须读成 0，不是让整列变 NULL。
    assert "coalesce(sum(coalesce(public.agent_runs.own_cost_cents" in s.lower()


async def test_budget_gate_still_excludes_the_live_run_on_request(captured_stmt):
    """``exclude_run_id`` 是门禁「我自己的花费我自己记」的那一半，去掉 root 过滤
    不该顺手把它也弄丢 —— 丢了就是把当前 run 的钱算两遍。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.spent_cents_for_issue(issue_id=1, exclude_run_id=9)
        )
    )
    assert "agent_runs.id !=" in s


async def test_rollup_sum_is_the_same_statement_as_the_gate(captured_stmt):
    """两处问的是同一个问题，必须是**整条语句**相同 —— SELECT 侧（同一条 SUM
    表达式）和 WHERE 侧（同一组 OR 键）都要比。

    只比 SELECT 侧是不够的：``own_cost_cents_for_issue_runs`` 曾经只按 ``issue_id``
    过滤，而门禁是 ``or_(issue_id, conversation_id)`` —— 一条只走会话键的 run 于是
    进得了门禁总额、进不了 Budget 格，两个面对同一个议题说两个数，而两边的 docstring
    都写着口径一致。比 SQL，不比注释 —— 注释不会在漂移时报错。
    """
    gate = _sql(
        await captured_stmt(
            lambda repo: repo.spent_cents_for_issue(issue_id=1, conversation_id=2)
        )
    )
    rollup = _sql(
        await captured_stmt(lambda repo: repo.own_cost_cents_for_issue_runs(1, 2))
    )

    assert gate == rollup
    assert "conversation_id" in rollup
    assert "parent_run_id IS NULL" not in rollup


async def test_rollup_sum_takes_the_conversation_key_from_the_caller(captured_stmt):
    """不传会话键时只按 issue_id 找（``load_rollup`` 的 issue 没有 session 时就是
    这条），传了就两个键都在。"""
    without = _sql(
        await captured_stmt(lambda repo: repo.own_cost_cents_for_issue_runs(1))
    )
    assert "conversation_id" not in without
    assert "issue_id" in without


async def test_rollup_sum_raises_when_the_read_fails(monkeypatch):
    """与门禁同一条纪律：读失败不许被读成「没花钱」。"""
    from app.repositories import agent_runs_repository as mod

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover — unreachable, keeps this a generator

    monkeypatch.setattr(mod, "read_scope", _boom)
    with pytest.raises(RuntimeError):
        await mod.AgentRunsRepository().own_cost_cents_for_issue_runs(1)


async def test_issue_totals_cost_no_longer_root_filtered(captured_stmt_usage):
    """钱那一列改读自身列并去掉 FILTER；``run_count`` 仍然只数 root
    （「这个议题跑了几次」问的是顶层运行数，不是树上有多少个节点）。"""
    s = _sql(await captured_stmt_usage())

    assert "sum(coalesce(public.agent_runs.own_cost_cents" in s.lower()
    assert (
        "count(*) FILTER (WHERE public.agent_runs.parent_run_id IS NULL)" in s
    ), "run_count 必须仍然只数 root"
    # 钱那一列不许再挂 FILTER。整串里只剩 run_count 那一个 root 谓词。
    assert s.count("parent_run_id IS NULL") == 1
    # token 三列从来就不带 root 过滤，WHERE 里也不许爬进来。
    assert "parent_run_id IS NULL" not in s.split("\nFROM ")[1]


async def test_rollup_uses_repo_sum_not_row_cost(monkeypatch):
    """Budget 格与它下面那几行都不读 ``agent_runs.cost_cents``（行上写着 99 也影响
    不了任何一个数）：格子来自 ``own_cost_cents_for_issue_runs``，行来自
    ``tree_cost_cents``。

    钉的是 ``load_rollup`` 这条真接线：只测纯函数的话，「忘了去问那两条」会让预算格
    永远是 0、每行退回折叠列，而单测全绿。
    """
    from unittest.mock import AsyncMock

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    import app.repositories.points_repository as points_mod
    import app.services.issues.issue_rollup as rollup

    asked: list[tuple[int, int | None]] = []
    listed: list[tuple[int, int | None]] = []
    trees: list[list[int]] = []

    class _Runs:
        async def list_for_issue(self, *, issue_id, conversation_id=None):
            listed.append((issue_id, conversation_id))
            return [
                {
                    "id": 776,
                    "status": "completed",
                    "started_at": None,
                    "model": "m",
                    "cost_cents": 99.0,
                },
                {
                    "id": 775,
                    "status": "completed",
                    "started_at": None,
                    "model": "m",
                    "cost_cents": 99.0,
                },
            ]

        async def own_cost_cents_for_issue_runs(self, issue_id, conversation_id=None):
            asked.append((issue_id, conversation_id))
            return 4.25

        async def tree_cost_cents(self, root_ids):
            trees.append(list(root_ids))
            # 两棵树加起来正好是上面那个议题总额 —— 真库里本来就该对得上。
            return {"776": 2.0, "775": 2.25}

        async def efficiency_for_issue(self, _issue_id):
            return {}

        async def last_transcript_seq(self, run_id):  # pragma: no cover — no live run
            return None

    class _Inbox:
        async def pending_count(self, **_kw):
            return 0

    class _Points:
        async def charged_points_for_references(self, **_kw):
            return {}

    monkeypatch.setattr(runs_mod, "get_agent_runs_repository", lambda: _Runs())
    monkeypatch.setattr(inbox_mod, "get_agent_run_inbox_repository", lambda: _Inbox())
    monkeypatch.setattr(points_mod, "get_points_repository", lambda: _Points())
    monkeypatch.setattr(
        issue_mod.issue_repository, "list_children", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(rollup, "resolve_origin", AsyncMock(return_value={}))

    out = await rollup.load_rollup(
        {"id": 5, "status": "in_progress", "budget_cents": 100, "ai_session_id": 88}
    )

    # 行与钱必须用同一组键去问 —— 会话键掉了的话，只走会话键的 run 进得了列表却
    # 进不了 Budget 格。
    assert asked == [(5, 88)] and listed == [(5, 88)]
    assert out["budget"]["spent_cents"] == 4.25
    assert out["budget"]["pct"] == 4
    # 每行那一格是**这一行那棵树**的总额（终审 I2），不是行上那个折叠列的 99。
    assert trees == [[776, 775]]
    assert [r["cost_cents"] for r in out["runs"]] == [2.0, 2.25]
    # 格子与它下面的行对得上账 —— 这正是换成树总额要买的东西。
    assert sum(r["cost_cents"] for r in out["runs"]) == out["budget"]["spent_cents"]


def _where(sql: str) -> str:
    """语句最外层那个 WHERE 的全文。

    从右边切：``issue_totals`` 的 SELECT 列表里有 ``FILTER (WHERE …)``，从左边切会
    切进那个 filter 里。这两条语句都没有子查询，所以最后一个 WHERE 就是外层那个。
    编译出来的 SQL 带换行，先压成一行再切。
    """
    return re.sub(r"\s+", " ", sql).rsplit(" WHERE ", 1)[1]


async def test_issue_totals_uses_the_same_or_keys_as_the_gate(
    captured_stmt, captured_stmt_usage
):
    """Usage 面的议题总额与预算门禁必须用**同一组键**找行，不只是同一条 SUM。

    只按 ``issue_id`` 找会漏掉只经 session 的 conversation 挂上来的 run —— 那条 run
    进得了门禁总额与驾驶舱 Budget 格，却不进 Usage 面，而三处 docstring 都写着口径
    一致（``_issue_scope_keys`` 就是为了不让这三处各写一份）。
    """
    totals = _where(_sql(await captured_stmt_usage(conversation_id=2)))
    gate = _where(
        _sql(
            await captured_stmt(
                lambda repo: repo.spent_cents_for_issue(issue_id=1, conversation_id=2)
            )
        )
    )
    assert totals == gate
    assert "conversation_id" in totals


async def test_issue_totals_without_a_session_key_asks_by_issue_alone(
    captured_stmt_usage,
):
    """调用方没有会话键时只按 ``issue_id`` 找 —— 不是编一个。"""
    s = _sql(await captured_stmt_usage())
    assert "conversation_id" not in s
    assert "agent_runs.issue_id =" in s


async def test_issue_totals_refuses_to_sum_the_whole_table():
    """两个键都没有 → WHERE 空掉 → 这条 SUM 变成全库花费。宁可抛，也不给一个
    看着像数的错答案（同 ``_own_cost_sum_stmt`` 那道守卫）。"""
    from app.repositories import usage_repository

    with pytest.raises(ValueError):
        await usage_repository.issue_totals(None)


def test_the_shared_sum_refuses_to_run_without_a_predicate():
    """``_own_cost_sum_stmt()`` 不带谓词拼出来的是整张 ``agent_runs`` 的 own 花费
    之和 —— 所有用户、所有议题的一个数字，被当成某一个议题的花费喂给预算门禁。

    这不是假想的手滑：``_issue_scope_keys`` 在两个键都为 None 时正好返回空列表，
    一次空参数调用就能走到这里。
    """
    from app.repositories.agent_runs_repository import _own_cost_sum_stmt

    with pytest.raises(ValueError):
        _own_cost_sum_stmt()
