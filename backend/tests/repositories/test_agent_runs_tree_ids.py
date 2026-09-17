"""``AgentRunsRepository.run_ids_in_trees`` —— 一个 root run 的整棵树有哪些 run id。

3c 终审 I2：气泡与议题线程的「◇ n」此前只取 root 自己那一条 consume 流水，而扣费
是**逐 run** 发生的（``token_billing`` 对每条 run 各 ceil 一次）。真栈实测一次回合
产生 6 条流水、余额 −6，界面显示 ◇ 1.00。取数要走整棵树，这是它的取数口径。

``agent_runs.root_run_id`` 在子 run 上指向根、在 root 行上是 NULL（``_attach_to_parent_run``
是唯一写方），所以一条 ``root_run_id IN (:roots) OR id IN (:roots)`` 就够。
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
            def all(self):
                return rows

        return _R()


class _Raising:
    async def execute(self, stmt):
        raise RuntimeError("db down")


def _scope(sess):
    @contextlib.asynccontextmanager
    async def _cm():
        yield sess

    return _cm


async def test_a_root_gets_itself_plus_every_descendant():
    # (id, root_run_id)：root 行的 root_run_id 是 NULL，子行指回根。
    sess = _Session([(700, None), (701, 700), (702, 700)])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().run_ids_in_trees([700])
    assert out == {"700": ["700", "701", "702"]}


async def test_two_trees_do_not_bleed_into_each_other():
    """一次批量里的每个 root 各自成树 —— 否则一屏气泡的钱会互相串。"""
    sess = _Session([(700, None), (701, 700), (800, None), (801, 800)])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().run_ids_in_trees([700, 800])
    assert out == {"700": ["700", "701"], "800": ["800", "801"]}


async def test_a_root_with_no_row_still_maps_to_itself():
    """查不到行不等于这个 id 不存在扣分流水。每个问到的 id 至少含它自己 ——
    否则一次读空会把一个真扣过钱的 run 显示成免费。"""
    sess = _Session([])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().run_ids_in_trees([700])
    assert out == {"700": ["700"]}


async def test_a_mid_tree_run_reports_only_itself():
    """问一个**子** run 时只回它自己：孙子的 ``root_run_id`` 指向真正的根，不指向它。
    这是已知的取数边界（气泡与议题线程问的都是 root），写下来免得被当成 bug。"""
    sess = _Session([(701, 700)])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        out = await AgentRunsRepository().run_ids_in_trees([701])
    assert out == {"701": ["701"]}


async def test_no_roots_means_no_query():
    sess = _Session([(700, None)])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        assert await AgentRunsRepository().run_ids_in_trees([]) == {}
    assert sess.stmts == []


async def test_the_statement_asks_both_ways_round():
    sess = _Session([])
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(sess)):
        await AgentRunsRepository().run_ids_in_trees([700])
    sql = str(sess.stmts[0].compile(dialect=postgresql.dialect()))
    assert "root_run_id IN" in sql and "agent_runs.id IN" in sql


async def test_a_read_failure_raises():
    """降级成「只有 root」等于悄悄把 I2 那个低报又装回去。让它冒出去，三个消费方
    各自已经有 catch（rollup 空掉一个字段、/runs/costs 转 503、状态帧回 null）。"""
    with patch("app.repositories.agent_runs_repository.read_scope", _scope(_Raising())):
        with pytest.raises(RuntimeError):
            await AgentRunsRepository().run_ids_in_trees([700])
