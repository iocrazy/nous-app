"""``_finish`` 的幂等守卫必须罩住扣费，不只是罩住那条 UPDATE（3c 终审 M2）。

UPDATE 带 ``.where(status == 'running')``，但 rowcount 从来没被读过，下面的
``record_usage``（小时表）与 ``reconcile_run``（真扣积分）无条件执行。
``reconcile_run`` 自己的 docstring 写着「Repeated calls would double-charge」并把
幂等责任推回调用方 —— 而调用方并没有承担。窗口很窄（``_finish`` 跑两次，或清扫器
先把 status 翻了），但 A3 之后那是真钱。

rowcount **只有明确为 0 才算「没抢到」**：拿不到这个数（桩、某些驱动）不能读成
「别人已经收工了」而跳过计费 —— 那是用不知道换一次静默的少扣。
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Writer:
    """折叠视图的最小桩（形状照抄 test_run_recorder_efficiency_columns）——
    ``own_cents`` 非零才走得到 ``reconcile_run`` 那个分支。"""

    views = {
        "cost": {"own_cents": 5.0, "by_child": {}, "media_cents": 0.0},
        "efficiency": {
            "steps": 1,
            "tool_calls": 0,
            "tool_errors": 0,
            "deliverables": 0,
            "turn_end_reason": "completed",
        },
    }

    async def refold_external_slices(self):
        return None

    async def persist_views(self):
        # ``_finish`` 在把 run 标成终态之前调它 —— 树收口按行读落库的 cost 视图。
        return None


def _recorder(monkeypatch, rowcount):
    class _S:
        async def execute(self, stmt):
            if str(stmt).lstrip().startswith("UPDATE public.agent_runs"):
                return _Result(rowcount)
            return _Result(1)

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat", team_id=1)
    rec.run_id = "777"
    rec._event_writer = _Writer()
    return rec


async def _drive(monkeypatch, rowcount, status="completed"):
    rec = _recorder(monkeypatch, rowcount)
    usage, reconcile = AsyncMock(), AsyncMock()
    with (
        patch("app.services.ai_usage.record_usage", usage),
        patch("app.services.ai.billing.token_billing.reconcile_run", reconcile),
        patch("app.services.search.projection.project_run_best_effort", AsyncMock()),
    ):
        await rec._finish(status=status)
    return usage, reconcile


async def test_the_run_we_actually_closed_gets_billed(monkeypatch):
    usage, reconcile = await _drive(monkeypatch, rowcount=1)
    assert usage.await_count == 1
    assert reconcile.await_count == 1


async def test_losing_the_race_skips_both_the_rollup_and_the_charge(monkeypatch):
    """rowcount 0 = 别人已经把这条 run 收工了。再写一次小时表是重复计数，
    再对账一次是重复扣钱。"""
    usage, reconcile = await _drive(monkeypatch, rowcount=0)
    assert usage.await_count == 0
    assert reconcile.await_count == 0


async def test_an_unknown_rowcount_still_bills(monkeypatch):
    """拿不到 rowcount 不等于没抢到。少扣一次真钱比多写一行遥测严重得多，
    所以「不知道」这一侧必须按「抢到了」处理。"""
    usage, reconcile = await _drive(monkeypatch, rowcount=None)
    assert usage.await_count == 1
    assert reconcile.await_count == 1
