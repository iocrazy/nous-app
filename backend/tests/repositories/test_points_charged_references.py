"""``PointsRepository.charged_points_for_references`` (3c §3.3) —— 真扣掉的积分
来自 ``point_transactions`` 的 consume 流水，效率账**引用**积分账而不复制一份。

同 ``efficiency_for_issue``：整段包在 try/except 里返回 ``{}``，所以建语句时的任何
错误都会被读成「这些 run 都没扣过分」。这里真编译一次语句。
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.points_repository import PointsRepository

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, rows):
        self.stmts, self._rows = [], rows

    async def execute(self, stmt):
        self.stmts.append(stmt)
        rows = self._rows

        class _R:
            def all(self):
                return rows

        return _R()


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


async def test_consume_rows_come_back_positive_and_summed_per_reference():
    """``amount`` 在库里是负的（扣分）。同一个 run 可能有多行——重试、补扣——
    所以求和。取负而不是 abs()：正数是数据异常，不该被悄悄读成扣分。"""
    sess = _Session([("101", -21.0), ("102", -4.5)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101", "102", "103"]
        )
    assert out == {"101": 21.0, "102": 4.5}
    # 103 一次都没扣过 —— 它必须缺席，调用方读到 None 才能把「没扣」和「扣了 0」分开。
    assert "103" not in out

    sql = str(sess.stmts[0].compile(dialect=postgresql.dialect()))
    assert "sum(public.point_transactions.amount)" in sql
    assert "GROUP BY public.point_transactions.reference_id" in sql
    binds = dict(sess.stmts[0].compile().params)
    assert binds["type_1"] == "consume"
    assert binds["reference_type_1"] == "agent_run"


async def test_a_run_that_was_charged_zero_still_reports_zero():
    """扣了 0 是一个真值，它有一行流水。缺席的才是「没扣过」。"""
    sess = _Session([("101", 0.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert out == {"101": 0.0}


async def test_an_empty_id_list_asks_nothing():
    """``IN ()`` 是一次白跑的查询。没有 run 的议题每次轮询都不该打库。"""
    sess = _Session([])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=[]
        )
    assert out == {} and sess.stmts == []


async def test_a_failed_read_is_empty_not_an_exception():
    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    with patch("app.repositories.points_repository.read_scope", _boom):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert out == {}
