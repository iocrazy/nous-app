"""``charged_points_for_run_trees`` —— 消耗行显示的那个数（3c 终审 I2）。

扣费逐 run 发生，显示按 root 聚。两个宿主（议题线程的 rollup、聊天气泡的
``/ai-library/runs/costs``）与 ``done`` 帧共用这一个取数，否则同一次回合在三处
会说出三个数。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.services.billing.run_tree_points as mod
from app.services.billing.run_tree_points import charged_points_for_run_trees

pytestmark = pytest.mark.unit


class _Runs:
    def __init__(self, trees, boom=False):
        self._trees, self._boom = trees, boom

    async def run_ids_in_trees(self, root_ids):
        if self._boom:
            raise RuntimeError("db down")
        return {k: v for k, v in self._trees.items() if int(k) in root_ids}


class _Points:
    def __init__(self, charged, boom=False):
        self.asked, self._charged, self._boom = None, charged, boom

    async def charged_points_for_references(self, *, reference_type, reference_ids):
        if self._boom:
            raise RuntimeError("points down")
        self.asked = (reference_type, sorted(reference_ids))
        return {k: v for k, v in self._charged.items() if k in reference_ids}


def _wire(runs, points):
    return (
        patch.object(mod, "get_agent_runs_repository", lambda: runs),
        patch.object(mod, "get_points_repository", lambda: points),
    )


async def test_the_whole_tree_is_summed_onto_its_root():
    """真栈形状：一次回合 6 条流水，root 那条只有 −1。界面要说 6，不是 1。"""
    runs = _Runs({"700": ["700", "701", "702"]})
    points = _Points({"700": 1.0, "701": 3.0, "702": 2.0})
    a, b = _wire(runs, points)
    with a, b:
        out = await charged_points_for_run_trees([700])
    assert out == {"700": 6.0}


async def test_a_tree_with_no_consume_row_is_absent_not_zero():
    """「没扣过」与「扣了 0」是两个答案 —— 缺席让消费方读到 None 并说 Not charged。"""
    runs = _Runs({"700": ["700", "701"]})
    a, b = _wire(runs, _Points({}))
    with a, b:
        assert await charged_points_for_run_trees([700]) == {}


async def test_only_the_children_were_charged_still_reports_the_tree():
    """root 自己免费（BYOK / 零花费）而子 run 扣了钱 —— 正是 I2 最坏的那一格。"""
    runs = _Runs({"700": ["700", "701"]})
    a, b = _wire(runs, _Points({"701": 4.0}))
    with a, b:
        assert await charged_points_for_run_trees([700]) == {"700": 4.0}


async def test_trees_are_asked_in_one_round_trip():
    """一屏气泡 50 个 root 不该变成 50 次查询。"""
    runs = _Runs({"700": ["700", "701"], "800": ["800"]})
    points = _Points({"700": 1.0, "701": 2.0, "800": 5.0})
    a, b = _wire(runs, points)
    with a, b:
        out = await charged_points_for_run_trees([700, 800])
    assert out == {"700": 3.0, "800": 5.0}
    assert points.asked == ("agent_run", ["700", "701", "800"])


async def test_no_roots_asks_nothing():
    runs, points = _Runs({}), _Points({})
    a, b = _wire(runs, points)
    with a, b:
        assert await charged_points_for_run_trees([]) == {}
    assert points.asked is None


async def test_either_read_failing_travels_up():
    """两个读都 raise：消费方各自决定怎么降级（rollup 空掉一个字段、
    /runs/costs 转 503、状态帧回 null）。仓库层给不出一个能同时伺候三者的默认值。"""
    a, b = _wire(_Runs({}, boom=True), _Points({}))
    with a, b, pytest.raises(RuntimeError):
        await charged_points_for_run_trees([700])
    a, b = _wire(_Runs({"700": ["700"]}), _Points({}, boom=True))
    with a, b, pytest.raises(RuntimeError):
        await charged_points_for_run_trees([700])
