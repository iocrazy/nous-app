"""按 agent / 按模型 / 按天 的聚合改读 ``agent_runs.own_cost_cents``（3d 第 0 票 Task 4）。

一行的 ``cost_cents`` 是「自身 + 已报到的后代」—— 父行里已经折进了子 run 的钱。
所以任何**不按 root 过滤**的 ``SUM(cost_cents)`` 都把子 run 数了两遍：一遍在它自己
那行，一遍在父行那笔折叠额里。下面这几处聚合全都是这个形状：

- ``monthly_usage_by_agent`` → 月度预算扫描（``recompute_monthly_budgets_step``）
  与 Settings → AI Usage
- ``usage_by_agent_since`` → 画廊卡片的 ``cost_cents_7d``
- ``daily_usage_by_model`` / ``daily_usage`` → 用量趋势图
- ``list_groups_by_agent`` 的分组小计
- ``agent_cost_anomaly._findings_stmt`` 的逐小时基线
- ``admin_telemetry`` 的 overview / top_agents / top_users / daily_trend

``own_cost_cents`` 每行只记自身（own + media，不含后代），所以「按 agent（按模型、
按天）烧了多少」就是全部行的和 —— 父子不同 agent 时各记各的，这正是按 agent 限额
想要的口径。

**对外字段名一律不变**（``cost_cents``），前端与各消费方无感：改的是读哪一列，不是
回什么。所以下面的断言允许 ``AS cost_cents`` 这个输出别名留着，钉的是没有任何一处
**列引用**还指着旧列。

断言的是**编译出来的 SQL**：注释写「用了自身列」不会在漂移时报错，SQL 会。真库执行
见 Task 7。
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.unit


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _column_refs(sql: str) -> str:
    """编译后的 SQL 去掉自身列、输出别名与 ``--`` 注释后剩下的部分。

    剩下任何 ``cost_cents`` 都意味着还有一处在读旧列。豁免的两样都不是列引用：输出
    别名是对外字段名（本票的约束就是它不变），注释是写给人看的。
    """
    body = re.sub(r"--[^\n]*", "", sql)
    return body.replace("own_cost_cents", "").replace("AS cost_cents", "")


def _assert_reads_own_cost_only(sql: str) -> None:
    assert "own_cost_cents" in sql
    assert "cost_cents" not in _column_refs(sql)


class _Result:
    def __init__(self, rows: Any = ()) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> Any:
        return self._rows


class _CapturingSession:
    """记下每一条被执行的语句；返回值一律空结果集（本文件只看 SQL）。"""

    def __init__(self) -> None:
        self.stmts: list[Any] = []

    async def execute(self, stmt: Any, *_a: Any, **_k: Any) -> Any:
        self.stmts.append(stmt)
        return _Result()

    async def scalar(self, stmt: Any, *_a: Any, **_k: Any) -> Any:
        self.stmts.append(stmt)
        return 0


@contextlib.asynccontextmanager
async def _scope_of(session: _CapturingSession):
    yield session


@pytest.fixture
def captured_stmt(monkeypatch: pytest.MonkeyPatch):
    """跑一次真方法，交出它实际执行的最后一条语句。

    ``agent_runs_repository`` 在 import 时就把 ``read_scope`` 绑成了模块属性，
    只 patch ``app.db.session`` 碰不到它 —— 那样它会去连真库。
    """

    async def _run(call: Callable[[Any], Any]) -> Any:
        from app.repositories import agent_runs_repository as mod

        session = _CapturingSession()
        monkeypatch.setattr(mod, "read_scope", lambda: _scope_of(session))
        await call(mod.AgentRunsRepository())
        assert session.stmts, "方法一条语句都没执行 —— 大概率被 except 吞了"
        return session.stmts[-1]

    return _run


_MONTH_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
_MONTH_END = datetime(2026, 10, 1, tzinfo=timezone.utc)


async def test_monthly_usage_by_agent_projects_own_cost(captured_stmt):
    """月度预算扫描与 /usage 都按 agent 累加这一列；读 ``cost_cents`` 的话，一次
    Delegate 的花费会同时进子 agent 和父 agent 的桶，父那边还是折叠额。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.monthly_usage_by_agent(
                month_start=_MONTH_START, month_end=_MONTH_END
            )
        )
    )

    # 投影出来的键仍叫 cost_cents —— 两个消费方（router.get_usage /
    # recompute_monthly_budgets_step）按这个键读，本票不动它们。
    assert "own_cost_cents AS cost_cents" in s
    _assert_reads_own_cost_only(s)


async def test_usage_by_agent_since_sums_own_cost(captured_stmt):
    """画廊卡片的 ``cost_cents_7d`` —— 同一棵树跨两个 agent 时，旧列让父 agent 的
    七日花费把子 agent 那份又算一遍。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.usage_by_agent_since([uuid4()], _MONTH_START)
        )
    )

    assert "coalesce(sum(coalesce(public.agent_runs.own_cost_cents" in s.lower()
    _assert_reads_own_cost_only(s)


async def test_daily_usage_by_model_sums_own_cost(captured_stmt):
    """按模型的趋势图：父 run 的折叠额挂在**父那次调用的模型**上，子 run 用的可能是
    另一个模型 —— 双计之外还把钱记错了模型。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.daily_usage_by_model(started_after=_MONTH_START)
        )
    )

    assert "coalesce(sum(coalesce(public.agent_runs.own_cost_cents" in s.lower()
    _assert_reads_own_cost_only(s)


@pytest.mark.parametrize("group_by", ["model", "agent"])
async def test_daily_usage_sums_own_cost(captured_stmt, group_by: str):
    """用量页那张 hero 图，两个维度开关都走同一条语句。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.daily_usage(started_after=_MONTH_START, group_by=group_by)
        )
    )

    assert "coalesce(sum(coalesce(public.agent_runs.own_cost_cents" in s.lower()
    _assert_reads_own_cost_only(s)


async def test_list_groups_by_agent_sums_own_cost(captured_stmt):
    """Runs tab 的分组小计。这条是存量裸 SQL（本票只改列名，不新增裸 SQL）。"""
    s = _sql(
        await captured_stmt(
            lambda repo: repo.list_groups_by_agent(agent_id=uuid4(), user_id=uuid4())
        )
    )

    assert "SUM(COALESCE(own_cost_cents, 0))" in s
    assert "SUM(cost_cents)" not in s
    _assert_reads_own_cost_only(s)


def test_cost_anomaly_baseline_sums_own_cost():
    """逐小时 z-score 的基线。旧列会让一次带委派的运行在父 agent 上凭空拔高，
    告警于是指着错的 agent 叫。"""
    from app.workflows.agent_cost_anomaly import _findings_stmt

    s = _sql(_findings_stmt(24, 3.0, 50.0))

    assert "sum(coalesce(public.agent_runs.own_cost_cents" in s
    assert "cost_cents" not in _column_refs(s).replace("hour_cost_cents", "")


async def test_admin_telemetry_reads_own_cost(monkeypatch: pytest.MonkeyPatch):
    """admin 遥测的 overview / top_agents / top_users / daily_trend 全是对这一列
    做 Python 端求和 —— 读旧列等于每一格都双计。

    ``admin_telemetry`` 在函数体内 ``from app.db.session import read_scope``，
    所以 patch 的是 ``app.db.session`` 本身。
    """
    # ``app.api`` 的 __init__ 把同名 APIRouter 实例绑在包属性上，所以 ``import
    # app.api.ai_library_router as x`` 拿到的是那个 router 而不是模块 —— 只能走
    # import_module。
    import importlib

    import app.db.session as db_session

    router_mod = importlib.import_module("app.api.ai_library_router")

    session = _CapturingSession()
    monkeypatch.setattr(db_session, "read_scope", lambda: _scope_of(session))

    out = await router_mod.admin_telemetry(auth=object(), days=7)

    assert out["overview"]["total_runs"] == 0
    s = _sql(session.stmts[-1])
    # 下游 Python 按 cost_cents 这个键累加，所以别名留着、读的列换掉。
    assert "own_cost_cents AS cost_cents" in s
    _assert_reads_own_cost_only(s)
