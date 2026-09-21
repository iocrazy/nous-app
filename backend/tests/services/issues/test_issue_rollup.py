"""issue.rollup: phase from the runs, budget/children/inbox aggregation, origin registry."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.issues import origin_resolvers as orr
from app.services.issues.issue_rollup import compute_rollup, derive_phase

pytestmark = pytest.mark.unit
T0 = dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)


def _issue(**kw):
    base = dict(
        id=7,
        status="in_progress",
        paused_at=None,
        execution_state={"turn": 3},
        budget_cents=None,
        origin_kind="manual",
        origin_id=None,
    )
    base.update(kw)
    return base


def _run(status="completed", cents=10.0, view=None, cost=None, **kw):
    base = dict(
        id=1,
        status=status,
        started_at=T0,
        ended_at=None if status == "running" else T0,
        cost_cents=None if status == "running" else cents,
        model="m",
        error_code=None,
        metadata_json={"view": view or {}, "cost": cost or {}},
    )
    base.update(kw)
    return base


# ── phase priority: paused > waiting_input > running > blocked > done > idle ─


def test_phase_priority_is_paused_over_everything():
    runs = [_run("running", view={"phase": "waiting_input"})]
    assert derive_phase(_issue(paused_at=T0, status="done"), runs) == "paused"


def test_waiting_input_beats_running_from_three_sources():
    assert (
        derive_phase(_issue(execution_state={"awaiting_input": {}}), [_run("running")])
        == "waiting_input"
    )
    assert (
        derive_phase(_issue(execution_state={"agent_outcome": "needs_input"}), [])
        == "waiting_input"
    )
    assert (
        derive_phase(_issue(), [_run("running", view={"phase": "waiting_input"})])
        == "waiting_input"
    )
    assert (
        derive_phase(_issue(), [_run(view={"ended": {"reason": "awaiting_approval"}})])
        == "waiting_input"
    )


def test_running_comes_from_a_running_run_not_from_execution_state():
    # MH-1: execution_state says turn 7 but nothing is running → not "running"
    assert (
        derive_phase(_issue(execution_state={"turn": 7}), [_run("completed")]) == "idle"
    )
    assert derive_phase(_issue(execution_state={}), [_run("running")]) == "running"


def test_blocked_and_done_and_idle():
    assert derive_phase(_issue(status="blocked"), []) == "blocked"
    assert derive_phase(_issue(), [_run("heartbeat_lost")]) == "blocked"
    assert derive_phase(_issue(status="done"), [_run("failed")]) == "done"
    assert derive_phase(_issue(status="todo"), []) == "idle"


# ── aggregation ────────────────────────────────────────────────────────────


def test_rollup_puts_the_repo_spend_against_the_budget():
    """3d 第 0 票起 ``spent_cents`` 由调用方从 ``own_cost_cents_for_issue_runs``
    取（全部 run 求和，含 Delegate 子 run），不再是这里逐行加 ``cost_cents``。
    终审 I2 起每行那一格读 ``tree_cost_cents``——running 的行仍读自己的实时视图。"""
    runs = [
        _run(
            "running",
            id=3,
            cost={"spent_cents": 30.0},
            view={"phase": "running", "step": {"done": 1, "total": 4}},
        ),
        _run("completed", id=2, cents=50.0),
        _run("failed", id=1, cents=5.5),
    ]
    out = compute_rollup(
        _issue(budget_cents=100),
        runs,
        [],
        2,
        {"kind": "manual"},
        spent_cents=85.5,
        tree_cost_cents={"3": 0.0, "2": 50.0, "1": 5.5},
        now=T0,
    )
    assert out["phase"] == "running"
    assert out["budget"] == {
        "budget_cents": 100,
        "spent_cents": 85.5,
        "pct": 86,
        "state": "warn",
    }
    assert out["current_run"]["id"] == "3" and out["current_run"]["view"]["step"] == {
        "done": 1,
        "total": 4,
    }
    assert [r["id"] for r in out["runs"]] == ["3", "2", "1"]
    # running 的行读实时视图，不读树图里那个 0.0 —— 内存里的折算比最后一次镜像新，
    # 且与同一份 payload 的 current_run.cost 是同一个数（库里那一列每次镜像都写，
    # 不是空的，只是落后一拍）。
    assert out["runs"][0]["cost_cents"] == 30.0
    # 结束的两行读树总额。
    assert [r["cost_cents"] for r in out["runs"][1:]] == [50.0, 5.5]
    assert out["inbox_pending"] == 2 and out["computed_at"] == T0
    assert out["issue_id"] == "7"


def test_the_rows_add_up_to_the_budget_cell():
    """终审 I2：Budget 格是全行 Σ ``own_cost_cents``，行必须是同一批钱的另一种切法。

    行若还读折叠列 ``agent_runs.cost_cents``，两者就对不上账 —— 委派链上**没报回父
    行**的子 run 只进得了上面那个总额，一行都不落。真栈 T7 那棵树实测 ¢35.75 的格子
    下面躺着加起来 ¢15.50 的几行，同一个面上两个说法。

    这里：两棵树 20 + 15.75 = 35.75，而行上那个折叠列故意写成低报的 10 / 5.5。
    """
    runs = [_run("completed", id=2, cents=10.0), _run("completed", id=1, cents=5.5)]
    out = compute_rollup(
        _issue(budget_cents=100),
        runs,
        [],
        0,
        {"kind": "manual"},
        spent_cents=35.75,
        tree_cost_cents={"2": 20.0, "1": 15.75},
        now=T0,
    )
    assert [r["cost_cents"] for r in out["runs"]] == [20.0, 15.75]
    assert sum(r["cost_cents"] for r in out["runs"]) == out["budget"]["spent_cents"]


def test_a_root_missing_from_the_tree_map_falls_back_to_its_own_column():
    """读空不等于免费：树图里没有这个 root 时退回这一行自己那一列，而不是 0。

    低报（折叠列漏掉没报回的后代）总好过把一行的钱凭空归零 —— 后者是一个关于钱的
    断言，前者至少还是那一行确实记着的数。
    """
    out = compute_rollup(
        _issue(),
        [_run("completed", id=9, cents=7.25)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=7.25,
        tree_cost_cents={},
        now=T0,
    )
    assert out["runs"][0]["cost_cents"] == 7.25


def test_rollup_zero_budget_is_a_real_budget_not_unlimited():
    # Mirrors BudgetGateHook: 0 means "spend nothing more". Seen on MH-61
    # (2026-09-06): the hook recorded budget_check{halt, pct 100} while this
    # rollup said pct None / state ok for the very same run.
    out = compute_rollup(
        _issue(budget_cents=0),
        [_run("completed", cents=0.128)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=0.128,
        tree_cost_cents={"1": 0.128},
        now=T0,
    )
    assert out["budget"] == {
        "budget_cents": 0,
        "spent_cents": 0.128,
        "pct": 100,
        "state": "over",
    }
    # ...and nothing spent yet is 0 %, not "over" — the run has not started.
    out = compute_rollup(
        _issue(budget_cents=0),
        [],
        [],
        0,
        {"kind": "manual"},
        spent_cents=0.0,
        tree_cost_cents={},
        now=T0,
    )
    assert out["budget"]["pct"] == 0 and out["budget"]["state"] == "ok"


def test_rollup_without_budget_and_children_done_count():
    children = [
        {"id": 11, "identifier": "N-11", "title": "a", "status": "done"},
        {"id": 12, "identifier": "N-12", "title": "b", "status": "in_progress"},
    ]
    out = compute_rollup(
        _issue(),
        [],
        children,
        0,
        {"kind": "manual"},
        spent_cents=0.0,
        tree_cost_cents={},
        now=T0,
    )
    assert out["budget"] == {
        "budget_cents": None,
        "spent_cents": 0.0,
        "pct": None,
        "state": "ok",
    }
    assert out["sub_issues"]["total"] == 2 and out["sub_issues"]["done"] == 1
    assert out["sub_issues"]["items"][0]["id"] == "11"
    assert out["current_run"] is None and out["phase"] == "idle"


# ── origin registry ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_origin_default_registered_and_raising():
    issue = _issue(origin_kind="publish", origin_id="p-1")
    assert await orr.resolve_origin(issue) == {"kind": "publish", "origin_id": "p-1"}

    @orr.register("publish")
    async def _publish(row):
        return {"platform": "douyin"}

    try:
        assert await orr.resolve_origin(issue) == {
            "kind": "publish",
            "origin_id": "p-1",
            "platform": "douyin",
        }
        with pytest.raises(ValueError):
            orr.register("publish")(
                _publish
            )  # duplicate registration is a bug, not a merge
    finally:
        orr._unregister_for_tests("publish")

    @orr.register("publish")
    async def _boom(row):
        raise RuntimeError("side panel down")

    try:
        assert await orr.resolve_origin(issue) == {
            "kind": "publish",
            "origin_id": "p-1",
        }  # never 500
    finally:
        orr._unregister_for_tests("publish")
    assert await orr.resolve_origin({"origin_kind": None}) == {
        "kind": "manual",
        "origin_id": None,
    }
