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

    async def _run() -> Any:
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
        await usage_repository.issue_totals(1)
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


async def test_rollup_sum_is_the_same_expression_as_the_gate(captured_stmt):
    """两处问的是同一个问题，必须是同一条表达式（共用模块级 helper）。比 SQL，
    不比注释 —— 注释不会在漂移时报错。"""
    gate = _sql(
        await captured_stmt(lambda repo: repo.spent_cents_for_issue(issue_id=1))
    )
    rollup = _sql(
        await captured_stmt(lambda repo: repo.own_cost_cents_for_issue_runs(1))
    )

    def _select_list(sql: str) -> str:
        return sql.split("\nFROM ")[0]

    assert _select_list(gate) == _select_list(rollup)
    assert "parent_run_id IS NULL" not in rollup


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
    """``compute_rollup`` 的 ``spent`` 来自仓库那条 SUM，而不是逐 root 行的
    ``cost_cents`` —— 所以传进去的行上写着 99 也不该影响结果。

    钉的是 ``load_rollup`` 这条真接线：只测纯函数的话，「忘了去问那条 SUM」会让
    预算格永远是 0 而单测全绿。
    """
    from unittest.mock import AsyncMock

    import app.repositories.agent_run_inbox_repository as inbox_mod
    import app.repositories.agent_runs_repository as runs_mod
    import app.repositories.issue_repository as issue_mod
    import app.repositories.points_repository as points_mod
    import app.services.issues.issue_rollup as rollup

    asked: list[int] = []

    class _Runs:
        async def list_for_issue(self, **_kw):
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

        async def own_cost_cents_for_issue_runs(self, issue_id):
            asked.append(issue_id)
            return 4.25

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
        {"id": 5, "status": "in_progress", "budget_cents": 100, "ai_session_id": None}
    )

    assert asked == [5]
    assert out["budget"]["spent_cents"] == 4.25
    assert out["budget"]["pct"] == 4
    # 每行那一格仍然展示这一行自己的 cost_cents —— 旧列是展示用的，没被取消。
    assert [r["cost_cents"] for r in out["runs"]] == [99.0, 99.0]
