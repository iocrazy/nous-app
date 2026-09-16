"""``issue.rollup.efficiency``（3c §3.3）。计数按**全体** run 求和（root + children：
计数是自身量，不像花费那样父行已含子行）；``cost_per_deliverable_cents`` 的分子沿用
rollup 已有的 root-only 树总额。两个口径不同是故意的。"""

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
        ISSUE, [_run(1, 20.0)], [], 0, {"kind": "manual"}, efficiency=EFF
    )
    assert out["efficiency"] == {**EFF, "cost_per_deliverable_cents": 5.0}


def test_an_issue_with_no_runs_yet_still_has_the_full_shape():
    """前端无条件读这八个键——缺席的字段会渲成空白格。0 件产出时「每件多少钱」是
    不知道，不是 0：0 会被读成「很便宜」。"""
    out = compute_rollup(ISSUE, [], [], 0, {"kind": "manual"})
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
        charged_points={"1": 21.0},
    )
    assert [(r["id"], r["charged_points"]) for r in out["runs"]] == [
        ("1", 21.0),
        ("2", None),
    ]
