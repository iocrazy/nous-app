"""A1：小时表记的是**自身**花费。父 + 两子 + 一张图的 fixture。

子 run 自己那一行就是它 —— 父行再加一遍，跨 run 求和就双计，而
ai_usage_hourly 没有 parent_run_id 维度，事后剔不掉。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.runner import run_recorder as rr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Writer:
    """EventWriter 替身：只需 views 与 refold_external_slices。"""

    def __init__(self, views: dict[str, Any]) -> None:
        self.views = views

    async def refold_external_slices(self) -> None:
        return None


def _recorder(*, views, prompt=10, completion=20):
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = "900000000000001"
    rec.user_id, rec.agent_id = uuid4(), uuid4()
    rec.team_id, rec.project_id, rec.session_id = 42, None, None
    rec.model, rec.trigger, rec.attribution = (
        "doubao-seed-2-0-lite",
        "chat",
        "direct_human",
    )
    rec._prompt_tokens, rec._completion_tokens, rec._cached_input_tokens = (
        prompt,
        completion,
        0,
    )
    rec._skill_slugs_used, rec._output_summary = [], None
    rec._prompt_rate = rec._completion_rate = None
    rec._event_writer = _Writer(views)
    return rec


@pytest.fixture
def captured(monkeypatch):
    """拦下 record_usage 与 agent_runs 的 UPDATE，两边都记下来。"""
    calls: dict[str, list] = {"usage": [], "update": []}

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    @asynccontextmanager
    async def _write_scope():
        class _S:
            async def execute(self, stmt):
                calls["update"].append(stmt)

        yield _S()

    async def _record(**kwargs):
        calls["usage"].append(kwargs)

    monkeypatch.setattr(db_session, "write_scope", _write_scope)
    monkeypatch.setattr(ai_usage, "record_usage", _record)
    monkeypatch.setattr(
        "app.services.ai.billing.token_billing.reconcile_run", AsyncMock()
    )
    return calls


def _values(stmt) -> dict:
    """UPDATE 的 values 是 BindParameter，取出里面的字面量再比。"""
    return {k.name: getattr(v, "value", v) for k, v in stmt._values.items()}


async def test_the_children_own_rows_sum_to_the_root_column(captured):
    """own 10 + 两子 3+4 + 一张图 5：根 run 的列是 22（树总额），它的小时行必须
    是 15，而三行小时表加起来正好等于那个 22。"""
    for views in (
        {"cost": {"own_cents": 3.0, "by_child": {}, "media_cents": 0.0}},
        {"cost": {"own_cents": 4.0, "by_child": {}, "media_cents": 0.0}},
        {
            "cost": {
                "own_cents": 10.0,
                "by_child": {"c1": 3.0, "c2": 4.0},
                "media_cents": 5.0,
            }
        },
    ):
        await _recorder(views=views)._finish(status="completed")
    root = _values(captured["update"][-1])["cost_cents"]
    assert root == 22.0, "列仍是树总额（预算门禁靠它）"
    assert captured["usage"][-1]["cost_cents"] == 15.0
    assert sum(c["cost_cents"] for c in captured["usage"]) == root


async def test_the_counters_default_to_zero_then_follow_the_fold(captured):
    """efficiency fold（Part B Task 8）还没合时写 0 而不是炸；合了之后跟它走。"""
    await _recorder(views={"cost": {"own_cents": 1.0}})._finish(status="completed")
    kw = captured["usage"][0]
    assert (kw["run_count"], kw["failed_runs"]) == (1, 0)
    assert (kw["tool_calls"], kw["tool_errors"], kw["deliverables"]) == (0, 0, 0)

    await _recorder(
        views={
            "cost": {"own_cents": 1.0},
            "efficiency": {"tool_calls": 7, "tool_errors": 2, "deliverables": 3},
        }
    )._finish(status="completed")
    kw = captured["usage"][1]
    assert (kw["tool_calls"], kw["tool_errors"], kw["deliverables"]) == (7, 2, 3)


async def test_a_failed_run_counts_as_failed(captured):
    await _recorder(views={"cost": {"own_cents": 1.0}})._finish(
        status="failed", error_code="provider_error"
    )
    assert captured["usage"][0]["failed_runs"] == 1


async def test_a_media_only_run_still_reaches_the_hourly_table(captured):
    """零 token、只生了图的 run：旧的 token>0 守门把它整行丢掉，run_count 从第一
    天起就偏低。"""
    rec = _recorder(
        views={"cost": {"own_cents": None, "by_child": {}, "media_cents": 6.0}},
        prompt=0,
        completion=0,
    )
    await rec._finish(status="completed")
    assert captured["usage"], "media-only run 也要进小时表"
    assert captured["usage"][0]["cost_cents"] == 6.0
