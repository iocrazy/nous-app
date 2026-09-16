"""``AgentRunsRepository.efficiency_for_issue`` (3c §3.3) —— 语句真能编译成
Postgres 接受的 SQL，分组行真能折成总量。

这条测试存在的理由：整个方法包在 ``try/except`` 里返回 ``{}``，所以**任何**建语句
时的错误（比如把 ``FILTER`` 挂到 ``extract`` 而不是 ``sum`` 上，SQLAlchemy 直接
AttributeError）都会被读成「这个议题没有效率数据」，而不是被读成故障。只断言
``compute_rollup`` 的形状是抓不到的——那一侧永远绿。
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.agent_runs_repository import AgentRunsRepository

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, rows):
        self.stmts, self._rows = [], rows

    async def execute(self, stmt):
        self.stmts.append(stmt)
        rows = self._rows

        class _R:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


def _row(reason, runs, steps, calls, errors, delivered, timed, seconds):
    return {
        "reason": reason,
        "runs": runs,
        "steps": steps,
        "tool_calls": calls,
        "tool_errors": errors,
        "deliverables": delivered,
        "timed_runs": timed,
        "total_seconds": seconds,
    }


async def test_efficiency_sql_compiles_and_folds_the_groups_into_totals():
    rows = [
        _row("completed", 2, 8, 14, 1, 3, 2, 60.0),
        _row("interrupted", 1, 4, 6, 2, 1, 1, 20.0),
    ]
    sess = _Session(rows)
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().efficiency_for_issue(5)

    assert out == {
        "runs": 3,
        "steps": 12,
        "tool_calls": 20,
        "tool_errors": 3,
        "deliverables": 4,
        # 80 秒 / 3 条计时的 run
        "avg_run_ms": 26666,
        "turn_end_reasons": {"completed": 2, "interrupted": 1},
    }

    # 真编译一次：FILTER 只对聚合合法，挂错位置在这里就炸，而不是在生产静默变 {}。
    sql = str(sess.stmts[0].compile(dialect=postgresql.dialect()))
    assert "count(*) FILTER (WHERE" in sql
    assert "sum(EXTRACT(epoch FROM" in sql and ")) FILTER (WHERE" in sql
    assert "GROUP BY public.agent_runs.turn_end_reason" in sql
    # 计数是每个 run 的自身量，父行不含子行 —— 这里绝不能有 root 过滤。
    assert "parent_run_id" not in sql
    assert dict(sess.stmts[0].compile().params)["issue_id_1"] == 5


async def test_an_issue_whose_runs_never_finished_has_no_average_rather_than_zero():
    """``avg_run_ms`` 的分母是两端时间戳都有的 run。一条都没有 → None。
    0 毫秒会被读成「快得不得了」。"""
    sess = _Session([_row(None, 2, 5, 7, 0, 0, 0, 0.0)])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().efficiency_for_issue(7)
    assert out["avg_run_ms"] is None
    assert out["runs"] == 2
    # reason 为 NULL 的分组计进总量，但不进分布——空字符串键没有含义。
    assert out["turn_end_reasons"] == {}


async def test_a_failed_read_is_empty_not_an_exception():
    """驾驶舱少两个格子，好过把整个议题页拖垮。"""

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    with patch("app.repositories.agent_runs_repository.read_scope", _boom):
        assert await AgentRunsRepository().efficiency_for_issue(1) == {}
