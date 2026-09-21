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

    async def refold_external_slices(self, *, force: bool = False) -> None:
        return None

    async def persist_views(self) -> bool:
        # ``_finish`` 在把 run 标成终态之前调它 —— 树收口按行读落库的 cost 视图。
        # 返回 True = 「视图真的落库了」。返回 None 的桩会让 ``_finish`` 判定镜像
        # 失败并**跳过收口**（终审 I1），于是每条断言收口的用例都红在桩上。
        return True


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

                    # 真驱动会报 rowcount。``closed_by_us`` 只看「是不是明确为
                    # 0」，所以这跟原来的「什么都不返回」等价；而树收口的 CAS
                    # 反过来要求**明确等于 1** 才扣，不报就一分不扣。
                    class _R:
                        rowcount = 1

                    return _R()

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
    """只要**终态那一条** UPDATE。

    打在 agent_runs 上的写不止一条：Task 13 的检索投影走 write_scope，树收口的
    ``charged_at`` CAS 也是一条 agent_runs 的 UPDATE。终态那条是唯一带
    ``status`` 的，按它认。
    """
    return [
        s
        for s in calls["update"]
        if getattr(s.table, "name", None) == "agent_runs"
        and "status" in {k.name for k in s._values}
    ]


def _tree_read(monkeypatch, rows: list[dict]) -> None:
    """把 ``read_scope`` 换成树收口那两条读：先「我的 root 是谁」，再全树。

    ``rows`` 是每条 run 的 cost 视图。收口按**行**聚合，所以这里要把子 run 各自
    摆出来 —— 它不读父行的 ``by_child``（workforce 链上 root 那份恒为空）。
    """
    from datetime import timedelta as _timedelta
    from uuid import uuid4 as _uuid4

    class _Row:
        def __init__(self, idx, cost):
            self.id = 800000000000000 + idx
            self.status = "completed"
            self.team_id, self.user_id = 42, _uuid4()
            self.model = "doubao-seed-2-0-lite"
            self.prompt_tokens, self.completion_tokens = 10, 20
            # 晚于切换点：切换点之前开始的树按用户裁定「只向前不追扣」永不收口
            # （``tree_charge.cutover_at``）。从配置推而不是写 ``now()`` —— 后者
            # 会让这些用例依赖跑测试的机器此刻已经过了那一刻。
            from app.services.ai.billing.tree_charge import cutover_at

            base = cutover_at()
            assert base is not None
            self.started_at = base + _timedelta(seconds=1)
            self.ended_at = None
            self.metadata_json = {"cost": cost}

    built = [_Row(i, c) for i, c in enumerate(rows)]

    class _Res:
        def __init__(self, r):
            self._r = r

        def first(self):
            return self._r[0] if self._r else None

        def all(self):
            return self._r

    class _S:
        def __init__(self):
            self._n = 0

        async def execute(self, stmt):
            self._n += 1
            # 第一条：我的 root_run_id（None = 我就是 root）；第二条：全树；
            # 第三条：防回溯正查「这棵树在 point_transactions 里扣过钱没有」——
            # 新树是零行，所以这里必须给空，给 built 就变成 legacy_charged。
            if self._n == 1:
                return _Res([(None,)])
            return _Res(built) if self._n == 2 else _Res([])

    @asynccontextmanager
    async def _read():
        yield _S()

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "read_scope", _read)


def _settle_call(charged):
    """收口那一次 —— 它是唯一 ``log_usage=False`` 的那条。"""
    return next(
        c for c in charged.await_args_list if c.kwargs.get("log_usage") is False
    )


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


async def test_the_points_charge_is_the_whole_tree_once_at_the_closer(
    captured, monkeypatch
):
    """用户裁定（2026-09-17）：一个回合的积分 = ceil(整棵树的平台花费)，一棵树只
    扣一次，扣的人是**把树收口的那一条 run**。此前这条用例断言的是「按自身花费
    15 扣」——那是每条 run 各 ceil 一次的口径，一次带委派的回合因此在
    point_transactions 里留下好几行、每行各向上取整（真栈 ≈¢0.92 收成 7 分）。

    ⚠️ 也不是「root 定稿时扣」（计划原文）：workforce 的委派是 fire-and-forget，
    root 通常**先于**子 run 结束，而 subagent_done 只写到直接父 —— root 的
    by_child 对那条链恒为空，那个方案会退化回逐 run ceil。

    这条 run 自己那一步只写审计行（15，它自身花费），扣费由收口那一次做（22，
    按全树逐行聚合）。小时表（A1）仍收 15：那张表没有 parent_run_id 维度，父行带
    上子 run 的花费就再也剔不掉。**三套账口径不同是对的** —— 分别回答「这条 run
    烧了多少」「这个回合该收多少钱」「这条 run 干了多少活」。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    _tree_read(
        monkeypatch,
        [
            {"own_cents": 10.0, "media_cents": 5.0},  # 这条 run 自己
            {"own_cents": 3.0},  # 子 run c1
            {"own_cents": 4.0},  # 子 run c2
        ],
    )
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
    assert captured["usage"][-1]["cost_cents"] == 15.0

    audit = charged.await_args_list[0].kwargs
    assert audit["usage_cost_points"] == 15.0 and audit["cost_points"] == 0.0

    settle = _settle_call(charged).kwargs
    assert settle["cost_points"] == 22.0


async def test_a_run_that_spent_nothing_itself_still_closes_the_tree(
    captured, monkeypatch
):
    """自身零花费、只有子 run 烧了钱：**不写审计行**（那张表按自身用量记，多写一
    行会让 overall_run_count 凭空上抬），但收口照叫 —— 这条 run 可能正是树里最后
    一个结束的，而整棵树的 3 分要有人收。此前这条断言的是「根本不进扣费分支」，
    那在「每条 run 各扣各的」口径下才成立。"""
    charged = AsyncMock()
    monkeypatch.setattr("app.services.ai.billing.token_billing.reconcile_run", charged)
    _tree_read(monkeypatch, [{"own_cents": 0.0}, {"own_cents": 3.0}])
    await _recorder(
        views={"cost": {"own_cents": 0.0, "by_child": {"c1": 3.0}, "media_cents": 0.0}}
    )._finish(status="completed")
    assert _values(_run_row_updates(captured)[-1])["cost_cents"] == 3.0
    assert [c.kwargs["cost_points"] for c in charged.await_args_list] == [3.0]
    assert _settle_call(charged).kwargs["cost_points"] == 3.0


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


async def test_a_non_numeric_media_cents_reads_as_zero_not_as_a_coerced_string(
    captured,
):
    """``own_media_cents`` 现在取 ``spend_of_run(...).total``，于是非数字读作 0。

    以前这里是手算的 ``float((folded or {}).get("media_cents") or 0.0)`` ——
    字符串 ``"6.0"`` 会被强转成 6.0，而 ``"abc"`` 直接抛 ValueError。
    ``tree_charge._num`` 的约定是「非数字（含 bool、字符串、None）一律 0.0」。

    这不是回归，是**把两个消费方钉到同一个值上**：``agent_runs.own_cost_cents``
    列（mig 479）写的就是 ``own_spend.total``。两边若各算各的，就会出现「行上
    写着 0、小时表记着 6」这种谁也说不清的分歧。
    """
    rec = _recorder(
        views={"cost": {"own_cents": None, "by_child": {}, "media_cents": "6.0"}},
        prompt=0,
        completion=0,
    )
    await rec._finish(status="completed")
    assert captured["usage"], "这一行仍然要进小时表"
    assert captured["usage"][0]["cost_cents"] == 0.0


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
