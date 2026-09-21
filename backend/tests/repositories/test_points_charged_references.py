"""``PointsRepository.charged_points_for_references`` (3c §3.3) —— 真扣掉的积分
来自 ``point_transactions`` 的 consume 流水，效率账**引用**积分账而不复制一份。

读失败**一律 raise**，降级交给消费方：``/ai-library/runs/costs`` 转 503（把一个真花了
钱的 run 显示成免费是最坏的答案），``issue_rollup.load_rollup`` 自己 catch 成 ``{}``（被
轮询的驾驶舱不该因为一个字段读不到就整块消失）。一个返回 ``{}`` 的仓库无法同时伺候这
两个，所以失败必须往上走。

建语句时的错误因此也会冒出去，而不是被读成「这些 run 都没扣过分」。这里真编译一次语句。
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.points_repository import PointsRepository

pytestmark = pytest.mark.unit


class _Session:
    """两条腿各一次 execute（consume 先、refund 后），所以桩按调用顺序发牌。

    ``refunds`` 省略即「没有退款行」—— 绝大多数用例的形状。
    """

    def __init__(self, rows, refunds=None):
        self.stmts = []
        self._legs = [rows, refunds or []]

    async def execute(self, stmt):
        self.stmts.append(stmt)
        rows = self._legs.pop(0) if self._legs else []

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


async def test_a_refund_is_deducted_from_what_the_reference_still_owes():
    """终审 I6。``refund`` 的 ``amount`` 是**正**的（mig 123 的 RPC 直接
    ``points_balance + p_amount``），所以净扣 = 扣 − 退。

    2026-09-17 那 81 棵被退款的树就卡在这里：钱退回去了，而议题线程 / 聊天气泡 /
    ``done`` 状态帧三处仍然显示 ``◇ n``。界面说扣了、账上没扣。"""
    sess = _Session([("101", -21.0), ("102", -4.5)], refunds=[("101", 6.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101", "102"]
        )
    assert out == {"101": 15.0, "102": 4.5}


async def test_a_fully_refunded_reference_reports_zero_not_the_old_amount():
    """全额退款后界面上该是 0，不是原来那个数。键仍在场 —— 「扣过、又退了」与
    「从没扣过」是两个答案。"""
    sess = _Session([("101", -21.0)], refunds=[("101", 21.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert out == {"101": 0.0}


async def test_an_over_refund_floors_at_zero_instead_of_going_negative():
    """退得比扣的多是数据异常，但界面上不该出现一个负的消耗。"""
    sess = _Session([("101", -5.0)], refunds=[("101", 9.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert out == {"101": 0.0}


async def test_a_refund_with_no_consume_does_not_invent_a_charged_key():
    """凭空造一个键会把「从没扣过」读成「扣了 0」—— 正是本方法用缺席/在场区分的
    那两件事。异常记日志，不改答案。"""
    sess = _Session([], refunds=[("101", 3.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert out == {}


async def test_each_leg_keeps_its_equality_on_type_so_the_partial_index_holds():
    """mig 474 的索引是 partial（``type='consume' AND reference_type='agent_run'``）。
    把两条腿合成 ``type IN (…)`` 会让谓词不再被蕴含，planner 退回全表扫描 —— 结果
    仍然正确，只有这条断言会说出来。"""
    sess = _Session([("101", -1.0)])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=["101"]
        )
    assert len(sess.stmts) == 2
    types = [dict(s.compile().params)["type_1"] for s in sess.stmts]
    assert types == ["consume", "refund"]
    for stmt in sess.stmts:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "point_transactions.type = %(type_1)s" in sql, sql


async def test_an_empty_id_list_asks_nothing():
    """``IN ()`` 是一次白跑的查询。没有 run 的议题每次轮询都不该打库。"""
    sess = _Session([])
    with patch("app.repositories.points_repository.read_scope", _scope(sess)):
        out = await PointsRepository().charged_points_for_references(
            reference_type="agent_run", reference_ids=[]
        )
    assert out == {} and sess.stmts == []


async def test_a_failed_read_raises_so_each_consumer_can_choose():
    """A billing read that answered ``{}`` would tell the cost bubble every run
    was free. The two readers want opposite things from a failure and only one
    of them can be served by a default, so the failure travels and each side
    decides (``load_rollup`` catches it; ``/runs/costs`` answers 503)."""

    @contextlib.asynccontextmanager
    async def _boom():
        raise RuntimeError("connection reset")
        yield  # pragma: no cover

    with patch("app.repositories.points_repository.read_scope", _boom):
        with pytest.raises(RuntimeError):
            await PointsRepository().charged_points_for_references(
                reference_type="agent_run", reference_ids=["101"]
            )
