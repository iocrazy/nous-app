"""``issue.rollup.efficiency``（3c §3.3）。计数按**全体** run 求和（root + children：
计数是每个 run 的自身量）；``cost_per_deliverable_cents`` 的分子是调用方传进来的
``spent_cents``，3d 第 0 票起同样是全体求和（``own_cost_cents`` 每行只记自身），分子
分母终于同一批 run。"""

import pytest

from app.services.issues.issue_rollup import compute_rollup

pytestmark = pytest.mark.unit
ISSUE = {"id": 5, "status": "in_progress", "budget_cents": None}
EFF = {
    "runs": 3,
    "steps": 12,
    "tool_calls": 20,
    "tool_errors": 3,
    "deliverables": 4,
    "avg_run_ms": 41000,
    "turn_end_reasons": {"completed": 2, "interrupted": 1},
}


def _run(run_id, cents):
    return {
        "id": run_id,
        "status": "completed",
        "started_at": None,
        "ended_at": None,
        "model": "doubao",
        "cost_cents": cents,
        "metadata_json": {},
    }


def test_efficiency_rides_through_with_cost_per_deliverable():
    out = compute_rollup(
        ISSUE,
        [_run(1, 20.0)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=20.0,
        efficiency=EFF,
    )
    assert out["efficiency"] == {**EFF, "cost_per_deliverable_cents": 5.0}


def test_an_issue_with_no_runs_yet_still_has_the_full_shape():
    """前端无条件读这八个键——缺席的字段会渲成空白格。0 件产出时「每件多少钱」是
    不知道，不是 0：0 会被读成「很便宜」。"""
    out = compute_rollup(ISSUE, [], [], 0, {"kind": "manual"}, spent_cents=0.0)
    assert out["efficiency"] == {
        "runs": 0,
        "steps": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "deliverables": 0,
        "avg_run_ms": None,
        "cost_per_deliverable_cents": None,
        "turn_end_reasons": {},
    }
    spent = compute_rollup(
        ISSUE,
        [_run(1, 20.0)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=20.0,
        efficiency={**EFF, "deliverables": 0},
    )
    assert spent["efficiency"]["cost_per_deliverable_cents"] is None


def test_charged_points_land_on_the_matching_run_only():
    """键是字符串 run id（wire 上每个 snowflake 都是 string）。没扣过的是 None——
    「这次没扣」（BYOK / 急停关闭 / 零花费）与「扣了 0」是两回事。"""
    out = compute_rollup(
        ISSUE,
        [_run(1, 20.0), _run(2, 5.0)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=25.0,
        charged_points={"1": 21.0},
    )
    assert [(r["id"], r["charged_points"]) for r in out["runs"]] == [
        ("1", 21.0),
        ("2", None),
    ]


def test_charged_points_of_zero_is_not_the_same_as_never_charged():
    """扣了 0 是一个真值，必须原样透出——被 ``or`` / falsy 判断吞成 None 的话，
    「这次真的一分没扣」就跟「压根没走积分链」混成一句话了。"""
    out = compute_rollup(
        ISSUE,
        [_run(1, 0.0)],
        [],
        0,
        {"kind": "manual"},
        spent_cents=0.0,
        charged_points={"1": 0.0},
    )
    assert out["runs"][0]["charged_points"] == 0.0


def test_the_empty_shape_is_not_shared_between_issues():
    """``EMPTY_EFFICIENCY`` 里的 ``turn_end_reasons`` 是个 dict。浅拷贝会把模块
    常量本身交给调用方，谁改一下就污染了全进程后续每一个议题。"""
    from app.services.issues.issue_rollup import EMPTY_EFFICIENCY

    a = compute_rollup(ISSUE, [], [], 0, {"kind": "manual"}, spent_cents=0.0)
    b = compute_rollup(ISSUE, [], [], 0, {"kind": "manual"}, spent_cents=0.0)
    a["efficiency"]["turn_end_reasons"]["completed"] = 1
    assert b["efficiency"]["turn_end_reasons"] == {}
    assert EMPTY_EFFICIENCY["turn_end_reasons"] == {}
