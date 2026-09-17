"""``_finish`` 把 efficiency 视图写进 agent_runs 五列（3c §3.2）。与 ``cost_cents``
同一次 UPDATE：两者来自同一份折叠，分两次写就有一半落空的窗口。没有折叠数据时**不
写**这五列而不是写 0——「一次工具都没调」与「早于本期」必须分得开。"""

import contextlib
from uuid import uuid4

import pytest

from app.services.ai.runner.run_recorder import RunRecorder

pytestmark = pytest.mark.unit
COST = {"own_cents": 1.0, "by_child": {}, "media_cents": 0.0}


class _Writer:
    def __init__(self, views):
        self.views = views

    async def refold_external_slices(self):
        return None

    async def persist_views(self):
        # ``_finish`` 在把 run 标成终态之前调它 —— 树收口按行读落库的 cost 视图。
        return None


def _recorder(monkeypatch, captured, views):
    class _S:
        async def execute(self, stmt):
            # 只认 agent_runs 那一条。``_finish`` 之后还有 usage rollup 与计费对账，
            # 它们走同一个被 patch 的 write_scope；取最后一条会把被测语句盖掉。
            if str(stmt).lstrip().startswith("UPDATE public.agent_runs"):
                captured["params"] = dict(stmt.compile().params)
            return None

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr("app.db.session.write_scope", _ws)
    rec = RunRecorder(agent_id=uuid4(), user_id=uuid4(), trigger="chat")
    rec.run_id = "777"
    if views is not None:
        rec._event_writer = _Writer(views)
    return rec


async def test_finish_writes_all_five_counters(monkeypatch):
    captured: dict = {}
    rec = _recorder(
        monkeypatch,
        captured,
        {
            "cost": COST,
            "efficiency": {
                "steps": 3,
                "tool_calls": 7,
                "tool_errors": 2,
                "deliverables": 4,
                "turn_end_reason": "completed",
            },
        },
    )
    await rec._finish(status="completed")
    p = captured["params"]
    assert (p["steps"], p["tool_calls"], p["tool_errors"], p["deliverables"]) == (
        3,
        7,
        2,
        4,
    )
    assert p["turn_end_reason"] == "completed"


async def test_a_reasonless_run_writes_counters_but_no_reason(monkeypatch):
    """被 kill 的 run 没有 turn_end——四个计数照写，reason 留给 sweeper 补。"""
    captured: dict = {}
    rec = _recorder(
        monkeypatch,
        captured,
        {
            "cost": COST,
            "efficiency": {
                "steps": 2,
                "tool_calls": 1,
                "tool_errors": 0,
                "deliverables": 0,
                "turn_end_reason": None,
            },
        },
    )
    await rec._finish(status="failed")
    assert captured["params"]["steps"] == 2
    assert "turn_end_reason" not in captured["params"]


async def test_a_run_without_folded_views_leaves_the_columns_alone(monkeypatch):
    """NULL ≠ 0：这五列根本不在 SET 子句里。而 A1 票要把计数当整数相加，所以
    ``_efficiency_counts`` 在同一情况下必须给出可加的零，不该让它先判空。"""
    captured: dict = {}
    rec = _recorder(monkeypatch, captured, None)
    await rec._finish(status="failed", error_code="boom")
    assert "steps" not in captured["params"]
    assert rec._efficiency_counts() == {
        "steps": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "deliverables": 0,
        "turn_end_reason": None,
    }
