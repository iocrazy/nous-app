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

# 在 fixture 打桩之前绑住真身：event_count 的语义只在 record_usage 内部成立，
# 光看 _finish 传了什么是证不出来的。
from app.services.ai_usage import record_usage as _real_record_usage

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
    """拦下 record_usage、agent_runs 的 UPDATE，以及小时表自己的 upsert。"""
    calls: dict[str, list] = {"usage": [], "update": [], "hourly": []}

    import app.db.session as db_session
    import app.services.ai_usage as ai_usage

    def _scope_into(sink: list):
        @asynccontextmanager
        async def _cm():
            class _S:
                async def execute(self, stmt):
                    sink.append(stmt)

            yield _S()

        return _cm

    async def _record(**kwargs):
        calls["usage"].append(kwargs)

    monkeypatch.setattr(db_session, "write_scope", _scope_into(calls["update"]))
    monkeypatch.setattr(ai_usage, "record_usage", _record)
    # ai_usage 在模块顶层 `from app.db.session import write_scope`，所以上面那次
    # 打桩够不着它，必须单独按住。
    monkeypatch.setattr(ai_usage, "write_scope", _scope_into(calls["hourly"]))
    monkeypatch.setattr(
        "app.services.ai.billing.token_billing.reconcile_run", AsyncMock()
    )
    return calls


def _values(stmt) -> dict:
    """语句的 values 是 BindParameter，取出里面的字面量再比。"""
    return {k.name: getattr(v, "value", v) for k, v in stmt._values.items()}


def _run_row_updates(calls: dict[str, list]) -> list:
    """只要打在 agent_runs 上的那些 —— Task 13 的检索投影也走 write_scope。"""
    return [
        s for s in calls["update"] if getattr(s.table, "name", None) == "agent_runs"
    ]


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
    root = _values(_run_row_updates(captured)[-1])["cost_cents"]
    assert root == 22.0, "列仍是树总额（预算门禁靠它）"
    assert captured["usage"][-1]["cost_cents"] == 15.0
    assert sum(c["cost_cents"] for c in captured["usage"]) == root


async def test_the_points_charge_is_the_runs_own_spend_not_the_tree(
    captured, monkeypatch
):
    """A3：父 run 的 cost_cents 列是树总额（22），但积分只能扣它自己那 15 ——
    每个子 run 自己也会走到这条线扣它那份，父行再扣一遍就是对同一笔钱收两次。
    小时表（A1）与积分账（A3）读的必须是同一个口径。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    await _recorder(
        views={
            "cost": {
                "own_cents": 10.0,
                "by_child": {"c1": 3.0, "c2": 4.0},
                "media_cents": 5.0,
            }
        }
    )._finish(status="completed")
    assert _values(_run_row_updates(captured)[-1])["cost_cents"] == 22.0
    assert charged.await_args.kwargs["cost_points"] == 15.0


async def test_a_run_that_spent_nothing_itself_is_not_charged(captured, monkeypatch):
    """自身零花费、只有子 run 烧了钱 —— 守门按树总额开会让这个父 run 替子 run
    再付一次；按自身花费开则根本不进扣费分支。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {"c1": 3.0}, "media_cents": 0.0}}
    )._finish(status="completed")
    assert _values(_run_row_updates(captured)[-1])["cost_cents"] == 3.0
    charged.assert_not_awaited()


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


async def test_a_zero_token_failed_run_is_counted_but_is_not_an_event(captured):
    """预检即拒、provider 认证失败、预算门禁停机 —— 这三类零 token 零花费，旧守门
    把它们整行丢掉，于是 failed_runs 从第一天起偏低，而那正是效率账最该看见的
    一类。它必须进表；但它不是一次 LLM 事件，所以 event_count 不动。"""
    await _recorder(views={"cost": {}}, prompt=0, completion=0)._finish(
        status="failed", error_code="provider_auth"
    )
    assert captured["usage"], "零 token 零花费的失败 run 也要进小时表"
    kw = captured["usage"][0]
    assert (kw["run_count"], kw["failed_runs"]) == (1, 1)
    assert kw["cost_cents"] == 0.0

    # 同一份 kwargs 喂进真的 record_usage：event_count 的语义在它内部，
    # 光看 _finish 传了什么证不出来。
    await _real_record_usage(**kw)
    assert captured["hourly"], "upsert 没发出去，下面的断言会假绿"
    vals = _values(captured["hourly"][-1])
    assert vals["event_count"] == 0, "零 token 不是一次「有 token 的完成」"
    assert (vals["run_count"], vals["failed_runs"]) == (1, 1)
