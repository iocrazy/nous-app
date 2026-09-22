"""``_finish`` 的检索投影和计费走同一道幂等门（3d 批一 Task 3）。

那条终态 UPDATE 带 ``.where(status == 'running')``，所以一次 ``_finish`` 可能什么
都没改到 —— 清扫器 / liveness 先把行翻成 ``heartbeat_lost`` 并自己投影过了。小时表
与 ``reconcile_run`` 早就看 ``closed_by_us``，投影却是无条件跑的，**且用的是本次调用
的入参**：一次迟到的 ``_finish(status='completed')`` 在库里是 no-op，却把
``search_docs.status`` 盖成 ``completed``，检索面与 run 行从此说两个话。

``closed_by_us`` 的口径不变：**只有明确为 0 才算没抢到**。拿不到 rowcount（测试桩、
不报这个数的驱动）读作「别人已经收工了」就是拿「不知道」换一次静默的漏投影。
"""

from __future__ import annotations

import contextlib
from typing import Any, Optional
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, rowcount: Optional[int]) -> None:
        self.rowcount = rowcount


class _Writer:
    """折叠视图的最小桩（形状照抄 test_run_recorder_finish_idempotent）。"""

    views: dict[str, Any] = {
        "cost": {"own_cents": 5.0, "by_child": {}, "media_cents": 0.0},
        "efficiency": {
            "steps": 1,
            "tool_calls": 0,
            "tool_errors": 0,
            "deliverables": 0,
            "turn_end_reason": "completed",
        },
    }

    async def refold_external_slices(self, *, force: bool = False) -> None:
        return None

    async def persist_views(self) -> bool:
        return True


async def _drive(monkeypatch, rowcount: Optional[int]) -> AsyncMock:
    """跑一次 ``_finish(status='completed')``，把投影的桩交回调用方。"""

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
    projection = AsyncMock()
    with (
        patch("app.services.ai_usage.record_usage", AsyncMock()),
        patch("app.services.ai.billing.token_billing.reconcile_run", AsyncMock()),
        patch("app.services.ai.billing.tree_charge.settle_tree_if_closed", AsyncMock()),
        patch("app.services.search.projection.project_run_best_effort", projection),
    ):
        await rec._finish(status="completed")
    return projection


async def test_finish_does_not_project_when_someone_else_closed_the_row(monkeypatch):
    """rowcount 0 = 收口的是别人（清扫器已把行翻成 heartbeat_lost 并投影过）。
    按本次入参再投一次，检索面上这条 run 就变成 ``completed`` 了。"""
    projection = await _drive(monkeypatch, rowcount=0)
    projection.assert_not_awaited()


async def test_finish_projects_when_it_closed_the_row(monkeypatch):
    """抢到收口 —— 库里刚落的就是 ``completed``，投影跟着写同一个值。"""
    projection = await _drive(monkeypatch, rowcount=1)
    assert projection.await_count == 1
    assert projection.await_args.args[0]["status"] == "completed"


async def test_an_unknown_rowcount_still_projects(monkeypatch):
    """拿不到 rowcount 不等于没抢到。这一侧按「抢到了」处理，与计费同一口径 ——
    否则「不知道」会换来一次静默不更新的检索面。"""
    projection = await _drive(monkeypatch, rowcount=None)
    assert projection.await_count == 1
